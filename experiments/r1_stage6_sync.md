# r1_stage6_sync: measuring an incremental sync

**Round 1, 2026-09-22 to 2026-09-24.** Stage 6 against a live baseline and a designed change set.
Moved out of the root `README.md` on 2026-10-16, unchanged apart from heading levels and cross-references. Every figure here comes from a run that passed `make verify`, and every run id is in the frozen v1 log, `report/token_log.csv`.

**Verdict:** 47/47 per-post agreement and 100% change-classification precision on 23 changed posts. Posts reprocessed fell by 56%, and re-syncing an unchanged account made zero AI calls. Only `caption_edit` clears n >= 10, and three of the five cells are close to tautological.

A re-sync finds most of a feed unchanged. The diff - content hash, then perceptual image hash,
then caption embedding - makes zero generative calls. An unchanged post costs nothing at all; a
new or caption-edited one costs a single `gemini-embedding-001` call, which the token log now
records (it did not until 2026-09-22, see below).

## The measured run - 2026-09-24

The first Stage 6 measurement against a **designed** change set: a live baseline was taken, the
vendor account was then changed deliberately, and the ground truth was written to
`eval/sync_labels/oluwadunnioluajayi_2026-09-24_truth.json` **before the sync ran**. Labels are
known by construction rather than guessed, which is what the brief's change-classification target
needs and what no earlier run had.

Baseline `snapshot_vendor_gadgets_01_20260922T205835Z` (41 posts, live-derived).
Syncs `sync_vendor_gadgets_01_20260924T181425Z` and `...T181627Z`.

| expected | predicted | n |
|---|---|---|
| `caption_edit` | `caption_edit` | 15 |
| `no_op` | `no_op` | 24 |
| `new_post` | `new_post` | 5 |
| `deleted` | `deleted` | 2 |
| `repost_merge` | `repost_merge` | 1 |

**47/47 per-post agreement. No disagreements, and nothing reported that the truth file did not
list.** Change-classification precision is therefore **100%, denominator 23** counting only the
changed posts, or 47 including the no-op denominator. Against the brief's >= 85% target: met, on
this change set.

**The reduction, re-derived on real data.** 20 of 45 posts need Stages 2-5 re-run
(`posts_needing_work()`): the 15 edits and the 5 new posts. The merge, the deletions and every
no-op cost nothing.

| | Full feed | Incremental |
|---|---|---|
| Posts processed | 45 | **20** |
| Reduction | | **56%** |

**This supersedes the 87% below.** That figure came from a golden-derived baseline which
re-discovered posts the feed already had, so it counted work it had invented. 56% is the first
figure measured against a live baseline with the corrected content hash.

**The second sync is the result that had never been demonstrated.** Run immediately after the
first with nothing changed on Instagram, it reported **zero changes, 45 no-ops, zero vendor
actions and zero `stage6_embedding` rows** - against 19 embedding rows for the sync before it.
That is the "a sync on an unchanged account costs about $0" claim, checkable in
`report/token_log.csv` for the first time rather than asserted.

It is also the proof that the three defects fixed on 2026-09-22/23 are dead on live data. Before
them this second sync would have re-reported both deletions, flip-flopped the merge, and
re-reported the caption edit on the superseded post.

**What this run does not establish.**

- **Only `caption_edit` (n=15) clears the codebook's n >= 10 bar.** `repost_merge` is n=1 and
  `deleted` is n=2 - anecdotes, per `eval/LABEL_CODEBOOK.md`'s own rule.
- **`caption_edit`, `no_op` and `deleted` are close to tautological.** Those labels were derived
  by exact string comparison and set arithmetic between the two dumps, and Stage 6 decides them
  the same way, so agreement was near-guaranteed. Deriving them that way is what kept the ground
  truth uncontaminated, but it means the informative results here are the single `repost_merge`
  and the silent second sync, not the 100%.
- **Four change types never fired.** `comment_delta` (comments cannot be added to this account),
  `repost_match` by pHash (no lightly-edited re-upload was posted - the nearest non-exact
  distance was 18), `repost_match` by embedding, and `out_of_stock` (no SOLD repost).
- **pHash only catches literal file reuse.** Of six new posts, one was a re-uploaded saved image
  and matched at distance 0; the other five measured 18-26 against every baseline post *and*
  every carousel child slide. A vendor who reposts a product with a fresh photo is invisible to
  the pHash path.

