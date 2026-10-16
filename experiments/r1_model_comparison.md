# r1_model_comparison: the September model families, and what they cost

**Round 1, September 2026.** Gemini, GPT-4o Mini, Llama and Qwen, each run as a whole family across Stages 2-4.
Moved out of the root `README.md` on 2026-10-16, unchanged apart from heading levels and cross-references. Every figure here comes from a run that passed `make verify`, and every run id is in the frozen v1 log, `report/token_log.csv`.

**Verdict:** Gemini beat the GPT-4o Mini baseline on every scored axis on both vendors, and it was the most expensive model measured at paid rates. Round 2 replaces this as the model comparison: see [r2_control_gemini](r2_control_gemini.md) and the `r2_family_*` records.

## Per-stage scores

Per-stage harness scores, each stage scored independently against gold labels:

| vendor | family | S2 acc | S3 price | name sim | miss price | S4 macro / micro | escalation | tokens |
|---|---|---|---|---|---|---|---|---|
| autos | **Gemini** | **97%** | **100%** | **91%** | 6/6 | 87% / **88%** | 1/30 | **91,102** |
| autos | GPT-4o Mini | 93% | 84% | 87% | 6/6 | 77% / **76%** | 2/30 | 287,258 |
| autos | Llama | 87% | 79% | 87% | 6/6 | 48% / **51%** | 0/30 | 92,878 |
| gadgets | **Gemini** | **93%** | **100%** | **81%** | 3/3 | 67% / **77%** | 5/41 | **115,281** |
| gadgets | Llama | 90% | 87% | 72% | 3/3 | 38% / **43%** | 5/41 † | 136,193 |

**The `S3 price` column above is the first-product metric** - it scores `predicted[0]` against
`gold[0]` only, which is 34% of gadgets' priced products. See [r1_stage3_all_products](r1_stage3_all_products.md) for what those models score
when every product is counted; the ranking changes.

† Llama's gadgets escalation is inflated by a `max_tokens` bug: 3 of those 5 vision calls only
repaired a truncated Pass A. Accuracy stands, escalation and cost do not.

Gemini beat the GPT-4o Mini baseline on every scored axis for both vendors. Qwen failed its
gate twice on invalid JSON and has no recorded numbers.

In the first Stage 2 comparison, Llama and Gemini both scored 27/30 while getting zero of the
same posts wrong, both at 0.8 to 0.95 confidence on their own errors. A confidence threshold
only catches uncertainty a model admits to.

## Cost

Measured from `report/token_log.csv` for `vendor_gadgets_01`.

| Stage | Calls | Tokens/post | Share |
|---|---|---|---|
| Stage 1 (amortised over 41 posts) | 1 | 26 | 1% |
| Stage 2 | 42 | 500 | 18% |
| **Stage 3** | 37 | **2,011** | **74%** |
| Stage 4 | 8 | 197 | 7% |
| **Total** | 88 | **2,734** | |

Stage 3 is three quarters of all token spend. Stage 4 is cheapest despite the longest prompt,
because its regex prefilter lets only 8 of 41 posts reach a model.

At live paid rates, the model that wins on quality is the most expensive one measured:

| Model | $/post | $/1,000 posts |
|---|---|---|
| Gemini 3.5 Flash-Lite | $0.001688 | $1.69 |
| GPT-4o Mini | $0.000588 | $0.59 |
| Qwen 2.5 7B | $0.000313 | $0.31 |
| Llama 3.1 8B | $0.000149 | $0.15 |

Gemini's completion tokens cost $2.50/M, over 4x GPT-4o Mini and 31x Llama. These runs were made
on the free tier, so this is arithmetic over real token counts and published rates, not observed
spend.
