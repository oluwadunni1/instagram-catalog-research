"""What a golden-set writer copies out of a raw dump's comments.

eval/golden/ is committed to the public repo, and docs/privacy.html promises
other people's comment text never goes there. Since the Meta app went Live on
2026-10-17, a raw dump carries it, so every golden-set writer keeps only the
vendor's own comments. The chained pipeline still gets all of them, because
Stage 4 reads buyers' comments. Offline: a hand-built post, no dump, no golden set.
"""

from __future__ import annotations

from make_golden_skeleton import build_comments_list

VENDOR = "vendor.handle"
POST = {
    "id": "1",
    "comments": [
        {"id": "11", "username": "a.buyer", "text": "how much?"},
        {"id": "12", "username": VENDOR, "text": "sold, thanks"},
        {"id": "13", "username": None, "text": "is it still available"},
    ],
}


def test_the_pipeline_still_gets_every_comment():
    rows = build_comments_list(POST, VENDOR)
    assert [r["text"] for r in rows] == ["how much?", "sold, thanks", "is it still available"]
    assert [r["is_vendor_reply"] for r in rows] == [False, True, False]


def test_a_golden_set_gets_only_the_vendors_own():
    rows = build_comments_list(POST, VENDOR, vendor_only=True)
    assert rows == [{"username": VENDOR, "text": "sold, thanks", "is_vendor_reply": True}]


def test_a_comment_with_no_username_is_never_taken_for_the_vendors():
    """Meta sometimes returns a comment's text without its username."""
    rows = build_comments_list(POST, VENDOR, vendor_only=True)
    assert all(r["username"] == VENDOR for r in rows)


def test_without_a_vendor_handle_a_golden_set_gets_no_comments():
    assert build_comments_list(POST, None, vendor_only=True) == []
