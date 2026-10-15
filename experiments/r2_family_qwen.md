# r2_family_qwen: Qwen on every stage

**Round 2, 2026-10-15.** Config: [`r2_family_qwen.yaml`](../pipeline/config/experiments/r2_family_qwen.yaml) at commit `548d453`.
Both full runs passed `make verify`, with no fallbacks and no errored posts.

**Verdict:** level with the recorded Gemini figures on triage and product names, weaker on autos
prices, and far behind on Stage 4 signals. It's a cheap option for Stages 2 and 3. Qwen 2.5 7B
is out for Stage 4.

## Setup

| Stage | Model | Output mode | Reasoning |
|---|---|---|---|
| 2 triage, text / vision | `qwen-2.5-7b-instruct` / `qwen3-vl-8b-instruct` | `json_object` | unset (neither reasons) |
| 3 extract, text / vision | `qwen3-14b` / `qwen3-vl-8b-instruct` | `json_schema` | text `"off"`, vision unset |
| 4 signals | `qwen-2.5-7b-instruct` | `json_object` | unset |

All models are reached through OpenRouter. Stage 1 profiles are cached from earlier runs, so
they made no call.

## Results

| | autos (30 posts) | gadgets (41 posts) | recorded Gemini, autos / gadgets |
|---|---|---|---|
| Run id | `r2_family_qwen_20261015T144319Z` | `r2_family_qwen_20261015T144953Z` | README [Model comparison](../README.md#model-comparison) |
| **S2** post type, 5-class | 29/30 = 97% | 38/41 = 93% | 97% / 93% |
| S2 product vs not (precision / recall) | 97% (100% / 96%) | 95% (100% / 94%) | |
| S2 vision escalations | 0/30 | 5/41, all 5 correct | 1/30, 5/41 |
| **S3** price, first product | 16/19 = 84% | 31/31 = 100% | 100% / 100% |
| S3 price, every product | 19/24 = 79% | 88/91 = 97% | |
| S3 name similarity | 92% | 82% (74% every product) | 91% / 81% |
| S3 missing-price recall | 6/6 | 6/6 | 6/6 / 3/3 |
| S3 products returned / gold | 30 / 30 | 95 / 97 | |
| S3 vision escalations | 7/25 | 5/33 | |
| **S4** micro F1 (precision / recall) | 33% (100% / 20%) | 0% (found 0 of 5) | 88% / 77% |
| Tokens | 132,005 | 139,044 | 91,102 / 115,281 |
| Cost, hand-priced | ~$0.019 | ~$0.021 | |
| Wall time | 6 min | 6 min | |

The recorded Gemini column is **not a like-for-like baseline**. It was measured in September,
partly under older prompts. The comparison that counts is `r2-control`: Gemini 3.5 Flash-Lite
through OpenRouter on today's prompts, not yet run.

## Per stage

| Stage, model | Provider | First-try schema failures | Output tokens / call | Median latency (max) |
|---|---|---|---|---|
| S2 text, Qwen 2.5 7B | Phala | 0 / 66 | 17 | 0.7 s (2.5 s) |
| S2 vision, Qwen3-VL 8B | Parasail | 0 / 5 | 22 | 3.1 s |
| S3 text, Qwen3-14B | NextBit, DeepInfra | 0 / 58 | 201 autos, 366 gadgets | 6.5–8.2 s (40 s) |
| S3 vision, Qwen3-VL 8B | Parasail | 0 / 12 | 169–201 | 6.7–8.9 s (13 s) |
| S4, Qwen 2.5 7B | Phala | **22 / 24** | 6–21 | 0.7 s (1.0 s) |

- **Stage 4 is the model's limit, not the plumbing.** Qwen 2.5 7B's first answer failed the
  schema on 22 of 24 posts, returning shapes like `{'log signals': []}`, `{'warnings': "..."}`
  or `{}`. The retry fixed every one. Even then it found 5 of 25 autos signals and none on
  gadgets. What it did report was always right: precision is 100%.
- **Stage 3 is slow because of its providers.** About 7–8 s for roughly 200–370 output tokens,
  with one call at 40 s.
- On gadgets, post `18091786415437652` returned 2 of its 4 products.

## How we got here

Each step below has its own run in `report/token_log_v2.csv`.

1. **September** (`archive/family_qwen.yaml`): failed the gate on autos. Qwen 2.5 7B invented
   labels and keys (`"log"`, `sync_confidence`) on 44 of 71 first attempts. Two posts exhausted
   every retry, in both runs.
2. **Diagnostic, 2026-10-15:** the same two posts, 6 calls per mode, came back valid 6/6 under
   both `json_object` and `json_schema`, all served by Phala. So September's failures most likely
   came from a provider OpenRouter routed to; at the time no column recorded which.
3. **`…T134852Z`, all `json_schema`:** passed the gate, but every Stage 2 first attempt ran away
   in the `confidence` float until it hit `max_tokens`. Stages 2 and 4 moved back to
   `json_object`.
4. **`…T140152Z`:** Stage 2 was clean, 5/5 on the first try. One Stage 3 attempt spent all 4,096
   output tokens and 199 s on hidden reasoning and returned nothing.
5. **`…T144058Z`, reasoning off for Stage 3:** output fell from 463–1,539 to 114–383 tokens per
   call, with no invalid attempts. Then the two full runs above.

## Caveats

- **Cost is hand-priced** from OpenRouter's listed rates on 2026-10-15, $/M in / out:
  - Qwen 2.5 7B: 0.10 / 0.20
  - Qwen3-14B: 0.12 / 0.24
  - Qwen3-VL 8B: 0.25 / 0.75

  `MODEL_RATES` has no Qwen entries yet (`r2-rates`), so the log prices these rows at 0.0.
  Schema retries are about $0.002 of the total, and are counted.
- **One run per vendor.** There's no noise floor yet. Stage 4 has 1 to 8 gold instances per
  signal, so its per-signal F1 is anecdote, not measurement.
- **Gadgets posts `18149430643539602` and `17983552128060866`** can't be fetched from Instagram
  any more and keep expired image links. Neither needed vision in this run.
- **The harness scores each stage against gold upstream labels.** A chained run would score
  lower.
