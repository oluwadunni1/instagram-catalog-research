"""
Stage 2 triage on Jev, as a BINARY decision: product listing, or not.

Why binary rather than the five-way enum stage2_triage.py uses: nothing
downstream consumes the other four categories. Every functional consumer tests
only `!= "product_listing"` - stage5_reconcile.py routes non-listings to
auto_exclude, stage6_sync.py marks them excluded, run_pipeline.py gates Stage 3
on it. The five-way value survives only as display text. The brief's own
success criterion is "Product-vs-not-product triage precision >= 90%", which
was binary all along.

Binary also makes Stage 2 measurable. The golden set splits 59 product /
12 not-product with both classes populated, where the five-way has three
classes at n <= 2 that cannot be scored at all.

CAVEAT that must travel with any comparison from this module: Jev chooses
between 2 options while the Gemini stage chooses between 5, so part of any Jev
advantage is the smaller option set rather than the model. This is not a
like-for-like result.

Returns the same `post_type` vocabulary the rest of the pipeline already
tests against, so nothing downstream needs to change: "product_listing", or
"not_product" for everything else.

IMAGES. Jev was text-only when this module was written, which is why
stage2_triage_hybrid.py hands every caption-less post to OCR and then Gemini
vision. It now accepts images (docs.typesafe.ai/images, still labelled
"dreamer-ready"), so image_policy lets Jev look at the photo itself:
  - "never": text only, the original behaviour and the default.
  - "escalate": the cascade, all on Jev. A caption-less post goes straight to
    the image; a captioned one goes to text first and to the image only when
    confidence is under escalate_below.
  - "always": every post with an image is judged on caption and image in one
    call. An image costs ~2.8k input tokens (~$0.0001 at Jev's rate), so
    the cascade's saving may not be worth the second call.
"""

from __future__ import annotations

import logging

from pipeline.jev_client import DEFAULT_JEV_MODEL, decide, image_asset, noul_probability
from pipeline.stages.stage2_triage import _is_uninformative_caption
from pipeline.types import Post, vision_image_url

logger = logging.getLogger(__name__)

QUESTION_ID = "is_product_listing"

# Default decision boundary. 0.5 is the neutral starting point, NOT a tuned
# value - sweeping it belongs in the calibration work, on the tune half only.
DEFAULT_THRESHOLD = 0.5

# The operative test from eval/LABEL_CODEBOOK.md, which reconstructs the rule
# the golden set's labels actually follow. Keep the two aligned: the codebook
# is the spec, this is one consumer of it.
#
# The two clauses after the question are the ones that decide the hard cases,
# and both were learned the expensive way. Writing "promotional implies advert"
# into a rubric made ad_creative a magnet that took 7 of 11 predictions in the
# first probes.
INSTRUCTIONS = (
    "Is a specific product being offered for sale in this post, right now? "
    "Answer yes only if a buyer could ask to purchase a specific item today. "
    "Pre-orders count when a real offer is attached, and a stated price is NOT "
    "required - 'DM for price' is still an offer. "
    "Answer no for posts that inform customers without offering a specific item "
    "(opening hours, location, a branch move, a clearance sale, a financing "
    "offer, a recurring discount day), for posts about a sale that already "
    "completed such as a delivery or handover, for polls and engagement posts, "
    "and for personal or lifestyle content. "
    "Two rules decide most hard cases: being promotional or enthusiastic does "
    "not by itself make a post an offer, and a post that reads like a listing "
    "but offers nothing purchasable yet - an unreleased model, for example - "
    "is not an offer."
)

# Appended for a call that carries the photo. The text rubric stays as it was,
# so a text-only run reproduces the round-1 configs exactly.
IMAGE_INSTRUCTIONS = (
    INSTRUCTIONS + " "
    "The post's photo is in `image`; judge the caption and the photo together. "
    "Vendors often write the product and its price on the photo itself, and a "
    "post with no caption can still be an offer."
)

IMAGE_POLICIES = ("never", "escalate", "always")

# Same value, and the same tune-half provenance, as the hybrid's trigger.
DEFAULT_ESCALATE_BELOW = 0.80

TEXT_STAGE_NAME = "stage2_triage_jev"
IMAGE_STAGE_NAME = "stage2_triage_jev_image"


