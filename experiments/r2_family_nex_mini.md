# r2_family_nex_mini: Nex N2.5 Mini on every stage

**Round 2, 2026-10-15, ultra-cheap tier.** Config: [`r2_family_nex_mini.yaml`](../pipeline/config/experiments/r2_family_nex_mini.yaml).
One model for every stage, text and vision, with reasoning off on every pass. **Only the autos
run counts.** It passed `make verify`. The gadgets run failed the gate and isn't scored here.

**Verdict, autos only:** cheap, with good prices, but Stage 2 lets too many non-products through.
- It called 4 of 5 non-product posts product listings, at confidence 0.98 or above, so none of
  them escalated. Product-post precision is 86%, under the brief's 90% target.
- Those posts would reach Stage 3 and the catalog.
- It needs a gadgets rerun before it can be ranked.

## Setup

| Stage | Model | Output mode | Reasoning |
|---|---|---|---|
| 2 triage, text / vision | `nex-n2.5-mini` / `nex-n2.5-mini` | `json_schema` | `"off"` / `"off"` |
| 3 extract, text / vision | `nex-n2.5-mini` / `nex-n2.5-mini` | `json_schema` | `"off"` / `"off"` |
| 4 signals | `nex-n2.5-mini` | `json_schema` | `"off"` |

Nex AGI, its only provider, served every call. Stage 1 profiles were cached.

## Results

| | autos (30 posts) | [control](r2_control_gemini.md), autos |
|---|---|---|
| Run id | `r2_family_nex_mini_20261015T162405Z` | `r2_control_gemini_20261015T154323Z` |
| **S2** post type, 5-class | 25/30 = 83% | 100% |
| S2 product vs not (precision / recall) | 87% (86% / 100%) | 100% (100% / 100%) |
| S2 vision escalations | 1/30, wrong | 1/30 |
| **S3** price, first product | 18/19 = 95% | 100% |
| S3 price, every product | 23/24 = 96% | 100% |
| S3 name similarity, first / every product | 87% / 88% | 90% / 90% |
| S3 missing-price recall | 6/6 | 6/6 |
| S3 products returned / gold | 30 / 30 | 30 / 30 |
| S3 vision escalations | 4/25 | 5/25 |
| **S4** micro F1 (precision / recall) | 82% (81% / 84%) | 87% (80% / 96%) |
| Tokens | 204,724 | 118,966 |
| **Cost**, listed rates | **$0.0058** | $0.061 |
| Cost, billed by OpenRouter | $0.0039 | not recorded then |
| Wall time | 2.7 min | 2.4 min |

The misses:
- **S2, called a product by mistake:**
  - testimonial reposts `18114987011511783` and `18173859304438540`;
  - announcements `18331493713257979` and `17894529186657159`.

  It also read announcement `17897690802571800` as a meme, at confidence 0.62. That one
  escalated to vision, and vision got it wrong too.
- **S3:** one wrong price. Post `18073956491710644` came back as ₦85,000 against a gold
  ₦123,000,000. That's worse than an empty price: an empty price goes to `needs_attention`,
  while a wrong one can go straight into the catalog.
- **S4:** 4 misses (3 `urgent`, 1 `swap_deal`) and 5 false positives (3 of them
  `price_negotiable`).

## Per stage

| Stage | Calls | First-try schema failures | Output tokens / call, median | Median latency (max) |
|---|---|---|---|---|
| S2 text | 30 | **2 / 30** | 22 | 1.0 s (4.4 s) |
| S2 vision | 1 | 0 | 23 | 14 s |
| S3 text | 25 | 0 | 142 | 1.9 s (4.5 s) |
| S3 vision | 4 | 0 | 122 | 6.8 s (7.9 s) |
| S4 | 16 | 0 | 58 | 1.3 s (2.9 s) |

- **Both schema failures are the runaway `confidence` decimal.** Each ran to the 1,024-token
  cap under `json_schema` before the retry fixed it.
- **It counts more tokens than the other models for the same prompts:** 205k against Qwen3.7
  Flash's 121k. Its lower price per token makes up for it.

## The gadgets run that failed the gate

`r2_family_nex_mini_20261015T162658Z` **did not pass `make verify`**, so none of its scores are
used.
- Nex AGI returned `internal_error` (502) on the same 5 posts in both Stage 2 and Stage 3:
  `17875545636631606`, `18085616054264180`, `18457546087186979`, `17940067572092567` and
  `18351809149247806`.
- In Stage 3 those posts fell back to the regex guess.
- They are the five posts the other models sent to vision. **Every gadgets vision call
  failed**, while the autos vision calls worked.
- The run took 20 minutes, against 3 for autos, mostly retrying the failed calls.

So the failure looks tied to those images, or to Nex's vision path on them, rather than a passing
outage. That's not confirmed. One rerun would tell the two apart.

## Caveats

- **One vendor, one run; there's no noise floor yet** (`r2-noise`).
- **Billed cost isn't comparable with the control,** which ran before billing was recorded.
  Compare on listed rates.
- **The harness scores each stage against gold upstream labels.** A chained run would score
  lower, and lower still for this model, since its Stage 2 false positives would feed Stage 3.
