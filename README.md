# Instagram Catalog Intelligence Pipeline

A research POC that turns a Nigerian vendor's Instagram feed into a structured product catalog
plus a short list of items needing the vendor's attention, using the cheapest model that can do
each job rather than one expensive call per post.

Input is a connected Instagram Professional account. Output is `catalog.json` and a three-way
split: `auto_import`, `needs_attention`, `auto_exclude`.

This file is the overview. **The detailed results, run ids and per-experiment caveats are in
[`experiments/`](experiments/README.md)**, one record per experiment, newest first.

## What the brief asked for

Prove that a cascade pipeline (cheap model first, expensive model only on escalation) can parse
a real vendor account accurately and cheaply; that Nigerian caption conventions (`25k`,
`₦25,000`, `#25k`, "DM for price", price-on-image, Pidgin/Yoruba mixing, "it don finish") can be
extracted reliably; that the attention flags are trustworthy enough to feel like "confirm a few
things" rather than "redo everything"; and which model wins each stage at what cost.

Two hard rules: **models are config, not code** (swapping one is a YAML edit), and **never
invent a price** (`source: "none"` plus a flag is always correct). No scraping; official Meta
API only.

## Architecture

```
Stage 0  INGEST     API pull -> raw JSON dump                        (no AI)
Stage 1  ACCOUNT    What kind of business is this?                   (1 call per account)
Stage 2  TRIAGE     Product post vs. not?                            (text -> OCR -> vision)
Stage 3  EXTRACT    name, variants, price, quantity                  (text -> +OCR -> vision)
Stage 4  SIGNALS    Stale? Out of stock?                             (regex prefilter -> LLM)
Stage 5  FLAGS      Confidence routing -> import vs. attention       (no AI)
Stage 6  SYNC       Re-pull -> diff -> AI on deltas only             (no generative calls)
```

Escalation triggers live in Python, not in prompt wording:

| Stage | Escalates when |
|---|---|
| 2 | Pass A confidence < threshold, or the caption is empty/emoji-only. In the Jev configs a caption-less post goes to the photo (Jev with images) or to local OCR and then Gemini (the hybrid) |
| 3 | missing name, `price.source == "none"`, or low confidence -> Tier 2 re-asks with the local OCR text; still unresolved -> vision. **Unless** `DM_FOR_PRICE_RE` matches (a confirmed absence, not uncertainty) |
| 4 | never. A free regex prefilter gates the LLM call, so a post with no candidate keywords costs zero |
| 5 | never. Deterministic Python over cached outputs, zero LLM calls |
| 6 | no generative calls ever. The diff itself is pure Python, but a new or caption-edited post costs one `gemini-embedding-001` call; an unchanged post costs nothing |

**OCR is the cascade's free tier.** `pipeline/ocr.py` runs locally, so a failed read costs only
latency. Both stages put it ahead of the vision model rather than in place of it.

`llm_client.py::complete_structured()` is the single call site for every LLM interaction: JSON
repair, retry on schema mismatch, backoff on 429/5xx, throttling, token logging. Two kinds of
call bypass it and must therefore log and throttle themselves: the vision Pass B calls
(multimodal message shapes) and Stage 6's caption embeddings
(`pipeline/media_fingerprint.py`, a raw `embedContent` POST). Jev calls go to TypeSafe directly
through `pipeline/jev_client.py`.

## Results

Every figure comes from a run that passed `scripts/verify_run.py`. Two vendors:
`vendor_autos_01` (30 labelled posts) and `vendor_gadgets_01` (41). The autos account has grown
since labelling; the 10 newer posts are unlabelled and never enter a figure.

There are two rounds, and their numbers are not interchangeable:
- **Round 1 (September 2026)** ran on the Google AI Studio free tier. Its costs are projections
  from token counts at published rates. Evidence: `report/token_log.csv`, frozen.
- **Round 2 (October 2026)** reruns every family through OpenRouter under a $5 cap, with billed
  cost and latency per call. Evidence: `report/token_log_v2.csv`. Its baseline is
  [r2_control_gemini](experiments/r2_control_gemini.md).

### Against the brief's targets

