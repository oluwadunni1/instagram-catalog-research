# r2_chain_best: round 2's pick per stage, run as one chain

**Round 2, 2026-10-17.** Config: [`r2_chain_best.yaml`](../pipeline/config/experiments/r2_chain_best.yaml).
The first chained run of round 2: `scripts/run_pipeline.py` over each vendor's latest raw dump,
so Stages 3 and 4 see only the posts Stage 2 called listings, then `scripts/score_catalog.py`
against the golden sets. Both runs passed `make verify` (catalog passed as `--predictions`), with
no fallbacks, no errored posts and one schema retry.

| Stage | Model | Picked from |
|---|---|---|
| 1 profile | cached per account, no call | |
| 2 triage | Jev text, then Jev with the photo (`jev-1.13.0`, TypeSafe) | [r2_jev](r2_jev.md): 97% / 98% product vs not, 100% precision |
| 3 extract, text / vision | `gemini-3.5-flash-lite` / `gemini-3.5-flash-lite` (OpenRouter) | [r2_control_gemini](r2_control_gemini.md): the only family at 100% of prices on both vendors |
| 4 signals | `gemini-3.5-flash-lite` (OpenRouter) | the control: micro F1 87% / 77%, ahead of every candidate |
| 5 routing | deterministic, no call | |

**Verdict:** the chain holds up. Every post that reached Stage 3 was priced correctly on both
vendors, and the only accuracy the chain loses is the 3 listings Jev sent away. Cost is $0.14 /
$0.17 per 100 posts, and a 100-post run takes about 6–7 minutes.
- **Routing penalty: 1 autos and 2 gadgets listings** never reached extraction, so they score
  zero on name and price. That is the whole gap between the chained and per-stage price figures.
- **Stage 4 can't be judged from a chained run** on these golden sets (see the caveats).

## Results

| | autos | gadgets |
|---|---|---|
| Run id | `pipeline_vendor_autos_01_20261017T103539Z` | `pipeline_vendor_gadgets_01_20261017T103823Z` |
| Posts in the dump / labelled and scored | 40 / 30 | 45 / 39 (2 deleted since labelling) |
| **S2** product vs not (precision) | **29/30 = 97%** (100%) | **37/39 = 95%** (100%) |
| S2 post type, 5-class | 24/30 = 80% | 31/39 = 79% |
| S2 photo calls | 7/40 | 11/45 |
| **S3** price, first product, chained | **17/17 = 100%** | **14/16 = 88%** |
| S3 name similarity, first product | 88% | 82% |
| S3 missing-price recall | 6/6 | 2/2 |
| Posts left out of S3/S4 for stale gold | 2 | 15 |
| Routing penalty (listings never extracted) | 1: `18029319446669897` | 2: `17908533072539933`, `18117403781512663` |
| **S5** auto_import / needs_attention / auto_exclude | 26 / 6 / 8 | 33 / 3 / 9 |
| Attention rate | 15% | 7% |
| Calls / tokens | 104 / 158,384 | 99 / 179,853 |
| **Cost**, listed | **$0.0566**, $0.14 / 100 posts | **$0.0762**, $0.17 / 100 posts |
| of which billed by OpenRouter / Jev estimated | $0.0546 / $0.0020 | $0.0737 / $0.0025 |
| Wall time | 2.4 min, ~6 min / 100 posts | 3.0 min, ~6.7 min / 100 posts |

The 5-class Stage 2 line is listed only to show it doesn't apply: Jev answers product or not, so
its `not_product` never equals a gold `announcement`, and every correct exclusion counts as a
miss there.

**The misses:**
- **Gadgets prices:** both misses are the two routing-penalty posts. Of the 16 listings with
  unchanged captions, every one Stage 3 saw was priced right.
- **Autos `18029319446669897`:** the post the photo flips the wrong way, as in r2_jev.
- **Gadgets `18117403781512663`:** text confidence just at the 0.80 trigger, as in r2_jev.
- **Gadgets `17908533072539933`**, a Reel: new. Jev saw the photo and answered 0.59 for
  not-a-product, where r2_jev's cascade run got it right. Same input, same pinned model, so this is
  Jev's run-to-run spread, the thing `r2-noise` is for.

## Per stage

| Stage | Calls, autos / gadgets | Median latency (max) | Billed, autos + gadgets | Share of cost |
|---|---|---|---|---|
| S2 Jev text | 40 / 39 | 1.1 s (2.0 s) | $0.0045 est. (both Jev rows) | 3% |
| S2 Jev with photo | 7 / 11 | 2.3–2.5 s (6.6 s) | | |
| S3 text | 32 / 36 | 1.5–1.6 s (4.6 s) | $0.0993 | 75% |
| S3 vision | 4 / 7 | 2.3–2.7 s (3.5 s) | $0.0164 | 12% |
| S4 | 21 / 6 | 1.0–1.1 s (1.5 s) | $0.0127 | 10% |

- **Stage 3 is 87% of the bill,** as in round 1. Stage 2 on Jev is close to free.
- **Chaining saves the calls the harness can't:** 8 and 9 non-listings never reach Stages 3-4.
- **One schema retry,** on gadgets Stage 3 text: the model returned a bare list of products
  instead of the wrapping object, and the retry fixed it.

## How it was scored

`score_catalog.py` gained two things for this run, both tested offline
(`tests/test_score_catalog.py`):
- **A product vs not-product Stage 2 score** beside the 5-class one, with precision.
- **Stale gold is left out of Stages 3 and 4, and listed.** The chained run reads the live dump.
  Gadgets' dump is from 2026-09-24, after the [Stage 6 measurement](r1_stage6_sync.md) edited 15
  captions on purpose and deleted 2 posts, and two autos captions have changed since labelling.
  On those posts the model read the new price, gold holds the old one, and 11 of the first
  report's 13 price "misses" were exactly that. Stage 2 still scores them: the edits changed
  prices, not what the posts are.

`run_pipeline.py` also had a bug that stopped any chained run from saving its catalog since the
v2 log: `read_run_usage()` returns the log's `Path`, and `json.dumps()` raised on it after the run
had paid for every call. Fixed, with a test.

## Caveats

- **Stage 4's chained score isn't a model measurement.** Meta returns no comment text, so the
  dump's comments are empty, while the golden sets carry hand-authored comments that some gold
  signals come from. On autos, 7 of 11 missed signals are on posts with gold comments the run never
  saw. Gadgets has 4 gold signal instances left once stale posts are out. Judge Stage 4 on the
  harness's 87% / 77%.
- **Stale-gold exclusion shrinks gadgets' Stage 3 set** from 31 priced posts to 16. The 15 left
  out are the ones the Stage 6 test edited, not a random sample.
- **One run per vendor; there's no noise floor yet** (`r2-noise`). Jev moved one gadgets post across
  the line between this run and r2_jev.
- **The chained scorer still uses the first-product metric** for prices and names, so it can't
  see under-extraction on multi-product posts ([r1_stage3_all_products](r1_stage3_all_products.md)).
- **Jev cost is estimated,** tokens × $0.042/M; TypeSafe returns no charge.
- **Wall time is single-threaded with no pacing** (`LLM_MIN_CALL_INTERVAL=0`) and excludes
  Stage 0 and Stage 1, whose outputs were cached.
- **Run ids are `pipeline_<account>_…`,** not prefixed by the config name: `run_pipeline.py`
  names them that way. The config is recorded in each catalog's `config` block.
