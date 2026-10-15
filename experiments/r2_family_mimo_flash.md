# r2_family_mimo_flash: MiMo v2.6 Flash on every stage

**Round 2, 2026-10-15, ultra-cheap tier.** Config: [`r2_family_mimo_flash.yaml`](../pipeline/config/experiments/r2_family_mimo_flash.yaml).
One model for every stage, text and vision, with reasoning off on every pass. **Only the autos
run counts.** It passed `make verify`. The gadgets run was stopped partway, when the machine ran
low on memory, and isn't scored.

**Verdict, autos only:** perfect on prices, but slow and over-eager on signals.
- It got all 24 autos prices right, the only ultra-cheap model to do so.
- It is 7× slower than the control: 16 minutes for 30 posts.
- More than half its bill went on runaway answers in Stage 2.
- Stage 4 found every signal but added 10 false positives.
- It's 2.6× cheaper than the control, the least saving in the tier.

## Setup

| Stage | Model | Output mode | Reasoning |
|---|---|---|---|
| 2 triage, text / vision | `mimo-v2.6-flash` / `mimo-v2.6-flash` | `json_schema` | `"off"` / `"off"` |
| 3 extract, text / vision | `mimo-v2.6-flash` / `mimo-v2.6-flash` | `json_schema` | `"off"` / `"off"` |
| 4 signals | `mimo-v2.6-flash` | `json_schema` | `"off"` |

OpenRouter spread the calls over four providers: Xiaomi, Makora, Io Net and GMICloud. Stage 1
profiles were cached.

## Results

| | autos (30 posts) | [control](r2_control_gemini.md), autos |
|---|---|---|
| Run id | `r2_family_mimo_flash_20261015T164716Z` | `r2_control_gemini_20261015T154323Z` |
| **S2** post type, 5-class | 27/30 = 90% | 100% |
| S2 product vs not (precision / recall) | 93% (100% / 92%) | 100% (100% / 100%) |
| S2 vision escalations | 2/30, both correct | 1/30 |
| **S3** price, first product | 19/19 = 100% | 100% |
| S3 price, every product | 24/24 = 100% | 100% |
| S3 name similarity, first / every product | 90% / 91% | 90% / 90% |
| S3 missing-price recall | 6/6 | 6/6 |
| S3 products returned / gold | 30 / 30 | 30 / 30 |
| S3 vision escalations | 3/25 | 5/25 |
| **S4** micro F1 (precision / recall) | 83% (71% / 100%) | 87% (80% / 96%) |
| Tokens | 150,297 | 118,966 |
| **Cost**, listed rates | **$0.0237** | $0.061 |
| Cost, billed by OpenRouter | $0.0173 | not recorded then |
| Wall time | 16.2 min | 2.4 min |

The misses:
- **S2:** two product listings went the wrong way at confidence 0.72, just above the 0.7
  threshold, so neither escalated:
  - `18120996466885495`, read as an ad creative;
  - `18029319446669897`, read as an announcement.

  Both would be dropped from the catalog, which is the safer direction to be wrong in.
  Announcement `17897690802571800` was read as a meme, as Nex did.
- **S4:** no misses, but 10 false positives:
  - 4 `price_negotiable`, 3 `stock_count_known`, 2 `clearance`, 1 `urgent`;
  - one of them on a post whose gold has no signal at all.

## Per stage

| Stage | Calls | First-try schema failures | Output tokens / call, median | Median latency (max) | Billed |
|---|---|---|---|---|---|
| S2 text | 30 | **11 / 30** | 26 | **9.2 s** (19 s) | **$0.0095** |
| S2 vision | 2 | 0 | 26 | 4.4 s | $0.0003 |
| S3 text | 25 | 0 | 181 | **12.5 s** (25 s) | $0.0037 |
| S3 vision | 3 | 0 | 117 | 4.4 s (17 s) | $0.0012 |
| S4 | 16 | 0 | 100 | **12.7 s** (36 s) | $0.0026 |

- **Strict schema mode is what makes it expensive.** 11 of 30 first triage answers wrote the
  `confidence` decimal to the 1,024-token cap, for example `0.95625000000000002220446…`. The
  retry fixed each one, but those 11 calls are 55% of the run's bill.
- **It's slow on every provider,** not just one: 9–13 s median per text call, against the
  control's 1.4–1.8 s.

## Caveats

- **One vendor, one run; there's no noise floor yet** (`r2-noise`).
- **Scores may depend on the provider.** Four providers served this run, and OpenRouter lists 8,
  at different quantizations. One run can't separate the model from the mix.
- **Billed cost isn't comparable with the control,** which ran before billing was recorded.
  Compare on listed rates.
- **The harness scores each stage against gold upstream labels.** A chained run would score
  lower.