| Target | Round 1, Gemini cascade | Round 2, Gemini control | |
|---|---|---|---|
| Triage precision >= 90% | product vs not: 100% / 100% (hybrid, [r1](experiments/r1_stage2_rebaseline.md)) | 100% / 100% | met |
| Price accuracy >= 85% | every product 100% / 98% ([r1](experiments/r1_stage3_all_products.md)) | 100% / 100% | met |
| Missing-price recall 100% | 6/6, 6/6 | 6/6, 6/6 | met |
| Stale/stock flag precision >= 75% | micro F1 88% / 77% | micro F1 87% / 77% | met |
| Escalation below 40% | 1/30, 5/41 | 1/30, 5/41 (Stage 2) | met |
| Attention <= 10 per 100 posts | 18% / 15% | 15% / 7%, chained ([r2_chain_best](experiments/r2_chain_best.md)) | **missed on autos** |
| Full parse <= $0.15 / 100 posts | $0.17, projected | $0.20 / $0.26 all-Gemini; **$0.14 / $0.17** chained with Jev for Stage 2 | **met on autos, missed on gadgets** |
| Sync on unchanged account ~ $0 | 45 no-ops, 0 AI calls ([r1](experiments/r1_stage6_sync.md)) | not rerun | met |
| Change-classification precision >= 85% | 100%, n=23 changed posts | not rerun | met, **first measurement** |
| Carousel classification >= 85% | not scored | not scored | **not measured** |
| Wall-clock <= 10 min / 100 posts | not instrumented | ~6 / ~6.7 min per 100 posts, chained, Stages 2-5 | met |

Autos / gadgets throughout. Round 2's cost and time exclude Stages 0 and 1, whose outputs were
cached. Per-stage rows are harness scores, which assume perfect upstream routing.

### Which model for which stage

Round 2 is the current comparison; one run per vendor, so no noise floor yet.

**Chained, the best pick per stage holds up** ([r2_chain_best](experiments/r2_chain_best.md)):
Jev for Stage 2 and Gemini for Stages 3-4, with Stage 3 seeing only what Stage 2 passed on.
Product vs not-product is 97% / 95% at 100% precision, and every post that reached Stage 3 was
priced correctly. The only loss is 1 autos and 2 gadgets listings Jev routed away. Gadgets' gold
is stale on 15 posts whose captions the Stage 6 test edited, so those are left out of the chained
Stage 3 score.


- **Stage 2, triage:** Jev now reads images. An all-Jev cascade (text, then Jev with the photo
  when unsure) scores 97% / 98% product vs not-product at 100% precision, about 15× cheaper than
  Gemini's own Stage 2 and with no OCR ([r2_jev](experiments/r2_jev.md)). Images are a TypeSafe
  beta, so the configs are pinned to `jev-1.13.0`.
- **Stage 3, extraction:** Gemini is the only family at 100% of prices on both vendors.
  Qwen3.7 Flash matches it on gadgets and is 13–18× cheaper, but misses 3 of 19 autos prices,
  all given as a range, which Gemini reads and Qwen, Qwen3.7 Flash and GPT-4o Mini do not
  ([r2_family_qwen37_flash](experiments/r2_family_qwen37_flash.md)).
- **Stage 4, signals:** Gemini leads (87% / 77% micro F1). Llama is out (39 false positives on
  autos), and the noise floor makes small gaps unquotable.
- **Out or incomplete:** GPT-4o Mini is behind the control everywhere and only 1.3–1.4× cheaper.
  Nex, MiMo and Llama have validated autos runs only, and their gadgets runs failed the gate.

The round-1 comparison, kept for its run ids and its lessons, is in
[r1_model_comparison](experiments/r1_model_comparison.md). One of those lessons still holds: in
the first Stage 2 comparison Llama and Gemini both scored 27/30 with no error in common, each at
0.8 to 0.95 confidence on its own errors, so a confidence threshold only catches uncertainty a
model admits to.

### Experiment records

| Record | What it settled |
|---|---|
| [r2_chain_best](experiments/r2_chain_best.md) | the chained pipeline on round 2's picks: routing penalty, cost and time per 100 posts |
| [r2_jev](experiments/r2_jev.md) | Jev with images replaces the Jev → OCR → Gemini hybrid for Stage 2 |
| [r2_control_gemini](experiments/r2_control_gemini.md) | the round-2 baseline |
| `r2_family_*` | Qwen, Qwen3.7 Flash, Nex, MiMo, GPT-4o Mini and Llama against the control |
| [r1_stage6_sync](experiments/r1_stage6_sync.md) | Stage 6 measured on a designed change set: 56% fewer posts reprocessed, 100% change classification |
| [r1_stage3_all_products](experiments/r1_stage3_all_products.md) | scoring only the first product hid under-extraction; the granularity rule |
| [r1_stage3_ocr](experiments/r1_stage3_ocr.md) | the OCR tier pays only on caption-less product cards; the price guard |
| [r1_stage2_rebaseline](experiments/r1_stage2_rebaseline.md) | the label codebook, the Jev + Gemini hybrid, and why gadgets is no longer held out for Stage 2 |
| [r1_model_comparison](experiments/r1_model_comparison.md) | the September families, and where the tokens go (Stage 3 is ~74%) |

