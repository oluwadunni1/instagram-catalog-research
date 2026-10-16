# r1_stage3_all_products: scoring every product, and the granularity rule

**Round 1, 2026-09-24.** The corrected Stage 3 scorer, and the product-granularity rule in `eval/LABEL_CODEBOOK.md`.
Moved out of the root `README.md` on 2026-10-16, unchanged apart from heading levels and cross-references. Every figure here comes from a run that passed `make verify`, and every run id is in the frozen v1 log, `report/token_log.csv`.

**Verdict:** the first-product metric checked only 34% of gadgets' priced products and could not see under-extraction at all. Scored on every product, Gemini + OCR gets 98% of gadgets prices and 100% of autos. One real omission remains unfixed: the Starlink post.

Every cached prediction file re-scored through the corrected scorer. No model calls: this
re-scores the exact output those runs produced, so the numbers are comparable to each other but
carry their original runs' caveats, noted under the table.

> **Every row here was produced under the PRE-2026-09-24 Stage 3 prompt**, which told the model to
> collapse purchasable variants into a `variants` array. Only Gemini + OCR has been re-run since
> the granularity rule landed, and it moved from 97% to **98%** with 95 of 97 products returned.
> Comparing any row below against that re-run compares two prompt regimes, not two models - the
> confound is the whole lesson of the Gemini MMLU table, where the model ranking flips depending
> on which regime you compare in. The rows below are like-for-like with **each other** and nothing
> else.

| vendor | config | first product | **all products** | under-extracted posts | products returned / gold |
|---|---|---|---|---|---|
| autos | Gemini flash-lite | 19/19 = 100% | **24/24 = 100%** | 0 | 30 / 30 |
| autos | Gemini + OCR | 19/19 = 100% | **24/24 = 100%** | 0 | 30 / 30 |
| autos | Qwen | 17/19 = 89% | **20/24 = 83%** | 0 | 30 / 30 |
| autos | Llama | 15/19 = 79% | **20/24 = 83%** | 0 | 30 / 30 |
| autos | GPT-4o Mini | 16/19 = 84% | **19/24 = 79%** | 0 | 30 / 30 |
| gadgets | Gemini flash-lite | 31/31 = 100% | **89/91 = 98%** | 3 | 91 / 97 |
| gadgets | Gemini + OCR | 31/31 = 100% | **88/91 = 97%** | 4 | 90 / 97 |
| gadgets | Qwen | 26/31 = 84% | **86/91 = 95%** | **0** | **97 / 97** |
| gadgets | Llama | 27/31 = 87% | **75/91 = 82%** | 4 | 91 / 97 |
| gadgets | dummy-heuristic | 27/31 = 87% | **63/91 = 69%** | 5 | 69 / 97 |
| gadgets | GPT-4o Mini | 19/31 = 61% | **39/91 = 43%** | 7 | 57 / 97 |

**The ranking changes on gadgets.** Qwen looks worse than Llama on the first-product metric
(84% vs 87%) and clearly better on every product (95% vs 82%), because it is the only config
that returned **all 97 gold products with no under-extraction at all** - better completeness than
Gemini, which drops 3 to 6. Reading a multi-product catalog from the first product of each post
was ranking models on a tenth of their output.

**Three provenance caveats, none of which the table can carry on its own:**

- **Qwen has no gated run.** It failed `verify_run.py` twice on invalid JSON, which is why it
  carries no numbers in [r1_model_comparison](r1_model_comparison.md). These figures come from cached predictions of
  ungated runs and are a reason to re-run it properly, not a result.
- **GPT-4o Mini's gadgets file predates the `max_tokens` fix** (2026-09-08 vs 2026-09-10), so its
  43% is substantially truncation, not judgment. It is the clearest illustration of the gap:
  truncated output loses whole products, and the first-product metric cannot see that.
- **`dummy-heuristic` is the regex fallback**, not a model. Its 87% first-product against 69% all
  products is what a single-product heuristic scores when the catalog is multi-product.

**Gemini + OCR looked marginally worse than Gemini alone under the old prompt** (97% vs 98%, one
more under-extracted post) - within noise on a 91-product denominator, and both were prompt-matched
so the comparison was fair at the time. It no longer holds either way: Gemini + OCR has since been
re-run at 98% under the new prompt and Gemini alone has not, so there is currently **no
like-for-like comparison between them**. Re-running the rest of the configs under the new prompt is
what would restore one.


