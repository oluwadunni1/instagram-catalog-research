"""Offline tests for the Jev Stage 2 binary triage.

No network, no .env, no golden set - the HTTP layer is mocked throughout.
Model quality is the harness's job; these cover the plumbing the harness
structurally cannot see: threshold mapping, the confidence a noul does not
return, retry on a transient status, and the reserved-parameter trap.
"""

from __future__ import annotations

import pytest

from pipeline import jev_client
from pipeline.stages import stage2_triage_jev


class FakeResponse:
    def __init__(self, status_code: int = 200, body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict:
        return self._body


def jev_body(p_yes: float, model: str = "jev-1.13.0") -> dict:
    return {
        "model": model,
        "answers": {stage2_triage_jev.QUESTION_ID: {"type": "noul", "noul": p_yes}},
        "usage": {"input_tokens": 400, "output_tokens": 12},
    }


@pytest.fixture(autouse=True)
def _no_key_lookup_or_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read a real .env, never actually pace or back off in tests."""
    monkeypatch.setattr(jev_client, "get_typesafe_api_key",
                        lambda *a, **k: "ts-FAKEtestkeyDONOTUSE")  # pragma: allowlist secret
    monkeypatch.setattr(jev_client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(jev_client, "throttle", lambda: None)


def post(caption: str = "2024 Toyota Hilux, brand new") -> dict:
    return {"post_id": "p1", "caption": caption}


# --- threshold mapping -----------------------------------------------------

@pytest.mark.parametrize("p_yes, expected", [
    (0.99, "product_listing"),
    (0.51, "product_listing"),
    (0.50, "product_listing"),   # at the boundary: >= threshold is a listing
    (0.49, "not_product"),
    (0.01, "not_product"),
])
def test_threshold_maps_probability_to_label(
    monkeypatch: pytest.MonkeyPatch, p_yes: float, expected: str
) -> None:
    monkeypatch.setattr(jev_client.requests, "post",
                        lambda *a, **k: FakeResponse(body=jev_body(p_yes)))
    result = stage2_triage_jev.triage_post_jev(post())
    assert result["post_type"] == expected


def test_threshold_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The threshold is a tunable, not a constant - calibration work sweeps it."""
    monkeypatch.setattr(jev_client.requests, "post",
                        lambda *a, **k: FakeResponse(body=jev_body(0.7)))
    assert stage2_triage_jev.triage_post_jev(post(), threshold=0.9)["post_type"] == "not_product"
    assert stage2_triage_jev.triage_post_jev(post(), threshold=0.6)["post_type"] == "product_listing"


# --- confidence ------------------------------------------------------------

@pytest.mark.parametrize("p_yes, expected_confidence", [
    (0.95, 0.95),
    (0.05, 0.95),   # confidently NOT a product is still confident
    (0.51, 0.51),   # a coin flip must not read as certain
])
def test_confidence_is_distance_from_the_boundary(
    monkeypatch: pytest.MonkeyPatch, p_yes: float, expected_confidence: float
) -> None:
    """A noul returns P(yes) and NO confidence field, unlike choice and score.
    max(p, 1-p) is the honest analogue: both 0.95 and 0.05 are confident
    answers, and 0.51 is not."""
    monkeypatch.setattr(jev_client.requests, "post",
                        lambda *a, **k: FakeResponse(body=jev_body(p_yes)))
    result = stage2_triage_jev.triage_post_jev(post())
    assert result["confidence"] == pytest.approx(expected_confidence)


def test_escalated_is_always_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jev has no vision path, so the honest comparison is against the Gemini
    stage's Pass A line rather than its overall figure."""
    monkeypatch.setattr(jev_client.requests, "post",
                        lambda *a, **k: FakeResponse(body=jev_body(0.9)))
    assert stage2_triage_jev.triage_post_jev(post())["escalated"] is False


# --- the profile must reach the model --------------------------------------

def test_profile_is_sent_in_the_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Gemini stage gets the Stage 1 profile. Withholding it here would
    repeat the failure that invalidated every figure before 2026-09-09, where
    the harness and the chained path fed one stage different inputs."""
    captured: dict = {}

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        captured.update(kwargs.get("json") or {})
        return FakeResponse(body=jev_body(0.9))

    monkeypatch.setattr(jev_client.requests, "post", fake_post)
    stage2_triage_jev.triage_post_jev(post(), profile={"business_category": "automotive"})
    assert captured["state"]["account_profile"] == {"business_category": "automotive"}
    assert captured["state"]["caption"]


# --- transient retry -------------------------------------------------------

def test_retries_on_503_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real probe hit 503 Service Unavailable and succeeded on a plain retry.
    Without backoff a harness run dies on a blip."""
    calls = {"n": 0}

    def flaky(*a: object, **k: object) -> FakeResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(status_code=503, text="high demand")
        return FakeResponse(body=jev_body(0.88))

    monkeypatch.setattr(jev_client.requests, "post", flaky)
    result = stage2_triage_jev.triage_post_jev(post())
    assert calls["n"] == 2
    assert result["post_type"] == "product_listing"


def test_permanent_4xx_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 400 means the request is wrong. Retrying an identical wrong request
    only burns budget."""
    calls = {"n": 0}

    def bad_request(*a: object, **k: object) -> FakeResponse:
        calls["n"] += 1
        return FakeResponse(status_code=400, text="bad question type")

    monkeypatch.setattr(jev_client.requests, "post", bad_request)
    with pytest.raises(jev_client.JevError):
        stage2_triage_jev.triage_post_jev(post())
    assert calls["n"] == 1


def test_missing_key_raises_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jev_client, "get_typesafe_api_key", lambda *a, **k: None)

    def must_not_be_called(*a: object, **k: object) -> FakeResponse:
        raise AssertionError("no request may be sent without a key")

    monkeypatch.setattr(jev_client.requests, "post", must_not_be_called)
    with pytest.raises(jev_client.JevError, match="TYPESAFE_API_KEY"):
        stage2_triage_jev.triage_post_jev(post())


# --- the reserved-parameter trap -------------------------------------------

def test_stage_does_not_declare_a_reserved_parameter() -> None:
    """load_stage_fn() reserves and strips `model`, so a stage declaring it
    silently runs its own default - the bug that ran a four-model Stage 4
    comparison on one model. The harness raises on this now; this pins the
    Jev stage specifically, since its config carries jev_model."""
    import inspect
    params = set(inspect.signature(stage2_triage_jev.triage_post_jev).parameters)
    assert "model" not in params
    assert "jev_model" in params


def test_token_log_records_the_resolved_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """jev-latest resolves server-side to a versioned model. The token log must
    record what answered, not what was asked for, or --expect-model is
    meaningless."""
    logged: dict = {}
    monkeypatch.setattr(jev_client.requests, "post",
                        lambda *a, **k: FakeResponse(body=jev_body(0.9, model="jev-1.13.0")))
    monkeypatch.setattr(jev_client, "log_token_usage", lambda **kw: logged.update(kw))

    stage2_triage_jev.triage_post_jev(post(), jev_model="jev-latest",
                                       run_id="r1", vendor_id="v1")
    assert logged["model"] == "jev-1.13.0"
    assert logged["prompt_tokens"] == 400
    assert logged["run_id"] == "r1"


def test_nothing_is_logged_without_a_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same rule as complete_structured(): a one-off probe must not pollute
    report/token_log.csv, which verify_run.py gates on."""
    calls = {"n": 0}
    monkeypatch.setattr(jev_client.requests, "post",
                        lambda *a, **k: FakeResponse(body=jev_body(0.9)))
    monkeypatch.setattr(jev_client, "log_token_usage",
                        lambda **kw: calls.__setitem__("n", calls["n"] + 1))

    stage2_triage_jev.triage_post_jev(post())
    assert calls["n"] == 0


# --- images ----------------------------------------------------------------
# Jev accepts images behind a per-request header, within a 280-1568 px window
# per side (docs.typesafe.ai/images). These pin the request shape, the
# resizing that keeps an Instagram photo inside the window, and which calls
# each image_policy makes.

def _jpeg(width: int, height: int) -> bytes:
    from io import BytesIO

    from PIL import Image

    out = BytesIO()
    Image.new("RGB", (width, height), "red").save(out, "JPEG")
    return out.getvalue()


def test_assets_are_sent_with_the_images_header(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict] = []

    def fake_post(url, headers, json, timeout):
        sent.append({"headers": headers, "json": json})
        return FakeResponse(body=jev_body(0.9))

    monkeypatch.setattr(jev_client.requests, "post", fake_post)
    asset = {"type": "image", "format": "jpg", "data_base64": "AAAA"}
    jev_client.decide(state={"image": "img-1"}, questions={}, assets={"img-1": asset})
    jev_client.decide(state="text only", questions={})

    assert sent[0]["headers"][jev_client.IMAGES_HEADER] == "true"
    assert sent[0]["json"]["assets"] == {"img-1": asset}
    assert jev_client.IMAGES_HEADER not in sent[1]["headers"]
    assert "assets" not in sent[1]["json"]


@pytest.mark.parametrize("size", [(100, 50), (720, 1280), (4000, 3000), (2000, 150)])
def test_prepared_image_fits_the_size_window(size: tuple[int, int]) -> None:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(jev_client.prepare_image(_jpeg(*size)))) as img:
        assert img.format == "JPEG"
        assert jev_client.IMAGE_MIN_SIDE <= img.width <= jev_client.IMAGE_MAX_SIDE
        assert jev_client.IMAGE_MIN_SIDE <= img.height <= jev_client.IMAGE_MAX_SIDE