The full index, with every verdict, is [`experiments/README.md`](experiments/README.md).

## Reproducing

Golden sets and raw dumps are committed, so a clone can score against the same hand labels.

```bash
git clone https://github.com/oluwadunni1/instagram-catalog-research.git
cd instagram-catalog-research
uv sync                       # fetches Python 3.14 (.python-version) and installs into .venv
cp .env.example .env          # add one provider key, e.g. OPENROUTER_API_KEY or GEMINI_API_KEY
uv run pytest -q              # offline sanity check - no key, no network

make harness GOLDEN=eval/golden/vendor_autos_01.json \
             CONFIG=pipeline/config/experiments/default.yaml
make verify  RUN_ID=<run_id the harness printed> POSTS=30
```

Windows ships no `make`. `make help` prints the `uv run ...` command behind every target; run
that instead, or use WSL. If one folder is shared between Windows and WSL, give each OS its own
environment - see `CLAUDE.md`, which also lists every command.

| Works from a clone | Needs |
|---|---|
| Tests | nothing |
| Harness on the committed golden sets, text-only stages | a provider key in `.env` |
| Pulling a new account (`make ingest`) | your own Meta app and an Instagram Professional account token |
| Vision and OCR tiers | live image links - see below |
| `report/` (predictions, token logs) and `data/snapshots/` | R2 credentials for `make data-pull`; the bucket is private |

`make stage5` re-runs routing with zero LLM calls, but its inputs are the cached predictions in
`report/`, which a clone does not have. Run the harness first, then point
`STAGE2=`/`STAGE3=`/`STAGE4=` at the files it wrote.

**The image links are expired and you cannot refresh them.** `fbcdn.net` URLs carry short-lived
`oh=`/`oe=` params, and `scripts/refresh_media_urls.py` needs a token belonging to the account
that owns the posts. Stage 2/3 vision and OCR tiers, Stage 6 pHash and snapshot builds need live
images; text passes, Stage 4, Stage 5 and the Stage 6 content-hash diff do not. Escalated posts
will fail on dead links. That is expected, not a bug.

`LLM_MIN_CALL_INTERVAL` (default 4.5s) spaces provider calls. Gemini's free tier caps at 15
requests/minute; without pacing the 429s are absorbed by the retry loop and Stage 3 drops to its
regex fallback while still reporting clean scores. Set to `0` on a paid tier.

## Layout

```
ingest/            Stage 0: Graph API pull, caching, retry
pipeline/stages/   Stages 1-6, one module each
pipeline/config/   experiment YAMLs + schema
pipeline/llm_client.py   single LLM call site
pipeline/jev_client.py   TypeSafe Jev calls, text and images
eval/harness.py    scores stages against the golden set
eval/golden/       hand-labeled golden sets   (git)
experiments/       one record per experiment  (git)
runs/              raw dumps, profiles, catalogs (git)
report/            predictions + token logs   (DVC)
data/snapshots/    Stage 6 baselines + history/ (DVC)
scripts/           drivers and checks
tests/             offline only
```

`pipeline/config/experiments/` holds only the configs behind a current figure. Every run_id is
prefixed with the config's `name`, so a figure traces back to one file, and each experiment record
links the configs it ran. `default.yaml` is the active default. `archive/` keeps superseded
round-1 configs under their original names, since renaming one would orphan its run_ids; they
still load but are not maintained. Configs that were never run were deleted on 2026-09-29 and are
in git history.

Three identifiers, easily confused:

- `account_label` (`vendor_autos_01`): a local folder name you choose. Need not match Instagram.
- `vendor_handle` (`ayodele.akinbohun`): the real Instagram username. Keys `data/snapshots/`.
- `vendor_id`: the golden file's stem. Scopes `report/<vendor_id>/` and the token logs.

## Verification

