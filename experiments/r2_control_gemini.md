# r2_control_gemini: the round-2 baseline

**Round 2, 2026-10-15.** Config: [`r2_control_gemini.yaml`](../pipeline/config/experiments/r2_control_gemini.yaml) at commit `796197c`.
`default.yaml`'s Gemini models, unchanged, routed through OpenRouter rather than the Google AI
Studio free tier. Both full runs passed `make verify`, with no fallbacks, no errored posts and no
schema retries.

**Verdict:** this is the bar every round-2 candidate has to clear.
- Product vs not-product: 100% on both vendors.
- Prices: 100% on every product.
- Stage 4: micro F1 87% on autos, 77% on gadgets.
- Cost: $0.002–0.003 per post, at 1.4–2.1 s per call.

## Setup

| Stage | Model | Output mode | Reasoning |
|---|---|---|---|
| 2 triage, text / vision | `gemini-3.5-flash-lite` / `gemini-3.5-flash` | `json_object` | unset (provider default) |
| 3 extract, text / vision | `gemini-3.5-flash-lite` / `gemini-3.5-flash-lite` | `json_object` | unset |
| 4 signals | `gemini-3.5-flash-lite` | `json_object` | unset |

Every call was served by Google. Stage 1 profiles were cached, so that stage made no call.

## Results

| | autos (30 posts) | gadgets (41 posts) |
|---|---|---|
| Run id | `r2_control_gemini_20261015T154323Z` | `r2_control_gemini_20261015T154556Z` |
| **S2** post type, 5-class | 30/30 = 100% | 40/41 = 98% |
| S2 product vs not (precision / recall) | 100% (100% / 100%) | 100% (100% / 100%) |
| S2 vision escalations | 1/30, correct | 5/41, all correct |
| **S3** price, first product | 19/19 = 100% | 31/31 = 100% |
| S3 price, every product | 24/24 = 100% | 91/91 = 100% |
| S3 name similarity, first / every product | 90% / 90% | 87% / 85% |
| S3 missing-price recall | 6/6 | 6/6 |
| S3 products returned / gold | 30 / 30 | 97 / 97 |
| S3 vision escalations | 5/25 | 5/33 |
| **S4** micro F1 (precision / recall) | 87% (80% / 96%) | 77% (62% / 100%) |
| Tokens | 118,966 | 142,292 |
| **Cost**, from the log | **$0.061**, $0.0020 / post | **$0.107**, $0.0026 / post |
| Wall time | 2.4 min | 2.9 min |

## Per stage

| Stage, model | Calls (autos + gadgets) | Output tokens / call | Median latency | Cost (autos + gadgets) |
|---|---|---|---|---|
| S2 text, Flash-Lite | 66 | 25 | 1.4 s | $0.019 |
| S2 vision, **Flash** | 6 | 275–375 | 4.8–5.7 s | $0.034 |
| S3 text, Flash-Lite | 58 | 238 autos, 449 gadgets | 1.8 s | $0.088 |
| S3 vision, Flash-Lite | 10 | 199–245 | 2.0–3.6 s | $0.015 |
| S4, Flash-Lite | 24 | 54–104 | 1.4–1.8 s | $0.012 |

- **Gemini 3.5 Flash reasons by default.** On Stage 2's vision pass it produced 275–375 output
  tokens for a two-field answer, at $9/M output. Those 6 calls are 20% of the control's total
  cost; on gadgets alone, 5 calls cost $0.029. Flash-Lite answers in 25 tokens, so it doesn't.
  A `vision_reasoning: "off"` variant is the obvious next test, but it would no longer be the
  control.
- **Stage 4 errs towards over-calling.** Of 9 false positives, 3 are `urgent` (all on gadgets),
  2 each are `clearance` and `stock_count_known`, and 1 each are `price_negotiable` and
  `finance_available`. Its one miss: `urgent` on autos post
  `18133507585622516`.
- **The single S2 miss is a meme the model read as an announcement:** gadgets post
  `18190139290395806`, gold `meme_personal`, at confidence 0.95. Qwen made the same call.

## Against the September figures

The README's model comparison has Gemini through the free tier: 97% / 93% on Stage 2, 100% /
100% on prices, and 88% / 77% micro F1 on Stage 4. Today's control matches or beats every one of
those. Stage 2 is the one that moved most, and the prompts changed in between (the codebook
landed on 2026-09-21). The September run also had free-tier rate limits.

## Caveats

- **One run per vendor; there's no noise floor yet** (`r2-noise`).
- **Cost uses OpenRouter's listed rates** in `MODEL_RATES` on 2026-10-15, and it looks high.
  Shortly after these runs, OpenRouter showed $0.1255 billed for the key across all of
  2026-10-15's work. The control alone logs $0.177, before the Qwen runs (~$0.05) are added.
  So the logged figure is either an upper bound (Gemini caches repeated prompt prefixes, and
  every call here shares a long system prompt), or billing had not caught up yet.
  `r2-budget` reconciles the two. Until it does, compare models on logged cost, which uses the
  same rates for every model, not on billed cost.
- **Stage 4 has 1 to 8 gold instances per signal**, so its per-signal F1 is anecdote.
- **The harness scores each stage against gold upstream labels.** A chained run would score
  lower.
