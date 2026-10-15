"""Structured-output mode tests for pipeline/llm_client.py.

Qwen 2.5 7B under json_object mode returned invented labels and keys on 44 of
71 first attempts in the September autos runs ("log", "sync_confidence",
{"post_type": "log", "data": {...}}), and two posts exhausted every retry in
both runs. json_schema mode sends the schema to the provider; these tests pin
what is sent, and what a retry tells the model, without calling one.

Offline: no network, no .env, no golden set.
"""

from __future__ import annotations

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
            usage=None,
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