- `eval/harness.py` scores stages against the golden set. It is the only check on model quality.
- `scripts/verify_run.py` gates a run against the token log, the only record of what actually
  reached the provider. Fails on zero rows, any `fallback_used=True` row, a model outside
  `--expect-model`, an implausible call count, or any errored post. Exits non-zero.
- `tests/` is offline only and covers what the harness cannot: credential handling, per-post
  error isolation, the reserved-parameter tripwire, Stage 5 routing, the token-log reader, the
  spend cap, the secret scanner, and the Stage 6 diff. No test may make a network call, read
  `.env`, or read a golden set.
- `tests/test_stage6_sync.py` pins the boundaries where an off-by-one moves a number without
  failing anything, and drives `run_sync` repeatedly, because the two defects fixed on 2026-09-23
  were invisible in a single sync and only appeared in the one after it
  ([r1_stage6_sync](experiments/r1_stage6_sync.md)). It also records that the `1e-9` epsilon
  guarding the cosine division makes the embedding threshold's inclusive `>=` exclusive in
  practice at exactly 0.92 - documented rather than "fixed", since the epsilon is what keeps an
  empty caption's zero vector from dividing by zero.

No number here comes from a run that has not passed the gate.

## What went wrong

Every one of these was silent. The pipeline kept producing plausible output throughout.

- **A configured model never bound.** `load_stage_fn()` strips a config key named `model`, and
  `detect_signals()` named its parameter `model`. A four-model Stage 4 comparison ran entirely
  on one model. Caught only from the token log. It now raises instead of binding silently.
- **The harness and the chained path fed stages different inputs.** The harness passed no
  Stage 1 profile while `run_pipeline.py` did, so published accuracy was measured on inputs the
  production path never uses. Silent because `profile` is optional everywhere.
- **Rate limiting became fake accuracy.** 429s absorbed by the retry loop pushed Stage 3 into
  its regex fallback, and the run reported clean scores over heuristic output. This is why
  `verify_run.py` exists.
- **Two credential leaks, same shape.** A token in a URL query string, embedded by `requests`
  into an exception that got logged. Now headers only, with `redact_tokens()` on every log path.
- **Reels broke vision.** A Reel's `media_url` is an `.mp4`. Everything now resolves through
  `vision_image_url()`, which prefers `thumbnail_url`.
- **`DM_FOR_PRICE_RE` missed a plural**, escalating confirmed price-absences to vision as if
  they were uncertain.
- **Stale CDN URLs biased results by path.** Only golden sets were refreshed, not the raw dumps
  the chained path reads.
- **Only the first product per post was scored**, so under-extraction on multi-product posts
  scored 100% ([r1_stage3_all_products](experiments/r1_stage3_all_products.md)).
- **The sync's content hash compared two sources that can never agree.** It included the length
  of the comments array, which a live fetch returned empty while the Meta app was in
  Development mode, and which the
  golden-derived baseline filled by hand. Every affected post reported `content_changed` on every
  sync. It stayed hidden because the symptom - a sync finding changes - looks exactly like a sync
  working, and because the README had already explained it away as CDN churn.
- **Stage 6 spent provider quota nothing recorded.** Its caption embeddings never wrote a token
  row or respected the rate limiter, so the one stage claiming to cost nothing was also the one
  stage whose cost no artifact could confirm or refute.
- **Strict schema mode can run away.** Four round-2 models (Qwen 2.5 7B, Nex, MiMo, Llama 4
  Scout) wrote whitespace or digits until the output limit on at least one call; one such call
  was half a run's bill. No output-token limit caps Stage 2 or the vision passes yet.
- **Two months of comments were blamed on the wrong gate.** Comment text came back empty, and
  the recorded cause was App Review: that Meta withholds it until Advanced Access. That ruled out
  re-investigating, so nobody did. The cause was the app's Development mode, where `/comments`
  returns an empty list with no error even for the owner's own comment. Switching the app to
  Live fixed it on 2026-10-17 with no code change.

Each was caught by an artifact recording what happened, not what was configured to happen - or,
in the Stage 6 cases, by going to look for the artifact and finding it empty.

## Caveats

- **Per-stage accuracy is not system accuracy.** Stages are scored against gold labels, not
  against the previous stage's output. A chained run is measurably lower.
- **Round 1's costs are projections.** Paid pricing applied to free-tier runs, not observed
  spend. Round 2's are from the log; billed where OpenRouter returned a charge, and Jev's are
  estimated from tokens because TypeSafe returns none.
