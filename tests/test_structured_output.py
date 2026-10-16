"""Structured-output mode tests for pipeline/llm_client.py.

Qwen 2.5 7B under json_object mode returned invented labels and keys on 44 of
71 first attempts in the September autos runs ("log", "sync_confidence",
{"post_type": "log", "data": {...}}), and two posts exhausted every retry in
both runs. json_schema mode sends the schema to the provider; these tests pin
what is sent, and what a retry tells the model, without calling one.

Offline: no network, no .env, no golden set.
"""

from __future__ import annotations

import csv
import json
from types import SimpleNamespace

import pytest

from pipeline import llm_client
from pipeline.llm_client import complete_structured, response_format_kwargs
from pipeline.stages.stage2_triage import TriageResult
from pipeline.stages.stage3_extract import ProductExtractionResult


def _objects(node):
    """Every JSON Schema object node that declares properties, at any depth."""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from _objects(value)


def test_json_object_mode_is_unchanged() -> None:
    """The default must send exactly what every run before 2026-10-15 sent,
    or every recorded figure stops being reproducible."""
    assert response_format_kwargs(TriageResult, "json_object", "openrouter/x") == {
        "response_format": {"type": "json_object"},
    }


def test_json_schema_mode_is_strict_at_every_level() -> None:
    """Strict structured outputs reject a schema with an optional or open
    object anywhere, including the nested product and price objects."""
    kwargs = response_format_kwargs(ProductExtractionResult, "json_schema", "gemini/x")
    schema = kwargs["response_format"]["json_schema"]["schema"]

    objects = list(_objects(schema))
    assert len(objects) > 1, "expected the nested product objects too"
    for node in objects:
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])


def test_json_schema_on_openrouter_requires_a_provider_that_honours_it() -> None:
    """Without require_parameters, OpenRouter may route to a provider that
    ignores response_format, and the run silently behaves like json_object."""
    on_openrouter = response_format_kwargs(TriageResult, "json_schema", "openrouter/qwen/x")
    elsewhere = response_format_kwargs(TriageResult, "json_schema", "gemini/x")

    assert on_openrouter["extra_body"] == {"provider": {"require_parameters": True}}
    assert "extra_body" not in elsewhere


def test_an_unknown_mode_is_refused() -> None:
    """A typo in a YAML must fail the run, not fall back to a mode the
    config does not name."""
    with pytest.raises(ValueError, match="structured_output"):
        response_format_kwargs(TriageResult, "json", "gemini/x")


def _fake_completion(monkeypatch: pytest.MonkeyPatch, replies: list[str]) -> list[dict]:
    calls: list[dict] = []
    stream = iter(replies)

    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=next(stream)))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10, total_tokens=110),
            provider="Phala",
        )

    monkeypatch.setattr(llm_client.litellm, "completion", completion)
    monkeypatch.setattr(llm_client, "throttle", lambda: None)
    return calls


def test_the_configured_mode_reaches_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_completion(monkeypatch, [json.dumps({"post_type": "announcement",
                                                       "confidence": 0.8})])

    complete_structured(model="openrouter/qwen/x", system_prompt="s", user_prompt="u",
                        schema=TriageResult, structured_output="json_schema")

    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[0]["extra_body"] == {"provider": {"require_parameters": True}}


def test_a_retry_names_the_allowed_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real failure: Qwen answered post_type "log". The retry used to say
    which fields exist, not which values post_type may take."""
    calls = _fake_completion(monkeypatch, [
        json.dumps({"post_type": "log", "data": {"confidence": 0.9}}),
        json.dumps({"post_type": "announcement", "confidence": 0.8}),
    ])

    result = complete_structured(model="m", system_prompt="s", user_prompt="u",
                                 schema=TriageResult)

    correction = calls[1]["messages"][-1]["content"]
    assert "post_type: one of product_listing, announcement" in correction
    assert "post_type: Input should be" in correction and "'log'" in correction
    assert "errors.pydantic.dev" not in correction
    assert result.post_type == "announcement"


def test_every_attempt_is_logged_with_its_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """The smoke run r2_family_qwen_20261015T134852Z logged 11 rows while
    paying for at least 17 calls: the runaway first attempts were invisible.
    And September's failures could not be traced to a provider at all."""
    _fake_completion(monkeypatch, [
        json.dumps({"post_type": "log"}),
        json.dumps({"post_type": "announcement", "confidence": 0.8}),
    ])

    complete_structured(model="m", system_prompt="s", user_prompt="u", schema=TriageResult,
                        run_id="r", vendor_id="v", post_id="p")

    with llm_client.TOKEN_LOG_PATH.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [(r["attempt"], r["outcome"], r["provider"]) for r in rows] == [
        ("1", "invalid", "Phala"), ("2", "ok", "Phala"),
    ]