## The earlier golden-derived run - superseded

A live sync of 40 posts against a 30-post baseline: 18 no-ops, 4 new, 2 caption edits, 1 comment
delta, 4 repost matches (2 pHash, 2 embedding), 2 exact duplicates auto-merged.

| | Full feed | Incremental |
|---|---|---|
| Posts processed | 40 | **6** |
| Model calls | 101 | **13** |
| Tokens | 116,201 | **14,288** |
| Reduction | | **87%** |

Excluding `content_changed` from reprocessing is the load-bearing choice: 10 of 23 changes were
that type (hash moved, caption and comment count did not). Including them would reprocess 16 of
40 posts instead of 6. Three tests pin this.

The 0% attention rate across those 6 posts is a 6-post denominator, not a quality signal.

## What changed on 2026-09-22

Five structural defects, each of which would have silently corrupted a sync measurement:

| | was | now |
|---|---|---|
| Baseline source | built from the golden set, so a sync re-discovered posts the feed already had | `--dump`/`--account` builds it from a live raw dump; `--golden` kept for the original run |
| Content hash | `caption \| comment_count \| len(comments)` | `caption \| comment_count` |
| Comment counts | `comments_count` was never requested at ingest, so a dump-built baseline recorded 0 for every post | requested and threaded through; the builder warns loudly on a dump that predates it |
| Snapshot writes | `latest.json` overwritten in place, so no sync was repeatable | previous trio archived to `data/snapshots/<handle>/history/<sync_timestamp>/` first |
| Embedding calls | neither logged nor throttled; **zero** rows in the token log | `throttle()` + a `stage6_embedding` row per call |

Two more on 2026-09-23, both of the same shape - a state the sync leaves behind that makes the
*next* sync wrong, and both invisible in the single sync every test and every figure had ever
looked at:

| | was | now |
|---|---|---|
| A deleted post | kept as `archived`, but still absent from every later fetch, so it landed in `deleted_ids` again and re-reported **every sync forever**, each time with `requires_vendor_action` | `archived` means "already reported"; the record is carried forward silently |
| An exact repost | dropped the original from the snapshot while it was still live on Instagram, so the next sync saw it as new, matched it at distance 0 and merged back - **flip-flopping forever** | the original is kept as a `superseded` tombstone with no `catalog_products`, so it diffs as the no-op it is |

Both were found by running the same sync three times against a stubbed fetch, which nothing had
done before. A `superseded` or `archived` entry is also excluded from pHash and embedding
matching, so a later repost resolves against the live post rather than a tombstone.

**These two are why the "a sync on an unchanged account costs about $0" claim could not have
held in practice.** Any account that had ever had a deletion or an exact repost reported a
change on every subsequent sync, forever. Six tests in `tests/test_stage6_sync.py` now drive
`run_sync` repeatedly and pin that each event reports exactly once.

`report/changes.json` was a fixed path that collided across vendors and across runs; the default
is now `report/<vendor_handle>/changes_<sync_timestamp>.json`. `run_sync()` takes
`vendor_handle`/`snapshot_dir` as parameters instead of reading module globals, which is what
lets `scripts/simulate_stage6.py` simulate any vendor rather than only the default one.

**Those `content_changed` entries were not CDN churn, and the numbers above predate the fix.**
The README previously attributed them to rotating `oh=`/`oe=` params, which cannot be right -
`compute_content_hash()` has always excluded `media_url` for exactly that reason. The real cause
was the hash's third component, `len(comments)`: the baseline was built from a golden set whose
comments are hand-authored, while a live fetch always returns an empty array (Meta withholds the
text). Same post, different hash, on every sync forever. The component is gone as of
`pipeline/stages/stage6_sync.py`, which **invalidates every `content_hash` written before it** -
the stored snapshots have been rehashed in place and the pre-change copies kept under
`data/snapshots/<handle>/history/pre-hash-change/`. Offline proof, via `make simulate-sync`
against the same baseline before and after: `other_content_changes` 2 -> 0, `no_ops` 33 -> 35,
every other count identical, and the two posts that moved are precisely the two the baseline
records comments for. **The 87% reduction has since been re-derived** on a live baseline with the fixed hash and comes
out at 56% - see the measured run above.