- **The noise floor is large.** Re-running the same model on the same golden set moved
  per-signal F1 by 20 to 35 points, since most signals have 1 to 3 gold instances. Only
  macro-level gaps are safe to quote. Gadgets has zero gold instances for 3 of 7 signals. Round 2
  has one run per vendor and no noise floor of its own yet.
- **Gadgets is no longer held out for Stage 2.** Its labels were corrected twice in response to
  observed model behaviour ([r1_stage2_rebaseline](experiments/r1_stage2_rebaseline.md)), and
  the hybrid's 0.80 escalation threshold was tuned on autos.
- **Stage 2 figures before 2026-09-21 predate the label codebook, and Stage 3 figures before
  2026-09-24 predate the granularity rule;** neither is comparable with what came after.
- **Jev scores a binary question where Gemini's Stage 2 picks among five classes,** so part of
  any Jev advantage is the smaller option set.
- **One suspected gold-labelling error was never investigated** (`18079164065330268`).
- **Single-vendor model rankings did not survive a second vendor.**
- **Stage 6's change-classification precision is 100% on 23 changed posts, but three of its five
  cells are `caption_edit`/`no_op`/`deleted`,** which Stage 6 decides by the same string
  comparison the ground truth was derived from, and only `caption_edit` clears n >= 10. The
  earlier 87% reduction came from a golden-derived baseline and is superseded by 56%.

## Known limits

- **No recorded result has read a real comment.** Until 2026-10-17 the Meta app was in
  Development mode, where `/comments` returns an empty list with no error while
  `comments_count` is real. Every dump, chained Stage 4 score and Stage 6 `comment_delta`
  recorded so far comes from that period, and the golden sets' comments are hand-written. Live
  mode returns comment text, but these two accounts have only 4 comments between them,
  commenter usernames sometimes come back empty, and a comment from an account with no role on
  the app is not yet confirmed.
- **`estimated_cost_usd` is 0.0 on most v1 rows, and 0.0 does not mean free.** In round 1 only
  Jev had a rate in `MODEL_RATES`. Gemini's round-1 completion rate ($2.50/M) is recorded in
  [r1_model_comparison](experiments/r1_model_comparison.md), but its prompt rate was never
  written down, and half a rate cannot price a row. Rows are never recomputed, so `read_run_usage()` reports
  `priced_calls` and `billed_calls` beside `calls`, and a total whose coverage is short of
  `calls` understates and must be quoted that way. The harness's printed "Cost:" line is
  `cost_per_call_usd` x call count - a config constant, not spend.
- **Stage 6's embedding calls are logged but cannot be priced.** `gemini-embedding-001` rows
  carry 0 tokens, because `embedContent` returns no usage block, so a Stage 6 cost figure has to
  come from the call *count* against the published embedding rate. What the log does settle: a
  sync on an unchanged account writes **zero** `stage6_embedding` rows.
- **Carousel per-slide extraction was never built.** Every carousel attaches all slides as one
  gallery, so carousel classification is unmeasured. On a multi-product carousel every slide
  image attaches to `products[0]`, including the slide that would tell two products apart.
- **Nothing runs Stages 1 to 6 end to end on live predictions.** `run_pipeline.py` chains 1 to 5
  and is a driver, not an orchestrator.
- **Two products with the same name at different prices route `auto_import`.** `route_post`
  flags missing price, unknown name and low confidence, and nothing checks for a duplicate name.
  A real gadgets caption lists `Samsung S26 Ultra 8/256GB` twice at N1,300,000 and N1,100,000;
  extraction confidence is 1.0 - correctly, because it measures fidelity to the caption, not
  whether the resulting catalog is coherent.
- **A caption-less post can only ever be repost-matched by pHash.** An empty caption embeds to a
  zero vector, which matches nothing, so the embedding half of Stage 6's cascade is structurally
  unavailable for them. Embedding the OCR text instead would close it and needs its own gated run.
- **pHash only catches literal file reuse.** A vendor who reposts a product with a fresh photo is
  invisible to it; four Stage 6 change types have never fired on real data.
- **Jev's image input is a beta** ("dreamer-ready"), and two gadgets posts have image links that
  have expired for good, so a policy that needs every image cannot be scored on that golden set.

Research POC, sponsor-owned, published for reference. Not licensed for reuse.
