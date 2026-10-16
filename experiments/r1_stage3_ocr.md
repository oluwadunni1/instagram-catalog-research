# r1_stage3_ocr: the local OCR tier in Stage 3

**Round 1, 2026-09-21 to 2026-09-22.** Config `stage3_ocr.yaml` against `default.yaml`, Stage 3 alone (`--only-stage 3`).
Moved out of the root `README.md` on 2026-10-16, unchanged apart from heading levels and cross-references. Every figure here comes from a run that passed `make verify`, and every run id is in the frozen v1 log, `report/token_log.csv`.

**Verdict:** OCR cut gadgets' vision calls from 6 to 1, and cost autos 10% more tokens, so across the two vendors it roughly breaks even. On autos, the price guard voided two image prices the model had invented.

Stage 2's cascade drove its own vision spend to zero by putting local OCR ahead of the vision
model. That left Stage 3 holding every remaining vision call in the project - 214 of them, 67%
of all vision cost - while `pipeline/ocr.py` was already caching the text for exactly the posts
Stage 3 was paying to look at, and nothing read the cache.

Stage 3 scored alone (`--only-stage 3`). All four runs VALIDATED.

| vendor | config | calls | text | text+OCR | vision | tokens | price acc | missing-price recall | name sim |
|---|---|---|---|---|---|---|---|---|---|
| gadgets | `default.yaml` | 40 | 34 | 0 | **6** | 95,133 | 31/31 = 100% | 3/3 = 100% | 81% |
| gadgets | `stage3_ocr.yaml` | 35 | 29 | 5 | **1** | 80,888 | 31/31 = 100% | 3/3 = 100% | 76% |
| autos | `default.yaml` | 28 | 25 | 0 | **3** | 62,502 | 19/19 = 100% | 6/6 = 100% | 90% |
| autos | `stage3_ocr.yaml` | 31 | 25 | 3 | **3** | 68,729 | 19/19 = 100% | 6/6 = 100% | 90% |

Run ids: `default_20260921T233431Z`, `stage3_ocr_20260921T233746Z`,
`default_20260922T090452Z`, `stage3_ocr_20260922T090946Z`.

**The tier pays on one vendor and costs on the other, and the reason is the same fact both
times.** OCR only helps when the answer is printed on the picture. On gadgets it removed five of
six vision calls and 15% of tokens: those posts are caption-less with the product and price on
the image, which is what OCR is for. On autos it removed nothing and added three calls and 10%
more tokens - autos captions already carry the price, so Stage 3 escalates there for other
reasons, and the extra text pass answered nothing the caption had not. **Averaged across the two
it is roughly break-even on cost.** It is not a general saving; it is a saving on caption-less
product cards, and those have to exist in the feed for it to pay.

**Read the gadgets saving as image tokens, not a model downgrade.** Stage 2's OCR tier cut cost
79% because the vision call it displaced ran on `gemini-3.5-flash`. Here `default.yaml` already
points `vision_model` at `flash-lite`, so what OCR displaces is a same-model call carrying an
image. No dollar figure is quoted because no Gemini rate is on file (see Known limits in the root `README.md`).

**The price guard fired twice on autos, and that is the more interesting result.** On two posts
the model returned an image-sourced price - ₦32,600,000 and ₦123,000,000 - that the OCR text
contained no money-shaped token to support at all. The Pass A OCR call cannot see the image, so
an image-sourced price it cannot ground is an invention. Both were voided and escalated to
vision, and price accuracy stayed 19/19. Without the guard those two would have been published
as prices read off the photo. That is the failure the brief calls unforgivable, it appeared on
the FIRST vendor the tier was pointed at, and it appeared on the vendor where the tier otherwise
does nothing.

**Why a garbled read cannot become a wrong price.** OCR text reaches the model in its own
labelled `IMAGE TEXT` block, never as the caption - the substitution `stage2_triage_hybrid.py`
uses is safe for a product/not-product question and is not safe here. Any price marked
`price.source = "image"` must clear `_guard_ocr_prices`, which requires the exact integer to be
recoverable from the raw OCR text and refuses ambiguous separators: the spike's `N500.000` is
500000 or nothing, never 500. Checked against the five committed caches in
`runs/vendor_gadgets_01/ocr/` before it was wired: 5/5 grounded, and the IMEI in one of them
produces no candidate at all because it carries no currency marker.

**Name similarity: 76% against a 78-81% baseline band on gadgets, unchanged at 90% on autos.**
Two baseline gadgets runs measured 78% and 81%, two OCR runs both measured 76%. It is a free
fuzzy `SequenceMatcher` score, not a gate, and OCR text carries character noise by construction
(`IPH0NE`, `C0RE3`). Recorded rather than explained away.

**Not tried: gating the second tier on whether the OCR text contains a price at all.** All three
autos Tier 1 calls were spent on text that had no money token in it, and all three escalated to
vision anyway. Checking for a candidate before paying for the extra pass would have made autos
free instead of 10% more expensive. It is a change to the escalation rule, so it needs its own
gated run on both vendors.

**Two defects this comparison surfaced, both fixed:**

- The harness names its predictions file after the config's `model:` display label. Both configs
  started with the same label, so the first OCR run silently overwrote the baseline file it was
  meant to be compared against. `stage3_ocr.yaml` now carries a distinct label even though the
  litellm strings are identical.
- `verify_run.py`'s call-count band derives from `--posts`, but a `--only-stage 3` run touches
  only the posts whose *gold* type is `product_listing` - 34 of 41 on gadgets, 25 of 30 on
  autos. Passing the full post count failed both good runs on the floor. The band is right for a
  3-stage run and wrong for a single-stage one; pass the scored denominator until the gate
  learns about `--only-stage`.

**One earlier attempt is void and is recorded so nobody recovers it from the log.** Run
`stage3_ocr_20260921T234301Z` reported 68% price accuracy and 33% name similarity on autos. It
exhausted the Gemini free tier and logged 16 `fallback_used` rows - almost all of that output is
the regex heuristic, not the model. The companion `default_20260921T234042Z` carries one such
row. Both fail the gate. This is the documented 429-becomes-fake-accuracy failure, caught by the
mechanism built for it.
