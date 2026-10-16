# r2_jev: Stage 2 on Jev, now that Jev can see images

**Round 2, 2026-10-16.** Stage 2 only (`--only-stage 2`), binary product vs not-product. Four
configs:

| Config | What it does |
|---|---|
| [`r2_jev_text`](../pipeline/config/experiments/r2_jev_text.yaml) | Jev on the caption alone, the round-1 call |
| [`r2_jev_cascade`](../pipeline/config/experiments/r2_jev_cascade.yaml) | Jev on the caption, then Jev again **with the photo** when unsure or when there's no caption |
| [`r2_jev_image`](../pipeline/config/experiments/r2_jev_image.yaml) | Jev on caption and photo together, one call for every post |
| [`r2_jev_hybrid_gemini`](../pipeline/config/experiments/r2_jev_hybrid_gemini.yaml) | Round 1's hybrid: Jev, then OCR, then Gemini, with Gemini through OpenRouter |

Jev calls go straight to TypeSafe with `TYPESAFE_API_KEY`, never through OpenRouter.
`jev-latest` resolved to `jev-1.13.0` on every call.

**Verdict:** Jev no longer needs Gemini for Stage 2.
- The all-Jev cascade scores **97% on autos and 98% on gadgets**, against the hybrid's 100% / 98%.
  It costs about the same ($0.0016–0.0018 per vendor) and needs no OCR.
- Seeing the photo fixed all 5 caption-less gadgets posts that text-only Jev got wrong, and both
  of its autos misses. On one autos post it went the other way: text-only Jev had it right and the
  photo flipped it.
- The cascade makes **no false positives**, as before: it never called a non-listing a listing.
- It's about 15× cheaper than Gemini's own Stage 2 in the [control](r2_control_gemini.md).

## How Jev takes images

TypeSafe added images to Jev 1.13 and 1.14 (docs.typesafe.ai/images). Its model page still says
text only; the Images page is newer.
- **It's opt-in, and labelled "dreamer-ready", not production-ready.** The request format,
  answers and limits may change between releases.
- Each request carrying an image needs the `TypeSafe-Danger-Zone-Images: true` header. Images go
  as base64 in an `assets` map and are referenced from `state`.
- Each side must be 280–1568 px. Outside that the request is rejected, not resized.
  `jev_client.prepare_image()` resizes and pads to fit.
- An image is billed as input tokens at Jev's $0.042 per million: about 2.4–2.9k tokens, or
  roughly $0.0001, per Instagram photo. Output is free.

The code is in `pipeline/jev_client.py` (`image_asset()`, `prepare_image()`, `decide(assets=)`)
and `pipeline/stages/stage2_triage_jev.py` (`image_policy`: `never`, `escalate` or `always`).
`escalate_below` is 0.80, the hybrid's value, which was tuned on autos only.

## Results

Product vs not-product, every run gated. Precision was 100% on every run.

| | autos (30 posts) | gadgets (41 posts) | Cost, autos + gadgets |
|---|---|---|---|
| Jev text | 28/30 = 93% (recall 92%) | 34/41 = 83% (recall 79%) | $0.0020 |
| **Jev cascade** | **29/30 = 97%** (recall 96%) | **40/41 = 98%** (recall 97%) | **$0.0034** |
| Jev image, every post | 29/30 = 97% (recall 96%) | **not validated**, see below | $0.0030 autos |
| Jev + Gemini hybrid | 30/30 = 100% | 40/41 = 98% (recall 97%) | $0.0041 listed |
| [Gemini control](r2_control_gemini.md), 5-class | 100% | 100% | ~$0.053, Stage 2 alone |
| *Round 1, Jev text (September)* | *28/30 = 93%* | *33/41 = 80%* | |

Run ids:

| Config | autos | gadgets |
|---|---|---|
| Jev text | `r2_jev_text_20261016T083005Z` | `…T083045Z` |
| Jev cascade | `r2_jev_cascade_20261016T084411Z` | `…T083823Z` |
| Jev image | `r2_jev_image_20261016T083943Z` | not validated |
| Hybrid | `r2_jev_hybrid_gemini_20261016T084033Z` | `…T084120Z` |

**Where the photo helped:**
- **The 5 caption-less gadgets posts:** `17875545636631606`, `18085616054264180`,
  `18457546087186979`, `17940067572092567` and `18351809149247806`.
  - Text-only Jev called all five not-a-product, at confidence 0.79–0.83, so it was wrong and
    fairly sure.
  - With the photo the cascade got all five right. The hybrid also gets them right, but only
    through a committed OCR cache (`runs/vendor_gadgets_01/ocr/`). RapidOCR isn't installed on
    Windows, so a new caption-less post there would go to Gemini vision.
- **Autos:** the cascade sent 6 low-confidence posts to the photo and got 5 of them right,
  including both text-only misses (`17975827368107390`, `18073956491710644`).

**What's left:**
- **Autos `18029319446669897`:** the cascade's one miss, and one the photo caused. On text
  alone Jev called it a listing at 0.78, which is right but under the 0.80 trigger. With the photo
  it fell to 0.52 on the wrong side. The hybrid's Gemini text got it right; MiMo read it as an
  announcement. The image-only run missed it too, at 0.55.
- **Gadgets `18117403781512663`:** missed by both the cascade and the hybrid. Its text confidence
  was 0.80–0.82, right at the 0.80 trigger, so it never escalated.
  - Jev's answer moved by 0.02 between runs on the same input, so a post this close to the
    trigger can escalate on one run and not the next.

## Per stage

| Call | Median latency (max) | Input tokens / call |
|---|---|---|
| Jev text | 0.9–1.0 s (1.9 s) | ~700 |
| Jev with photo | 1.6–2.2 s (5.2 s) | 2,400–2,900 |
| Gemini text (hybrid) | 1.3 s (2.1 s) | ~750 |

- **The cascade sent 17–20% of posts to the photo.** The hybrid sent the same share to OCR or
  Gemini.
- **Image-only costs about twice the cascade** on autos ($0.0030 against $0.0016), for the same
  29/30. At these prices the gap is $0.0014 per 30 posts, so cost doesn't decide between them.
  What does is that image-only depends on every image link working (next section).

## The runs that failed the gate

- **Image-only, gadgets** (`r2_jev_image_20261016T084104Z`): 2 posts errored, `18149430643539602`
  and `17983552128060866`.
  - These are the two gadgets posts whose Instagram image links have expired (the CDN returns
    403), and Graph can't refresh them.
  - A policy that needs every image can't be scored on this golden set. The cascade never needed
    those two images, because their captions were clear.
- **Cascade, autos, first attempt** (`r2_jev_cascade_20261016T083140Z`): 4 posts errored when
  `api.typesafe.ai` stopped resolving on this machine for a few minutes. That was a network
  failure, not a Jev one. The rerun passed.

## Caveats

- **Jev picks between 2 options where Gemini's Stage 2 picks between 5,** so part of any Jev
  advantage is the smaller option set. Only the binary metric is compared.
- **One run per vendor; there's no noise floor yet** (`r2-noise`). Jev's answers moved by up to
  0.02 between runs on the same post.
- **Images are a beta feature.** TypeSafe says accuracy and format may change between releases,
  so the four configs are pinned to `jev-1.13.0` (2026-10-16), the version every run here used.
- **`escalate_below` 0.80 came from the hybrid's tuning on autos**; it wasn't retuned for the
  photo tier. Gadgets is still the held-out half.
- **Jev cost is estimated**, tokens × $0.042/M. TypeSafe doesn't return a charge per call, so
  there's no billed figure for Jev.
