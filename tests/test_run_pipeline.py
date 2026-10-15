"""End-to-end runner tests.

`scripts/run_pipeline.py` is the one stage path the eval harness cannot check:
the harness scores stages independently against gold, while the runner chains
them on live predictions with no golden set at all. Nothing else verifies its
deterministic normalization step, which fails silently when wrong - a bad
normalization hands every stage an empty caption rather than raising.

read_run_usage() used to live here too; it moved next to its writer in
pipeline/llm_client.py so the gate (scripts/verify_run.py) and the runner read
the token log through one function. Its tests moved with it, to
tests/test_token_log.py.

Offline: no network, no .env, no golden set.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# scripts/ is not a package and run_pipeline.py is written to be run as a
# script, so load it by path - same as tests/test_scan_secrets.py. It imports
# harness and make_golden_skeleton, which conftest.py has already put on the
# path via REPO_ROOT/eval.
_spec = importlib.util.spec_from_file_location(
    "run_pipeline", REPO_ROOT / "scripts" / "run_pipeline.py"
)
run_pipeline = importlib.util.module_from_spec(_spec)
sys.modules["run_pipeline"] = run_pipeline
_spec.loader.exec_module(run_pipeline)


# --- normalize_post --------------------------------------------------------

def test_normalize_post_renames_id_to_post_id() -> None:
    """Raw dumps key by "id", every stage reads "post_id" (CLAUDE.md). Getting
    this wrong makes each stage see an empty post rather than raising."""
    post = run_pipeline.normalize_post({"id": "123", "caption": "hi"}, "vendor")

    assert post["post_id"] == "123"
    assert post["caption"] == "hi"


def test_normalize_post_keeps_thumbnail_url_for_a_reel() -> None:
    """A Reel's media_url is the .mp4; thumbnail_url must survive
    normalization or vision_image_url() downstream has nothing to prefer."""
    post = run_pipeline.normalize_post(
        {
            "id": "r1",
            "media_type": "VIDEO",
            "media_product_type": "REELS",
            "media_url": "https://cdn.example/clip.mp4",
            "thumbnail_url": "https://cdn.example/cover.jpg",
        },
        "vendor",
    )

    assert post["thumbnail_url"] == "https://cdn.example/cover.jpg"
    assert post["media_url"] == "https://cdn.example/clip.mp4"


def test_normalize_post_flags_the_vendors_own_comments() -> None:
    """Stage 4's [VENDOR]/[BUYER] labeling depends on this flag (and on a
    profile reaching the stage, which both this runner and the harness now
    do - see tests/test_harness.py)."""
    post = run_pipeline.normalize_post(
        {
            "id": "c1",
            "comments": [
                {"username": "buyer_one", "text": "how much?"},
                {"username": "the_vendor", "text": "sold"},
            ],
        },
        "the_vendor",
    )

    assert post["comment_count"] == 2
    assert [c["is_vendor_reply"] for c in post["comments"]] == [False, True]


def test_normalize_post_defaults_missing_fields() -> None:
    """4 of 41 posts in a real dump carry no media_url, and 29 no
    thumbnail_url - absent fields must normalize to None, not KeyError."""
    post = run_pipeline.normalize_post({"id": "bare"}, None)

    assert post["caption"] == ""
    assert post["media_url"] is None
    assert post["thumbnail_url"] is None
    assert post["has_carousel_children"] is False
    assert post["comments"] == []


def test_normalize_post_detects_carousel_children() -> None:
    post = run_pipeline.normalize_post({"id": "car", "children": [{"id": "a"}]}, None)

    assert post["has_carousel_children"] is True


# --- Stage 6 -> pipeline handoff -------------------------------------------

def _changes(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "changes.json"
    path.write_text(json.dumps({"vendor": "v", "changes": entries}), encoding="utf-8")
    return path


def test_only_actionable_change_types_are_selected(tmp_path: Path) -> None:
    """The economic point of Stage 6: a re-sync finds most of the feed
    unchanged, and those posts must cost nothing. Selecting too broadly here
    silently reinstates the full-feed spend sync exists to avoid."""
    path = _changes(tmp_path, [
        {"post_id": "new1", "change_type": "new_post"},
        {"post_id": "edit1", "change_type": "caption_edit"},
        {"post_id": "cmt1", "change_type": "comment_delta"},
        {"post_id": "churn1", "change_type": "content_changed"},
        {"post_id": "rep1", "change_type": "repost_match"},
        {"post_id": "rep2", "change_type": "repost_merge"},
    ])

    assert run_pipeline.posts_needing_work(path) == {"new1", "edit1", "cmt1"}


def test_a_post_with_two_changes_is_selected_once(tmp_path: Path) -> None:
    """A post can be both caption-edited and comment-changed in one sync;
    it must not be processed twice."""
    path = _changes(tmp_path, [
        {"post_id": "p1", "change_type": "caption_edit"},
        {"post_id": "p1", "change_type": "comment_delta"},
    ])

    assert run_pipeline.posts_needing_work(path) == {"p1"}


def test_a_sync_with_nothing_to_do_selects_nothing(tmp_path: Path) -> None:
    """The common case for a routine re-sync - and the one where spending
    anything at all would be the bug."""
    path = _changes(tmp_path, [{"post_id": "p1", "change_type": "content_changed"}])

    assert run_pipeline.posts_needing_work(path) == set()


# --- per-bucket output files -----------------------------------------------

def _catalog(items: list[dict]) -> dict:
    return {"account_label": "vendor_demo", "items": items}


def test_every_bucket_file_is_written_even_when_empty(tmp_path: Path) -> None:
    """An empty bucket must still produce its file. These paths get printed and
    then opened by hand; a path that exists only when it happens to be non-empty
    is one someone reads as a broken run."""
    catalog = _catalog([{"post_id": "p1", "bucket": "auto_import", "flags": []}])

    written = run_pipeline.write_bucket_files(catalog, tmp_path / "catalog.json")

    assert set(written) == set(run_pipeline.BUCKETS)
    for bucket, path in written.items():
        assert path.exists(), f"{bucket} file missing"
    assert json.loads(written["needs_attention"].read_text(encoding="utf-8")) == []


def test_items_are_partitioned_with_their_flags_intact(tmp_path: Path) -> None:
    """The flags are the reason a post landed in needs_attention, so they are
    the whole point of splitting the catalog up - a reviewer opens the file and
    reads why without cross-referencing anything."""
    catalog = _catalog([
        {"post_id": "keep", "bucket": "auto_import", "flags": []},
        {"post_id": "check", "bucket": "needs_attention", "flags": ["Missing price"]},
        {"post_id": "skip", "bucket": "auto_exclude", "flags": []},
    ])

    written = run_pipeline.write_bucket_files(catalog, tmp_path / "catalog.json")
    attention = json.loads(written["needs_attention"].read_text(encoding="utf-8"))

    assert [item["post_id"] for item in attention] == ["check"]
    assert attention[0]["flags"] == ["Missing price"]
    assert len(json.loads(written["auto_import"].read_text(encoding="utf-8"))) == 1


def test_bucket_files_land_beside_the_catalog(tmp_path: Path) -> None:
    """--out moves the catalog; the buckets have to follow it rather than
    scattering back into runs/<account>/."""
    written = run_pipeline.write_bucket_files(_catalog([]), tmp_path / "nested" / "catalog.json")

    assert written["auto_import"].parent == tmp_path / "nested" / "buckets"


# --- per-post stage failures -----------------------------------------------

def _raise(*_args, **_kwargs):
    raise RuntimeError("provider down")


def _chained_run(tmp_path: Path, monkeypatch, triage, signals=None) -> dict:
    """run_pipeline() over a two-post dump with stub stages: no network, no
    .env, no model. Only the wiring between the stage loops and Stage 5 runs."""
    from types import SimpleNamespace

    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps({
        "profile": {"username": "the_vendor"},
        "media": [{"id": "bad", "caption": "Phone 25k"}, {"id": "good", "caption": "Laptop 90k"}],
    }), encoding="utf-8")
    stages = {
        "stage2_triage": triage,
        "stage3_extract": lambda post, profile: [{
            "name": "Thing", "price": {"value": 1, "source": "caption"},
            "extraction_confidence": 0.9,
        }],
        "stage4_signals": signals or (lambda post, profile: []),
    }
    monkeypatch.setattr(run_pipeline, "load_experiment_config", lambda path: SimpleNamespace(
        name="stub", stage1_profile=None, stage4_signals=object()))
    monkeypatch.setattr(run_pipeline, "latest_dump", lambda account: dump)
    monkeypatch.setattr(run_pipeline, "get_or_create_profile", lambda *a, **k: SimpleNamespace(
        model_dump=lambda: {"vendor_username": "the_vendor"}))
    monkeypatch.setattr(run_pipeline, "load_stage_fn",
                        lambda config, stage, **k: (stages[stage], stage, None))
    monkeypatch.setattr(run_pipeline, "read_run_usage", lambda run_id: {})

    catalog = run_pipeline.run_pipeline("vendor_demo", Path("stub.yaml"),
                                        out_path=tmp_path / "catalog.json")
    return {item["post_id"]: item for item in catalog["items"]}


def test_a_raising_stage2_lands_the_post_in_needs_attention(tmp_path: Path, monkeypatch) -> None:
    """The fail-s2drop bug: an errored Stage 2 used to become post_type
    "unknown" and route to auto_exclude, so a product vanished unflagged."""
    def triage(post, profile):
        if post["post_id"] == "bad":
            _raise()
        return {"post_type": "product_listing", "confidence": 0.9, "escalated": False}

    items = _chained_run(tmp_path, monkeypatch, triage)

    assert items["bad"]["bucket"] == "needs_attention"
    assert items["bad"]["failed_stages"] == ["stage2"]
    assert items["good"]["bucket"] == "auto_import"
    assert items["good"]["failed_stages"] == []


def test_a_raising_stage4_blocks_auto_import(tmp_path: Path, monkeypatch) -> None:
    """A Stage 4 error returns [], the same value as "no sold signal"."""
    def triage(post, profile):
        return {"post_type": "product_listing", "confidence": 0.9, "escalated": False}

    def signals(post, profile):
        if post["post_id"] == "bad":
            _raise()
        return []

    items = _chained_run(tmp_path, monkeypatch, triage, signals)

    assert items["bad"]["bucket"] == "needs_attention"
    assert items["bad"]["failed_stages"] == ["stage4"]
    assert items["good"]["bucket"] == "auto_import"
