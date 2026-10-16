# r2_family_gpt4o_mini: GPT-4o Mini on every stage

**Round 2, 2026-10-16.** Config: [`r2_family_gpt4o_mini.yaml`](../pipeline/config/experiments/r2_family_gpt4o_mini.yaml).
One model for every stage, text and vision. It isn't a reasoning model, so reasoning is unset.
Both full runs passed `make verify`, with no fallbacks, no errored posts and no schema retries.

**Verdict:** reliable but not cheap. It is only 1.3–1.4× cheaper than the
[Gemini control](r2_control_gemini.md), because a vision call is billed at about 26–36k input tokens.
Vision is 66–75% of its cost.
- It's behind the control on every scored axis.
- On gadgets it dropped 4 real listings in Stage 2, at confidence 0.8–0.9, so none escalated.
- It misses the same 3 autos price ranges as both Qwen runs (see "A shared miss" below).
- [Qwen3.7 Flash](r2_family_qwen37_flash.md) matches or beats it on every axis but autos name similarity,
  at about a tenth of the cost.

## Setup

| Stage | Model | Output mode | Reasoning |
|---|---|---|---|
| 2 triage, text / vision | `gpt-4o-mini` / `gpt-4o-mini` | `json_object` | unset |
| 3 extract, text / vision | `gpt-4o-mini` / `gpt-4o-mini` | `json_object` | unset |
| 4 signals | `gpt-4o-mini` | `json_object` | unset |

OpenAI and Azure served the calls. Every vision call went to Azure. Stage 1 profiles were
cached.

## Results

| | autos (30 posts) | gadgets (41 posts) | [control](r2_control_gemini.md), autos / gadgets |
|---|---|---|---|
| Run id | `r2_family_gpt4o_mini_20261016T003038Z` | `r2_family_gpt4o_mini_20261016T003419Z` | `r2_control_gemini_20261015T154323Z` / `…T154556Z` |
| **S2** post type, 5-class | 28/30 = 93% | 36/41 = 88% | 100% / 98% |
| S2 product vs not (precision / recall) | 97% (100% / 96%) | 90% (100% / 88%) | 100% / 100% |
| S2 vision escalations | 1/30, correct | 5/41, all correct | 1/30, 5/41 |
| **S3** price, first product | 16/19 = 84% | 31/31 = 100% | 100% / 100% |
| S3 price, every product | 19/24 = 79% | 89/91 = 98% | 100% / 100% |
| S3 name similarity, first / every product | 89% / 89% | 84% / 83% | 90% / 90%, 87% / 85% |
| S3 missing-price recall | 6/6 | 6/6 | 6/6 / 6/6 |
| S3 products returned / gold | 30 / 30 | 95 / 97 | 30 / 30, 97 / 97 |
| S3 vision escalations | 6/25 | 5/33 | 5/25, 5/33 |
| **S4** micro F1 (precision / recall) | 79% (75% / 84%) | 89% (4 of 5) | 87% / 77% |
| Tokens | 285,328 | 465,403 | 118,966 / 142,292 |
| **Cost**, listed rates | **$0.046** | **$0.076** | $0.061 / $0.107 |
| Cost, billed by OpenRouter | $0.043 | $0.072 | not recorded then |
| Wall time | 3.3 min | 4.0 min | 2.4 min / 2.9 min |

The misses:
- **S2:** the 4 gadgets listings it dropped are `17994209417829051`, `17908533072539933` and
  `18117403781512663`, read as testimonials, and `17944115412074853`, read as an announcement.
  They're dropped from the catalog, not mis-imported.
  - The shared gadgets meme `18190139290395806` is wrong again, as for every model so far.
  - On autos, listing `18073956491710644` went to announcement at confidence exactly 0.7, so
    it didn't escalate.
  - Announcement `17897690802571800` was read as a meme.
- **S4, autos:** 4 misses (3 `urgent`, 1 `swap_deal`) and 7 false positives, 4 of them
  `price_negotiable`. Gadgets missed one `finance_available`.

## A shared miss: price ranges

The three autos posts with no price, `18042748280816067`, `18051759722796086` and
`18133507585622516`, all give the price as a range: ₦40–45M, $34–38k and ₦8.5–9.2M. Gold records
the low end.
- GPT-4o Mini, Qwen and Qwen3.7 Flash all return no price for them. Gemini takes the low end.
- That's three model families failing on the same posts, so the likely cause is how the Stage 3
  prompt handles a range, not the models.
- An empty price goes to `needs_attention`, so these aren't wrong catalog entries.
- It's the next thing to fix in Stage 3, and it would move every non-Gemini model's autos price
  score.

## Per stage

| Stage | Calls (autos + gadgets) | Output tokens / call, median | Median latency | Billed (autos + gadgets) |
|---|---|---|---|---|
| S2 text | 66 | 20 | 1.1–1.5 s | $0.0077 |
| S2 vision | 6 | 20 | 3.2–6.0 s | **$0.0303** |
| S3 text | 58 | 182 autos, 267 gadgets | 3.1–3.4 s (12 s) | $0.0206 |
| S3 vision | 11 | 150–160 | 5.1–6.3 s | **$0.0517** |
| S4 | 24 | 26–90 | 1.3–1.7 s | $0.0043 |

- **Images are what it costs.** GPT-4o Mini bills a vision call at about 26–36k input tokens,
  against hundreds for Gemini or Qwen. Its 17 vision calls cost $0.082, 72% of its two runs.
  Without them it would be about $0.03 for both vendors.
- **Sending images at low detail** (OpenAI's `detail: "low"`, a flat charge of about 2.8k
  tokens) would cut this sharply. It would need a code change to the vision passes, and it
  might cost accuracy on prices printed in the image. It hasn't been tested.

## Caveats

- **One run per vendor; there's no noise floor yet** (`r2-noise`).
- **Gadgets Stage 4 has 5 gold signal instances in all**, so its 89% is anecdote.
- **Billed cost isn't comparable with the control,** which ran before billing was recorded.
  Compare on listed rates.
- **The harness scores each stage against gold upstream labels.** A chained run would score
  lower.