def _ask(post: Post, profile: dict | None, jev_model: str, threshold: float,
         run_id: str | None, vendor_id: str | None, image_url: str | None) -> dict:
    """One Jev noul on a post, with its photo when image_url is given."""
    caption = (post.get("caption") or "").strip()
    post_id = post.get("post_id") or post.get("id") or ""

    # An object state rather than a bare string, so the profile travels with
    # the caption the way it does in the Gemini prompt.
    state: dict = {"caption": caption}
    if profile:
        state["account_profile"] = profile
    assets = None
    if image_url:
        reference, asset = image_asset(image_url)
        state["image"] = reference
        assets = {reference: asset}

    body = decide(
        state=state,
        questions={
            QUESTION_ID: {"type": "noul",
                          "instructions": IMAGE_INSTRUCTIONS if image_url else INSTRUCTIONS},
        },
        jev_model=jev_model,
        stage_name=IMAGE_STAGE_NAME if image_url else TEXT_STAGE_NAME,
        run_id=run_id,
        vendor_id=vendor_id,
        post_id=post_id,
        assets=assets,
    )

    p_product = noul_probability(body, QUESTION_ID)
    is_product = p_product >= threshold

    # A noul returns P(yes) and no separate confidence field, unlike choice and
    # score. Distance from the decision boundary is the honest analogue: 0.95
    # and 0.05 are both confident answers, 0.51 is not.
    confidence = max(p_product, 1.0 - p_product)

    logger.debug("[stage2_triage_jev] %s: p_product=%.3f -> %s (conf=%.3f, image=%s)",
                  post_id, p_product, "product_listing" if is_product else "not_product",
                  confidence, bool(image_url))

    return {
        "post_type": "product_listing" if is_product else "not_product",
        "confidence": confidence,
        "escalated": False,
        "p_product": p_product,
    }


def triage_post_jev(
    post: Post,
    profile: dict | None = None,
    jev_model: str = DEFAULT_JEV_MODEL,
    threshold: float = DEFAULT_THRESHOLD,
    run_id: str | None = None,
    vendor_id: str | None = None,
    image_policy: str = "never",
    escalate_below: float = DEFAULT_ESCALATE_BELOW,
) -> dict:
    """Classifies one post as product_listing or not, via Jev nouls.

    Args:
        post: Post dict with at least "caption".
        profile: Stage 1's AccountProfile as a dict. Passed into the state
            because the Gemini stage gets it too - withholding it here would
            repeat the failure that invalidated every figure before
            2026-09-09, where the harness and the chained path fed the same
            stage different inputs.
        jev_model: deliberately NOT named `model`. load_stage_fn() reserves and
            strips that key, so a parameter called `model` silently never
            receives its configured value - the bug that ran a four-model
            Stage 4 comparison on one model.
        threshold: P(yes) at or above this is product_listing.
        run_id: token usage is logged only when set.
        image_policy: "never", "escalate" or "always" - see the module
            docstring. A post with no image is judged on text under all three.
        escalate_below: under "escalate", the text confidence below which the
            post is asked again with its photo. Unused by the other policies.

    Returns:
        {"post_type": "product_listing" | "not_product",
         "confidence": float, "escalated": bool, "p_product": float}

        `escalated` is True only when an "escalate" run sent the photo, so the
        harness's Pass A / Pass B split means what it means for the Gemini
        stage. Under "always" there is no escalation: one call, with the image.
    """
    if image_policy not in IMAGE_POLICIES:
        raise ValueError(f"image_policy must be one of {IMAGE_POLICIES}, got {image_policy!r}")
    caption = (post.get("caption") or "").strip()
    image_url = vision_image_url(post)
    ask = dict(profile=profile, jev_model=jev_model, threshold=threshold,
               run_id=run_id, vendor_id=vendor_id)

    if image_policy == "always" and image_url:
        return _ask(post, image_url=image_url, **ask)
    if image_policy == "escalate" and image_url:
        # Nothing to read: a text call on an empty caption is a guaranteed
        # waste, so go straight to the photo.
        if _is_uninformative_caption(caption):
            return {**_ask(post, image_url=image_url, **ask), "escalated": True}
        text = _ask(post, image_url=None, **ask)
        if text["confidence"] >= escalate_below:
            return text
        logger.info("[stage2_triage_jev] %s: confidence %.2f < %.2f - asking again with the image",
                     post.get("post_id") or post.get("id") or "", text["confidence"], escalate_below)
        return {**_ask(post, image_url=image_url, **ask), "escalated": True}
    return _ask(post, image_url=None, **ask)
