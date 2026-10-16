# r1_stage2_rebaseline: Stage 2 after the label codebook

**Round 1, 2026-09-21 to 2026-09-23.** Configs `stage2_jev.yaml`, `stage2_hybrid.yaml` and `family_gemini.yaml`, scored on product vs not-product.
Moved out of the root `README.md` on 2026-10-16, unchanged apart from heading levels and cross-references. Every figure here comes from a run that passed `make verify`, and every run id is in the frozen v1 log, `report/token_log.csv`.

**Verdict:** the Jev + Gemini hybrid scored 100% on autos and 95% on gadgets, at 100% precision. Gadgets has since been corrected twice in response to model behaviour, so it is no longer a held-out set for Stage 2. For Jev, [r2_jev](r2_jev.md) replaces this record: Jev now reads the photo itself.

Everything in this record is scored on **product vs not-product**, which is the brief's
actual target and what every downstream consumer reduces `post_type` to anyway: Stage 5, Stage 6
and `run_pipeline.py` all test only `!= "product_listing"`.

| | autos (tune) | gadgets (test) | precision | escalation |
|---|---|---|---|---|
| Jev alone (binary) | 28/30 = 93% | 33/41 = 80% | 100% | n/a, no vision |
| **Jev + Gemini hybrid** | **30/30 = 100%** | **38/41 = 93%** | **100%** | 20% / 15% |
| Gemini alone | 30/30 = 100% | see caveat | 97% | 1/30 |

Run ids: `stage2_jev_20260921T084428Z`, `stage2_jev_20260921T084716Z`,
`stage2_hybrid_20260921T091816Z`, `stage2_hybrid_20260921T092310Z`,
`family_gemini_20260921T081023Z`. All passed the gate.

**The hybrid** runs Jev as a text-only Pass A and escalates two ways. A low-confidence post with
a caption goes to Gemini text. In the runs above, a caption-less post went straight to Gemini
vision, skipping Jev since there was nothing to read. That changed later on 2026-09-21: a
caption-less post now goes to local OCR first, Jev re-runs on the OCR text, and only a post
where OCR reads nothing or Jev is still unsure reaches vision - the 2026-09-22 runs below made
zero vision calls. Jev alone loses mainly because five
gadgets posts have no caption at all and carry their price on the image, which a text-only model
cannot reach.

**Cost is where the funnel shows itself.** On gadgets the hybrid spent $0.0084 across 42 calls:

| | calls | cost | share |
|---|---|---|---|
| Jev | 36 | $0.000956 | 11% |
| Gemini vision | 5 | $0.007175 | **86%** |
| Gemini text | 1 | $0.000244 | 3% |

Five vision calls are 86% of the bill. The cheap first pass is close to free, so the lever worth
pulling is the escalation rate, not the Pass A model.

**Jev's confidence is better calibrated than Gemini's**, which is the property a
confidence-triggered cascade actually needs: separation between confidence-when-right and
confidence-when-wrong is +0.217 on autos and +0.113 on gadgets, against Gemini's +0.074.

**Caveat on Gemini alone for gadgets.** Not currently measurable. Two attempts on the new prompt
both failed the gate on provider 503s (36/41 and 34/41, with 1 and 3 errored posts). The 39/41
previously recorded is from the definition-free prompt and is not comparable.

**The codebook has a known defect, unfixed.** All three remaining hybrid misses on gadgets are
posts the codebook mis-specifies: two marked SOLD while still listing a price, and one
"available soon". The codebook says a completed sale is a `testimonial_repost`, which puts a
Stage 4 concern (is it still available) into a Stage 2 category (is this a product post). One of
those posts escalated to Gemini and **Gemini agreed with Jev**, so two independent models given
the same definitions disagree with the gold in the same direction. The autos labels and the
gadgets labels genuinely differ on this, and the codebook encoded the autos convention as
universal. Fixing it meant editing the clause, not the models.

**Changed 2026-09-21, and re-measured 2026-09-22.** `eval/LABEL_CODEBOOK.md`'s step 2 now tests
what a post is *about* rather than whether a sale happened, so a SOLD mark on a post whose
subject is still an item with its specs and price leaves it a `product_listing`. The wording is
carried in `pipeline/stages/stage2_triage.py`'s `POST_TYPE_DEFINITIONS` and
`scripts/smoke_jev.py`'s rubrics; all three changed together.

| `stage2_hybrid.yaml` | before | after | precision | escalation |
|---|---|---|---|---|
| autos (tune) | 30/30 = 100% | 30/30 = 100% | 100% | 7/30 = 23% |
| gadgets (test) | 38/41 = 93% | **39/41 = 95%** | 100% | 7/41 = 17% |

Run ids `stage2_hybrid_20260922T091356Z` and `stage2_hybrid_20260922T091520Z`, both VALIDATED,
and both still at **zero vision calls** - the Stage 2 OCR tier holds.

One of the three codebook misses is recovered and two remain; precision stays at 100% and autos
is unmoved, so the clause did not buy the gain by loosening something else. **The posts that
motivated the change are in the test half, so it was made with knowledge of the test set and
+1 on gadgets is not a clean held-out result.** Every Stage 2 figure recorded before 2026-09-22
predates the clause and is not comparable to one measured after it.

**Changed again on 2026-09-23, and this is the second time.** The 2026-09-22 clause said a post
is a `product_listing` even when it carries "a SOLD mark or an available-soon date". That second
half contradicted the rule directly below it - announcements cover "posts naming products that
are not purchasable yet" - and rule 2 is evaluated first, so the contradiction decided the
answer. The codebook itself was never ambiguous ("**Listing-shaped is not the same as
purchasable**"); only the two prompt copies were, and one gold label followed them.

`18024559289696773` ("iPhone Ultra Protective Case available soon ( Phone not included )") was
labeled `product_listing` with one product and is now `announcement` with none, matching
`18163097968466814`, the codebook's own documented trap for unreleased items. A sweep of both
golden sets for deferred-availability language found five posts and no other inconsistency: the
three that stay `product_listing` all carry a real offer ("Pre-order yours today", "has
arrived"), which is the dividing line now written into the codebook.

**What it costs, stated rather than buried.** The hybrid predicted `not_product` on that post at
0.9 confidence without escalating, so flipping the label turns a miss into a hit and gadgets
would read 40/41 rather than 39/41. It also drops Stage 3's scored set from 34 posts to 33 and
missing-price recall from 3/3 to 2/2. **No re-run has been made and no such figure is claimed
here.** More importantly: gadgets has now been adjusted twice in response to observed model
behaviour, so it is no longer a held-out set for Stage 2 in any useful sense, and its Stage 2
numbers should be read as "measured on a set we have twice corrected", not as generalisation.
The defensible part of this change is that a label contradicting its own codebook was fixed; the
accuracy gain that falls out of it is close to worthless as evidence.