def test_an_image_that_cannot_be_fetched_fails_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silently answering on text alone would hide that the photo was never seen."""
    def broken_get(*a, **k):
        raise jev_client.requests.ConnectionError("CDN link expired")

    monkeypatch.setattr(jev_client.requests, "get", broken_get)
    with pytest.raises(jev_client.JevError, match="could not prepare image"):
        jev_client.image_asset("https://cdn.example/expired.jpg")


@pytest.mark.parametrize("policy, caption, has_image, p_text, expected_calls, escalated", [
    ("never", "Hilux for sale", True, 0.55, ["text"], False),
    ("always", "Hilux for sale", True, 0.99, ["image"], False),
    ("always", "Hilux for sale", False, 0.99, ["text"], False),
    ("escalate", "Hilux for sale", True, 0.95, ["text"], False),
    ("escalate", "Hilux for sale", True, 0.55, ["text", "image"], True),
    ("escalate", "", True, 0.55, ["image"], True),
    ("escalate", "Hilux for sale", False, 0.55, ["text"], False),
])
def test_image_policy_decides_which_calls_are_made(
    monkeypatch: pytest.MonkeyPatch, policy: str, caption: str, has_image: bool,
    p_text: float, expected_calls: list[str], escalated: bool,
) -> None:
    calls: list[str] = []

    def fake_post(url, headers, json, timeout):
        with_image = "assets" in json
        calls.append("image" if with_image else "text")
        return FakeResponse(body=jev_body(0.97 if with_image else p_text))

    monkeypatch.setattr(jev_client.requests, "post", fake_post)
    monkeypatch.setattr(stage2_triage_jev, "image_asset",
                        lambda url: ("img-ref", {"type": "image", "format": "jpg", "data_base64": "AA"}))
    p = {"post_id": "p1", "caption": caption}
    if has_image:
        p["media_url"] = "https://cdn.example/p1.jpg"

    result = stage2_triage_jev.triage_post_jev(p, image_policy=policy)

    assert calls == expected_calls
    assert result["escalated"] is escalated


def test_unknown_image_policy_is_refused() -> None:
    with pytest.raises(ValueError, match="image_policy"):
        stage2_triage_jev.triage_post_jev(post(), image_policy="sometimes")