def test_a_pass_b_response_that_fails_to_parse_is_still_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass B builds its own request, so it needs its own guarantee."""
    from pipeline.stages import stage2_triage

    _fake_completion(monkeypatch, ["not json"])
    monkeypatch.setattr(stage2_triage, "throttle", lambda: None)
    monkeypatch.setattr(stage2_triage.litellm, "completion", llm_client.litellm.completion)
    post = {"post_id": "p", "caption": "", "media_url": "https://cdn.example/p.jpg"}

    with pytest.raises(json.JSONDecodeError):
        stage2_triage._pass_b(post, None, "m", "r", "v", "p")

    with llm_client.TOKEN_LOG_PATH.open(encoding="utf-8", newline="") as f:
        (row,) = csv.DictReader(f)
    assert (row["stage"], row["outcome"], row["provider"]) == ("stage2_triage_pass_b", "invalid", "Phala")


# --- reasoning -------------------------------------------------------------

def test_reasoning_off_disables_it_on_openrouter() -> None:
    from pipeline.llm_client import request_kwargs

    assert request_kwargs(TriageResult, "json_object", "openrouter/qwen/x", "off")["extra_body"] == {
        "reasoning": {"enabled": False},
    }
    assert request_kwargs(TriageResult, "json_object", "openrouter/qwen/x", "low")["extra_body"] == {
        "reasoning": {"effort": "low"},
    }


def test_reasoning_merges_with_the_json_schema_provider_requirement() -> None:
    """Both settings live in OpenRouter's extra_body; one must not replace
    the other."""
    from pipeline.llm_client import request_kwargs

    extra = request_kwargs(TriageResult, "json_schema", "openrouter/qwen/x", "off")["extra_body"]

    assert extra == {"provider": {"require_parameters": True}, "reasoning": {"enabled": False}}


def test_no_reasoning_key_sends_nothing() -> None:
    """Absent means the provider default - what every earlier run got - so
    old configs reproduce."""
    from pipeline.llm_client import request_kwargs

    assert "extra_body" not in request_kwargs(TriageResult, "json_object", "openrouter/qwen/x")


@pytest.mark.parametrize("model, setting", [("gemini/x", "off"), ("openrouter/qwen/x", "none")])
def test_a_reasoning_setting_that_would_not_apply_is_refused(model: str, setting: str) -> None:
    """A config that says "off" must never run with reasoning on because the
    route ignored the key, or because the value was misspelled."""
    from pipeline.llm_client import request_kwargs

    with pytest.raises(ValueError, match="reasoning"):
        request_kwargs(TriageResult, "json_object", model, setting)


def test_stage3_hands_both_settings_to_every_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage 3 has a text pass, an OCR retry and a vision pass, and the OCR
    retry was once missed when a setting was threaded through. A product
    with no name escalates through all three."""
    from pipeline.stages import stage3_extract

    seen: list[tuple] = []

    unfinished = stage3_extract.ProductExtractionResult(
        products=[stage3_extract.ProductItem(price=stage3_extract.PriceModel(source="none"))]
    )

    def fake_text(**kwargs):
        seen.append((kwargs["stage_name"], kwargs["structured_output"], kwargs["reasoning"]))
        return unfinished

    def fake_vision(post, user_prompt, vision_model, run_id, vendor_id, post_id,
                    structured_output, reasoning):
        seen.append(("stage3_extract_pass_b", structured_output, reasoning))
        return unfinished

    monkeypatch.setattr(stage3_extract, "complete_structured", fake_text)
    monkeypatch.setattr(stage3_extract, "_pass_b", fake_vision)
    monkeypatch.setattr(stage3_extract, "extract_text", lambda *a, **k: ("CAMRY 25,000,000", "ok"))
    post = {"post_id": "p", "caption": "Toyota Camry for sale", "media_url": "https://cdn.example/p.jpg"}

    stage3_extract.extract_product(post, None, text_model="openrouter/qwen/x",
                                   vision_model="openrouter/qwen/v", use_ocr=True,
                                   structured_output="json_schema", reasoning="off",
                                   vision_reasoning="low")

    assert [entry[0] for entry in seen] == [
        "stage3_extract_pass_a", "stage3_extract_pass_a_ocr", "stage3_extract_pass_b",
    ]
    assert [entry[1:] for entry in seen] == [
        ("json_schema", "off"), ("json_schema", "off"), ("json_schema", "low"),
    ]


def test_vision_structured_output_overrides_pass_b_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Llama 3.1 8B's cheap providers take only json_object and Llama 4 Scout's
    only json_schema, so one mode for both passes left Scout with no provider.
    The vision mode must reach Pass B alone, and default to the text mode."""
    from pipeline.stages import stage2_triage, stage3_extract

    seen: list[tuple] = []

    def fake_s2_text(post, profile, model, run_id, vendor_id, post_id, structured_output, reasoning):
        seen.append(("s2a", structured_output))
        return stage2_triage.TriageResult(post_type="product_listing", confidence=0.1)

    def fake_s2_vision(post, profile, model, run_id, vendor_id, post_id, structured_output, reasoning):
        seen.append(("s2b", structured_output))
        return stage2_triage.TriageResult(post_type="product_listing", confidence=0.9)

    unfinished = stage3_extract.ProductExtractionResult(
        products=[stage3_extract.ProductItem(price=stage3_extract.PriceModel(source="none"))]
    )

    def fake_s3_text(**kwargs):
        seen.append(("s3a", kwargs["structured_output"]))
        return unfinished

    def fake_s3_vision(post, user_prompt, vision_model, run_id, vendor_id, post_id,
                       structured_output, reasoning):
        seen.append(("s3b", structured_output))
        return unfinished

    monkeypatch.setattr(stage2_triage, "_pass_a", fake_s2_text)
    monkeypatch.setattr(stage2_triage, "_pass_b", fake_s2_vision)
    monkeypatch.setattr(stage3_extract, "complete_structured", fake_s3_text)
    monkeypatch.setattr(stage3_extract, "_pass_b", fake_s3_vision)
    post = {"post_id": "p", "caption": "Toyota Camry for sale", "media_url": "https://cdn.example/p.jpg"}

    for vision_mode in ("json_schema", None):
        seen.clear()
        stage2_triage.triage_post(post, None, structured_output="json_object",
                                  vision_structured_output=vision_mode)
        stage3_extract.extract_product(post, None, structured_output="json_object",
                                       vision_structured_output=vision_mode)
        expected_b = vision_mode or "json_object"
        assert seen == [("s2a", "json_object"), ("s2b", expected_b),
                        ("s3a", "json_object"), ("s3b", expected_b)]