`score_stage3` compared `predicted[0]` against `gold[0]` and nothing else, so on
`vendor_gadgets_01` **31 of 91 priced gold products were ever checked - 34%**. 19 of its 34
product posts carry more than one product and one carries 15. It now scores every gold product,
aligned positionally, and reports the first-product metric beside it so figures recorded before
this date stay comparable.

Replayed through the corrected scorer from the cached `stage3_ocr` predictions - no model calls,
so this re-scores the exact output the original runs produced:

Under the pre-granularity prompt (see the re-run below for the current figures):

| | gadgets first-product | gadgets **all products** | autos first-product | autos **all products** |
|---|---|---|---|---|
| Price accuracy | 31/31 = 100% | **88/91 = 97%** | 19/19 = 100% | **24/24 = 100%** |
| Missing-price recall | 2/2 | **6/6** | 6/6 | **6/6** |
| Name similarity | 76% | 74% | 90% | 91% |
| Products returned / gold | | **90 / 97** | | 30 / 30 |
| Coverage of the legacy metric | 34% | | 79% | |

**Re-run 2026-09-24 with the granularity rule written down.** The four "under-extracting" posts
were not a model failure - the Stage 3 prompt said *"only return multiple products when the post
is genuinely offering different items for sale (different make/model, different trim, or
explicitly different price points)"* and told the model to collect options into `variants`. The
model followed it exactly; gold splits per purchasable variant regardless of price. That
convention had never been written down anywhere, which is the same failure as the `post_type`
values a stage earlier. It is now in `eval/LABEL_CODEBOOK.md` under "Product granularity" and in
the Stage 3 system prompt, and both say one row per purchasable variant even when every price is
null.

Runs `stage3_ocr_20260924T194202Z` (gadgets) and `stage3_ocr_20260924T194614Z` (autos), both
VALIDATED.

| gadgets | before | after |
|---|---|---|
| Price accuracy, all products | 88/91 = 97% | **89/91 = 98%** |
| Products returned / gold | 90 / 97 | **95 / 97** |
| Under-extracted posts | 4 | **1** |
| Name similarity, all products | 74% | **85%** |
| Name similarity, first product | 76% | **88%** |
| Missing-price recall, all products | 6/6 | 6/6 |

Autos is unmoved at 24/24, 30/30 products, name similarity 91→92% - it has only three
multi-variant posts and none of them collapsed, which is why it never showed the problem.

**The name-similarity jump is the clearest signal it was a spec disagreement.** 74% to 85% on
gadgets, from the same model on the same captions: gold names carry the variant
("Premium UK used iPhone 12 128GB") and now so do the predictions. A collapsed row could never
have matched that name however well it read the caption.

**One genuine miss survives.** `18094596503428757` still returns 3 of 5 Starlink products,
dropping *Starlink Hook* (N60,000) and *Hook & pipe* (N120,000) from the tail of a five-item
caption. No variants are involved - these are separate accessories, simply omitted. That is the
one real extraction failure the corrected scorer found, and it is unfixed.

**Before the granularity fix, gadgets read 97% and looked like three price errors:**

| post | product | gold price |
|---|---|---|
| `18115331426067887` | Samsung Galaxy Tab S10 FE 256GB 5G | ₦900,000 |
| `18094596503428757` | Starlink Hook | ₦60,000 |
| `18094596503428757` | Starlink Hook & pipe | ₦120,000 |

**Under-extraction was the failure mode, and it was completely invisible.** Four of 19
multi-product gadgets posts returned fewer products than gold - 2→1, 3→1, 3→1, 5→3 - and every
one scored 100%, because `products[0]` was right in all four. Autos showed none of it: 30 gold,
30 returned, which is why the vendor that tops out at 3 products per post hid the problem,
exactly as it hid the `max_tokens` truncation. Three of the four were the spec disagreement
described above and are now fixed; the fourth is the Starlink omission.

**Missing-price recall holds at 6/6 across all products** on both vendors, so nothing was
invented on the products the metric could not previously see. A gold product with no price that
the model never returned counts as a hit, not a hallucination - the brief's requirement is "never
invent a price", and under-extraction is counted separately rather than folded in.

Seven tests in `tests/test_harness.py` pin this, including that a hallucinated price on product 2
now fails loudly - it used to be silent.
