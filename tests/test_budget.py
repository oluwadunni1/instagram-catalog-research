"""Round spend cap tests for pipeline/llm_client.py.

Round 2 runs on $5 of OpenRouter credit. The cap is the only thing between a
misconfigured run - a reasoning model left on, a loop that retries forever -
and the whole balance. It has to count the right rows, stop before the next
paid call, and not be swallowed by the per-post error handling that every
stage loop has.

Offline: no network, no .env, no golden set.
"""

from __future__ import annotations

import csv
import json
from types import SimpleNamespace

import pytest

from pipeline import llm_client
from pipeline.exceptions import BudgetExceededError


def _row(model="openrouter/qwen/x", **overrides) -> None:
    row = dict(run_id="r", vendor_id="v", post_id="p", stage="s", model=model,
               prompt_tokens=0, completion_tokens=0)
    llm_client.log_token_usage(**{**row, **overrides})


def test_the_cap_stops_the_next_openrouter_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUND_BUDGET_USD", "0.50")
    _row(billed_cost_usd=0.30)
    llm_client.check_budget("openrouter/qwen/x")  # under the cap

    _row(billed_cost_usd=0.25)  # the running total moves as rows are written

    with pytest.raises(BudgetExceededError, match="0.5500"):
        llm_client.check_budget("openrouter/qwen/x")


def test_the_cap_is_not_an_ordinary_exception() -> None:
    """Stage 3 turns any Exception into the regex fallback, and every stage
    loop isolates per-post Exceptions. A cap that were one would be absorbed
    post by post while the run carried on."""
    assert not issubclass(BudgetExceededError, Exception)


def test_stage3_does_not_swallow_the_cap_into_its_regex_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipeline.stages import stage3_extract

    def over_budget(**kwargs):
        raise BudgetExceededError("spent")

    monkeypatch.setattr(stage3_extract, "complete_structured", over_budget)
    post = {"post_id": "p", "caption": "Toyota Camry for sale", "media_url": None}

    with pytest.raises(BudgetExceededError):
        stage3_extract.extract_product(post, None, text_model="openrouter/qwen/x")


def test_billed_cost_counts_ahead_of_the_listed_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The billed charge is what left the balance; the listed-rate estimate
    is the fallback for a row without one."""
    monkeypatch.setitem(llm_client.MODEL_RATES, "openrouter/qwen/x", (1_000_000.0, 0.0))
    _row(prompt_tokens=1, billed_cost_usd=0.01)  # estimate $1.00, billed $0.01
    _row(prompt_tokens=1)  # no billed charge: the estimate counts

    assert llm_client.round_spend_usd() == pytest.approx(1.01)


def test_only_openrouter_spend_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jev and the Gemini free tier are paid from elsewhere, or not at all."""
    monkeypatch.setitem(llm_client.MODEL_RATES, "jev-latest", (1_000_000.0, 0.0))
    _row(model="jev-latest", prompt_tokens=1)
    monkeypatch.setenv("ROUND_BUDGET_USD", "0.10")

    assert llm_client.round_spend_usd() == 0.0
    llm_client.check_budget("openrouter/qwen/x")
    llm_client.check_budget("gemini/gemini-3.5-flash-lite")


@pytest.mark.parametrize("value, expected", [("", 5.0), ("2.5", 2.5), ("off", None), ("OFF", None)])
def test_the_cap_setting(monkeypatch: pytest.MonkeyPatch, value: str, expected) -> None:
    monkeypatch.setenv("ROUND_BUDGET_USD", value)
    assert llm_client.round_budget_usd() == expected


def test_a_malformed_cap_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUND_BUDGET_USD", "five")
    with pytest.raises(ValueError, match="ROUND_BUDGET_USD"):
        llm_client.round_budget_usd()


def test_a_call_records_what_openrouter_charged(monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline.stages.stage2_triage import TriageResult

    def completion(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content=json.dumps({"post_type": "announcement", "confidence": 0.8})))],
            usage=SimpleNamespace(prompt_tokens=554, completion_tokens=25, total_tokens=579,
                                  cost=0.0002287),
            provider="Google",
        )

    monkeypatch.setattr(llm_client.litellm, "completion", completion)
    monkeypatch.setattr(llm_client, "throttle", lambda: None)

    llm_client.complete_structured(model="openrouter/google/x", system_prompt="s",
                                   user_prompt="u", schema=TriageResult,
                                   run_id="r", vendor_id="v", post_id="p")

    with llm_client.TOKEN_LOG_PATH.open(encoding="utf-8", newline="") as f:
        (row,) = csv.DictReader(f)
    assert row["billed_cost_usd"] == "0.0002287"


def test_a_header_missing_trailing_columns_is_extended_in_place() -> None:
    """Columns are only ever appended, so old rows stay aligned and simply
    have no value for the new ones."""
    old = llm_client.TOKEN_LOG_FIELDNAMES[:-1]
    path = llm_client.TOKEN_LOG_PATH
    path.write_text(",".join(old) + "\n" + ",".join("x" for _ in old) + "\n", encoding="utf-8")

    assert llm_client.extend_token_log_header(path) is True

    with path.open(encoding="utf-8", newline="") as f:
        (row,) = csv.DictReader(f)
    assert row["run_id"] == "x" and row["billed_cost_usd"] is None
    assert llm_client.extend_token_log_header(path) is False


def test_a_header_that_is_not_a_prefix_is_refused() -> None:
    path = llm_client.TOKEN_LOG_PATH
    path.write_text("run_id,something_else\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not a prefix"):
        llm_client.extend_token_log_header(path)


def test_a_row_logged_before_its_model_had_a_rate_still_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The round-2 Qwen runs were logged at 0.0, before Qwen was priced. Read
    at face value they would be free to the cap."""
    monkeypatch.delitem(llm_client.MODEL_RATES, "openrouter/qwen/x", raising=False)
    _row(prompt_tokens=1_000_000, completion_tokens=0)  # written unpriced
    monkeypatch.setitem(llm_client.MODEL_RATES, "openrouter/qwen/x", (0.10, 0.20))
    llm_client._spend_cache.update(path=None)  # a fresh process re-reads the log

    assert llm_client.round_spend_usd() == pytest.approx(0.10)
