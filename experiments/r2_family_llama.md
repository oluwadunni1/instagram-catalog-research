# r2_family_llama: Llama 3.1 8B with Llama 4 Scout

**Round 2, 2026-10-16.** Config: [`r2_family_llama.yaml`](../pipeline/config/experiments/r2_family_llama.yaml).
Llama 3.1 8B for every text pass and Llama 4 Scout for both vision passes, the pairing round 1
planned but never ran. Neither model reasons. **Only the autos run counts.** It passed
`make verify`. The gadgets run failed the gate on one errored post and isn't scored here.

**Verdict, autos only:** cheap, and good at triage, but out for Stages 3 and 4.
- **Stage 3:** it multiplies every US-dollar price by 1,000 ($44,000 becomes 44,000,000). That
  is a wrong number that would go straight into the catalog.
- **Stage 4:** it found 22 of 25 signals but added 39 false positives, often 5 or 6 on one post.
  Micro F1 is 51%.
- **Stage 2:** 97% product vs not-product, close to the
  [Gemini control](r2_control_gemini.md), at a seventh of its listed cost.

## Setup

| Stage | Model | Output mode | Reasoning |
|---|---|---|---|
| 2 triage, text / vision | `llama-3.1-8b-instruct` / `llama-4-scout` | `json_object` / `json_schema` | unset |
| 3 extract, text / vision | `llama-3.1-8b-instruct` / `llama-4-scout` | `json_object` / `json_schema` | unset |
| 4 signals | `llama-3.1-8b-instruct` | `json_object` | unset |

**This is the first config that needs a different output mode for vision.**
- Llama 3.1 8B's cheap providers accept `json_object` but not `json_schema`.
- Scout is the reverse. With `json_object` on both passes, the first smoke run
  (`r2_family_llama_20261016T002658Z`) found no Scout endpoint at all, and both vision calls fell
  back to the regex guess.
- `vision_structured_output` (added for this run) sets the vision passes on their own. Scout
  then routes to DeepInfra.

Text calls were spread over Groq, DeepInfra, Novita and Cloudflare. Stage 1 profiles were cached.

## Results

| | autos (30 posts) | [control](r2_control_gemini.md), autos |
|---|---|---|
| Run id | `r2_family_llama_20261016T003829Z` | `r2_control_gemini_20261015T154323Z` |
| **S2** post type, 5-class | 28/30 = 93% | 100% |
| S2 product vs not (precision / recall) | 97% (100% / 96%) | 100% (100% / 100%) |
| S2 vision escalations | 0/30 | 1/30 |
| **S3** price, first product | 15/19 = 79% | 100% |
| S3 price, every product | 20/24 = 83% | 100% |
| S3 name similarity, first / every product | 89% / 89% | 90% / 90% |
| S3 missing-price recall | 6/6 | 6/6 |
| S3 products returned / gold | 30 / 30 | 30 / 30 |
| S3 vision escalations | 5/25 | 5/25 |
| **S4** micro F1 (precision / recall) | **51%** (36% / 88%) | 87% (80% / 96%) |
| Tokens | 114,774 | 118,966 |
| **Cost**, listed rates | **$0.0087** | $0.061 |
| Cost, billed by OpenRouter | $0.0048 | not recorded then |
| Wall time | 5.0 min | 2.4 min |

The misses:
- **S2:** two, both shared with GPT-4o Mini.
  - Listing `18073956491710644` went to announcement.
  - Announcement `17897690802571800` was read as a meme.
- **S3:** all 4 wrong prices are US-dollar prices, each multiplied by 1,000:
  - `18349537654172552`: $75,000 became 75,000,000;
  - `17916501969435418`: $44,000 became 44,000,000;
  - `18106477655229394`: $78,500 became 78,500,000;
  - `18051759722796086`: $34,000 became 3,400,000.

  It's the only model so far to price the range on `18051759722796086`, and it got that one
  wrong too.
- **S4:** 39 false positives against 22 hits. Two examples:
  - post `17997025208807776` got all 6 signals except the one in gold;
  - `18331493713257979` got 6 wrong signals as well.

  `price_negotiable` is the most common false call. The model labels nearly everything rather
  than reading the post.

## Per stage

| Stage | Calls | Output tokens / call, median | Median latency (max) | Billed |
|---|---|---|---|---|
| S2 text, 3.1 8B | 30 | 21 | 1.2 s (2.8 s) | $0.0009 |
| S3 text, 3.1 8B | 25 | 182 | 6.2 s (17 s) | $0.0015 |
| S3 vision, Scout | 5 | 140 | 5.3 s (17 s) | $0.0020 |
| S4, 3.1 8B | 16 | 126 | 4.3 s (9.3 s) | $0.0004 |

- **Billed is 55% of listed,** because `MODEL_RATES` holds the Groq price and most calls went to
  cheaper providers.
- **It's slow for an 8B model.** Its Stage 3 text calls take 6.2 s median, against 1.8 s for
  Flash-Lite. Gadgets was slower still, up to 47 s per call.

## The gadgets run that failed the gate

`r2_family_llama_20261016T004339Z` **did not pass `make verify`**, so none of its scores are
used.
- Scout's Stage 2 vision call on post `18457546087186979` wrote whitespace under `json_schema`
  until it hit 16,384 output tokens. That one call was half the run's bill.
- The vision passes don't retry, so the post errored.
- It's the fourth model with a runaway in strict schema mode, after Qwen 2.5 7B, Nex and MiMo.
  This one wasn't the `confidence` decimal. An output limit on the vision
  passes would cap it.

## Caveats

- **One vendor, one run; there's no noise floor yet** (`r2-noise`).
- **Text calls went to four providers** at different quantizations, so one run can't separate
  the model from the mix.
- **Billed cost isn't comparable with the control,** which ran before billing was recorded.
  Compare on listed rates.
- **The harness scores each stage against gold upstream labels.** A chained run would score
  lower.
