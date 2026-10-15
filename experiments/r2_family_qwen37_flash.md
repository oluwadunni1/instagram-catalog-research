# r2_family_qwen37_flash: Qwen3.7 Flash on every stage

**Round 2, 2026-10-15, ultra-cheap tier.** Config: [`r2_family_qwen37_flash.yaml`](../pipeline/config/experiments/r2_family_qwen37_flash.yaml).
One model for every stage, text and vision, with reasoning off on every pass. Both full runs
passed `make verify`, with no fallbacks, no errored posts and no schema retries.

**Verdict:** the best of the ultra-cheap tier, and 13–18× cheaper than the
[Gemini control](r2_control_gemini.md) at listed rates.
- It matches the control on product vs not-product (100% on both vendors) and on gadgets prices.
- Its gap is autos prices: it misses 3 of 19, the same 3 posts [Qwen family](r2_family_qwen.md) missed.
- Stage 4 is 5 points behind the control on autos. Gadgets has too few signals to judge.

## Setup

| Stage | Model | Output mode | Reasoning |
|---|---|---|---|
| 2 triage, text / vision | `qwen3.7-flash` / `qwen3.7-flash` | `json_object` | `"off"` / `"off"` |
| 3 extract, text / vision | `qwen3.7-flash` / `qwen3.7-flash` | `json_object` | `"off"` / `"off"` |
| 4 signals | `qwen3.7-flash` | `json_object` | `"off"` |

It runs on `json_object` because its only provider, Alibaba, doesn't accept `json_schema`.
Alibaba served every call. Stage 1 profiles were cached, so that stage made no call.

## Results

| | autos (30 posts) | gadgets (41 posts) | [control](r2_control_gemini.md), autos / gadgets |
|---|---|---|---|
| Run id | `r2_family_qwen37_flash_20261015T161602Z` | `r2_family_qwen37_flash_20261015T161935Z` | `r2_control_gemini_20261015T154323Z` / `…T154556Z` |
| **S2** post type, 5-class | 30/30 = 100% | 40/41 = 98% | 100% / 98% |
| S2 product vs not (precision / recall) | 100% (100% / 100%) | 100% (100% / 100%) | 100% / 100% |
| S2 vision escalations | 2/30, both correct | 5/41, all correct | 1/30, 5/41 |
| **S3** price, first product | 16/19 = 84% | 31/31 = 100% | 100% / 100% |
| S3 price, every product | 19/24 = 79% | 91/91 = 100% | 100% / 100% |
| S3 name similarity, first / every product | 86% / 88% | 91% / 88% | 90% / 90%, 87% / 85% |
| S3 missing-price recall | 6/6 | 6/6 | 6/6 / 6/6 |
| S3 products returned / gold | 30 / 30 | 97 / 97 | 30 / 30, 97 / 97 |
| S3 vision escalations | 6/25 | 5/33 | 5/25, 5/33 |
| **S4** micro F1 (precision / recall) | 82% (83% / 80%) | 100% (5 of 5) | 87% / 77% |
| Tokens | 120,933 | 137,989 | 118,966 / 142,292 |
| **Cost**, listed rates | **$0.0046** | **$0.0059** | $0.061 / $0.107 |
| Cost, billed by OpenRouter | $0.0037 | $0.0048 | not recorded then |
| Wall time | 3.4 min | 4.3 min | 2.4 min / 2.9 min |

The misses:
- **S2:** the only miss is gadgets post `18190139290395806`, gold `meme_personal`, read as an
  announcement. The control and Qwen family made the same call.
- **S3:** three autos posts returned no price: `18042748280816067`, `18051759722796086` and
  `18133507585622516`. They're the same three Qwen family missed. Two of them escalated to vision
  and still came back with no price. The control priced all three, so the price is
  recoverable. An empty price goes to `needs_attention` rather than into the catalog with a
  wrong number.
- **S4:** 5 misses, 4 of them `urgent`, and 4 false positives (2 `clearance`, 2
  `stock_count_known`). It under-calls `urgent`, where the control over-calls.

## Per stage

| Stage | Calls (autos + gadgets) | Output tokens / call, median | Median latency (max) | Billed (autos + gadgets) |
|---|---|---|---|---|
| S2 text | 66 | 23 | 1.2 s (2.6 s) | $0.0017 |
| S2 vision | 7 | 23 | 2.5–3.3 s | $0.0003 |
| S3 text | 58 | 213 autos, 332 gadgets | 3.1–4.0 s (17 s) | $0.0044 |
| S3 vision | 11 | 198–209 | 5.1–7.0 s (12 s) | $0.0013 |
| S4 | 24 | 53–98 | 1.7–2.0 s | $0.0008 |

- **Reasoning off held.** No call ran away, and no answer needed a schema retry.
- **Billed is about 20% under listed** on both runs.

## Caveats

- **One run per vendor; there's no noise floor yet** (`r2-noise`).
- **Gadgets Stage 4 has 5 gold signal instances in all**, so its 100% is anecdote. Autos has
  25. Per-signal F1 is anecdote on both vendors.
- **Billed cost isn't comparable with the control.** The control's runs came before
  `billed_cost_usd` was recorded, so compare on listed rates.
- **The harness scores each stage against gold upstream labels.** A chained run would score
  lower.
