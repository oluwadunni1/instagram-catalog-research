"""
Thin LLM wrapper used by all pipeline stages.

complete_structured() is the single call-site for every LLM interaction in
the pipeline.  It:
  - Sends a system + user prompt via litellm (supports any provider/model string)
  - Expects the model to return a JSON object
  - Parses + validates that JSON into the caller-supplied Pydantic schema
  - Retries with a correction message if the model returns invalid JSON or
    a schema mismatch (brief section 3: "reject and retry on invalid JSON") -
    raises only after retries are exhausted, so a stage knows extraction
    genuinely failed rather than swallowing a bad response silently
  - Retries with exponential backoff on transient provider errors (429
    rate-limits and 5xx overloads) - free-tier models share pools that get
    temporarily overloaded; crashing the whole eval run on the first 429
    is the wrong behaviour for experiments
  - Logs prompt/completion token usage and call latency to the token log
    (report/token_log_v2.csv - see log_token_usage()) whenever the caller
    passes a run_id - opt-in per call so a one-off script or REPL probe
    doesn't pollute the log

Logging goes to stdout so `uv run` output is easy to follow.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Type, TypeVar

import httpx
import litellm
from pydantic import BaseModel, ValidationError

import pipeline.settings  # noqa: F401 - import side effect: load_dotenv() before the env reads below
from pipeline.exceptions import BudgetExceededError

# Suppress litellm's noisy "Provider List: https://..." startup banners and
# debug output - these add nothing to eval runs and drown real log lines.
litellm.suppress_debug_info = True
litellm.set_verbose = False

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_OBJ_RE = re.compile(r"(\{.*\})", re.DOTALL)


# Gemini's free tier allows 15 requests/minute/model - one call every 4 seconds.
# Nothing here paced the calls, so a run burst straight through that ceiling: the
# transient-retry loop below absorbed the resulting 429s until Stage 3 exhausted
# its budget and dropped to _regex_fallback(), leaving the run reporting clean
# scores over partly-heuristic output. Observed 2026-09-11 on a 20-call, 10-post
# run, which lost one post that way.
#
# Spacing the calls costs roughly the wall-clock the backoff was burning anyway,
# without the failures. 4.5s rather than the bare 60/15 = 4.0s: exactly at the
# limit leaves no margin for however the provider bounds its window, and the
# difference over a whole run is a few seconds. Set LLM_MIN_CALL_INTERVAL=0 to
# disable on a paid tier.
DEFAULT_CALL_INTERVAL_SECONDS = 4.5


def _read_call_interval() -> float:
    """LLM_MIN_CALL_INTERVAL as a float, treating unset-or-blank as the default.

    The blank case is the one that matters: .env.example ships the key with an
    empty value, so a copied .env yields "" and a bare float("") would raise at
    import time and take the whole pipeline down. A malformed non-empty value
    still raises - a typo'd interval should be loud, not silently ignored.
    """
    raw = (os.environ.get("LLM_MIN_CALL_INTERVAL") or "").strip()
    if not raw:
        return DEFAULT_CALL_INTERVAL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        raise ValueError(
            f"LLM_MIN_CALL_INTERVAL must be a number of seconds (got {raw!r}). "
            "Leave it blank for the default, or set 0 to disable throttling."
        ) from None


MIN_CALL_INTERVAL_SECONDS = _read_call_interval()

# Round 2's spend cap: OpenRouter spend recorded in the v2 token log, which
# holds round 2 and nothing else. Checked before every paid call; at or over
# the cap, BudgetExceededError stops the run. "off" disables it. The default is
# the $5 the round was given on 2026-10-14. It is a log-side cap: a call made
# without a run_id is never logged and so never counted, and the cap can be
# overshot by at most the one call in flight per process.
DEFAULT_ROUND_BUDGET_USD = 5.0


def round_budget_usd() -> float | None:
    """ROUND_BUDGET_USD as a float, the default when unset or blank, None when "off"."""
    raw = (os.environ.get("ROUND_BUDGET_USD") or "").strip().lower()
    if not raw:
        return DEFAULT_ROUND_BUDGET_USD
    if raw == "off":
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError(
            f"ROUND_BUDGET_USD must be dollars or 'off' (got {raw!r})."
        ) from None

_last_call_started_at = 0.0
_throttle_lock = threading.Lock()


def throttle() -> None:
    """Blocks until MIN_CALL_INTERVAL_SECONDS have passed since the last call.

    Must be called before EVERY request that reaches a provider, including the
    vision Pass B calls in stage2_triage.py/stage3_extract.py that bypass
    complete_structured() - they spend the same per-minute quota, so throttling
    only this module's calls would still let a cascade trip the limit.

    Spacing is measured from one call's start to the next, which is what a
    requests-per-minute quota counts; a call that itself takes 3s therefore only
    waits 1s afterwards.
    """
    global _last_call_started_at
    if MIN_CALL_INTERVAL_SECONDS <= 0:
        return
    with _throttle_lock:
        wait = MIN_CALL_INTERVAL_SECONDS - (time.monotonic() - _last_call_started_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_started_at = time.monotonic()


def _extract_json(text: str) -> dict:
    """Best-effort JSON extraction from model output.

    Models often wrap JSON in markdown fences or precede it with prose even
    when told to return ONLY JSON. Try the most-specific extractions first
    and fall back to broader ones before giving up.
    """
    # 1. Direct parse - most models that follow instructions land here
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Markdown code fence: ```json { ... } ```
    m = _FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3. Bare object anywhere in the response (catches "Sure! Here you go: {...}")
    m = _BARE_OBJ_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError(f"No valid JSON found in response", text, 0)


# Exceptions that indicate a transient provider problem - safe to retry with
# backoff. Distinguished from content errors (bad JSON / schema mismatch)
# which get a different retry path (re-prompt with correction message).
_TRANSIENT_EXCEPTIONS = (
    litellm.exceptions.RateLimitError,        # 429 - upstream rate limit
    litellm.exceptions.APIError,              # 5xx - provider overloaded / temp error
    litellm.exceptions.ServiceUnavailableError,  # 503 explicit
    litellm.exceptions.Timeout,               # network / gateway timeout
    httpx.ReadError,                          # server disconnect mid-stream
    httpx.ConnectError,                       # connection refused / DNS
    httpx.RemoteProtocolError,               # server sent bad HTTP
)

# Provider errors that arrive INSIDE a _TRANSIENT_EXCEPTIONS type but are
# permanent, so retrying only wastes wall-clock time. litellm raises
# OpenRouter's 402 as a generic APIError, which the tuple above classifies as
# transient - so an exhausted balance burned 6 retries with exponential
# backoff (2+4+8+16+32+64 = ~2 minutes) PER POST before failing. On a 41-post
# run that is over an hour to learn what one balance check answers instantly.
# Hit three times on 2026-09-09; see README.md.
#
# Matched on message text because the provider's HTTP status is not reliably
# surfaced on the exception - deliberately narrow, and it only ever converts a
# slow failure into a fast one with the same outcome.
_PERMANENT_ERROR_MARKERS = (
    "insufficient credits",
    "requires more credits",
    "never purchased credits",
    "402",
)


def _is_permanent_provider_error(exc: Exception) -> bool:
    """True for a provider error that no amount of waiting will fix."""
    text = str(exc).lower()
    return any(marker in text for marker in _PERMANENT_ERROR_MARKERS)


# ---------------------------------------------------------------------------
# Real cost, per row.
#
# estimated_cost_usd was hardcoded 0.0 from the start, on the reasoning that
# everything was running on a free tier. The consequence was that every "Cost:
# $X" this project printed was cost_per_call_usd from a YAML times a call
# count - a config's OPINION of what a call costs, not what the call cost - and
# every real figure in README.md had to be worked out by hand from token counts.
#
# Rates are $ per 1,000,000 tokens, (prompt, completion).
#
# A model that is not in this table is priced 0.0 and WARNED about, never
# guessed at. A silently-guessed rate is worse than no rate: it produces a
# number that looks like evidence and is not, which is the failure this whole
# column already had once.
#
# HISTORY IS MIXED, and every reader of the column has to know it: rows written
# before this landed are 0.0 regardless of what they actually cost. Old rows
# are deliberately NOT recomputed in place - the log is append-only evidence,
# and rewriting history destroys the one property that makes it the record.
# read_run_usage() therefore reports priced_calls alongside calls so a caller
# can say how much of a total is real.
MODEL_RATES: dict[str, tuple[float, float]] = {
    # Jev's published input rate, with free output. Source: scripts/smoke_jev.py,
    # which has been quoting it since the model was introduced.
    "jev-1.13.0": (0.042, 0.0),
    "jev-latest": (0.042, 0.0),
    # Round 2 candidates, all through OpenRouter. Source: the model-level
    # `pricing` in https://openrouter.ai/api/v1/models, read 2026-10-15. Two
    # limits on what these prices tell you:
    #   - OpenRouter prices vary by upstream provider, and the log's provider
    #     column records which one served each row; these are the listed
    #     model price. Qwen3-14B, for one, is $0.10 / $0.22 on NextBit against
    #     $0.12 / $0.24 listed, so its rows overstate slightly.
    #   - Prices move. Kimi K3's input was $0.70 on 2026-10-14 and $0.45 a day
    #     later. A row is priced at the rate on file when it was written and
    #     never recomputed, so update the date when you change a line.
    "openrouter/google/gemini-3.5-flash-lite": (0.30, 2.50),
    "openrouter/google/gemini-3.5-flash": (1.50, 9.00),
    "openrouter/openai/gpt-4o-mini": (0.15, 0.60),
    "openrouter/meta-llama/llama-3.1-8b-instruct": (0.05, 0.08),
    "openrouter/meta-llama/llama-4-scout": (0.18, 0.59),
    "openrouter/qwen/qwen-2.5-7b-instruct": (0.10, 0.20),
    "openrouter/qwen/qwen3-14b": (0.12, 0.24),
    "openrouter/qwen/qwen3-vl-8b-instruct": (0.25, 0.75),
    "openrouter/qwen/qwen3.7-flash": (0.03, 0.13),
    "openrouter/nex-agi/nex-n2.5-mini": (0.025, 0.10),
    "openrouter/xiaomi/mimo-v2.6-flash": (0.14, 0.28),
    "openrouter/moonshotai/kimi-k2.6": (0.65, 3.41),
    "openrouter/moonshotai/kimi-k3": (0.45, 15.00),
}

# Models seen in the log with no rate on file. Kept separate from MODEL_RATES so
# the gap is visible rather than implied by absence, and so filling one in is a
# single-line edit against a named source.
#
# UNPRICED, needs a rate from the provider's own page before any cost figure
# that includes these rows can be quoted:
#   gemini/gemini-3.5-flash-lite  - the direct Google AI Studio route, used by
#       every round-1 run on the free tier. README records the COMPLETION rate
#       as $2.50/M but never the prompt rate, and Google's paid rate need not
#       match OpenRouter's. Half a rate cannot price a row.
#   gemini/gemini-3.5-flash       - same.
#   the round-1 OpenRouter rows (2026-09) - priced 0.0 when written, and rows
#       are never recomputed, so their cost stays hand-derived.
#   gemini-embedding-001          - Stage 6's caption embeddings
#       (pipeline/media_fingerprint.py). Google prices embeddings per input
#       token on a separate sheet from the generative models, and embedContent
#       returns no usage block, so these rows carry 0 tokens: the row proves the
#       call happened and nothing more. A Stage 6 cost figure needs the call
#       COUNT against the published embedding rate, not this column.

_warned_unpriced: set[str] = set()


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Dollar cost of one call, or 0.0 when the model has no rate on file.

    Warns once per unknown model per process. Once, because a harness run would
    otherwise emit one warning per post and the signal would be lost in it.
    """
    rate = MODEL_RATES.get(model)
    if rate is None:
        if model not in _warned_unpriced:
            _warned_unpriced.add(model)
            logger.warning(
                "[token_log] no rate on file for model %r - its rows are priced 0.0 and "
                "any cost total including them understates. Add it to MODEL_RATES.",
                model,
            )
        return 0.0
    prompt_rate, completion_rate = rate
    return (prompt_tokens * prompt_rate + completion_tokens * completion_rate) / 1_000_000


# Two logs, and only one of them is ever written. v1 is every row up to round 1:
# the evidence behind every figure in README.md, frozen on 2026-10-15 so a
# re-run can never mix into it. v2 is round 2 onward, with the same columns
# plus latency_ms. Readers find a run in whichever log holds it (find_run_log),
# so a v1 run_id still verifies without anyone naming the file.
TOKEN_LOG_V1_PATH = Path("report") / "token_log.csv"
TOKEN_LOG_PATH = Path("report") / "token_log_v2.csv"
TOKEN_LOG_FIELDNAMES = [
    "run_id",
    "timestamp",
    "vendor_id",
    "post_id",
    "stage",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "escalated",
    "fallback_used",
    "estimated_cost_usd",
    # Wall time of the provider call that produced the result, in ms. Excludes
    # throttle() pacing and backoff sleeps, so it measures the model, not this
    # project's rate limiting. Empty on a row with no call behind it (a
    # fallback row) and on every v1 row, which predates the column.
    "latency_ms",
    # Every attempt that reached a provider gets a row, not only the one that
    # succeeded: a response that fails the schema is still billed. attempt is
    # 1-based within one complete_structured() call; outcome is "ok", "invalid"
    # (it came back but failed parsing or the schema) or "fallback" (no call
    # behind the row). Before 2026-10-15 only "ok" attempts were logged, so the
    # smoke run r2_family_qwen_20261015T134852Z understates its own spend.
    "attempt",
    "outcome",
    # Who actually served the call: OpenRouter's upstream provider ("Phala"),
    # or litellm's provider label for a direct call ("gemini"). September's Qwen
    # failures came from a routing nobody could see, because this was missing.
    "provider",
    # What OpenRouter charged for the call (its usage.cost), as opposed to
    # estimated_cost_usd, which is tokens x the listed rate in MODEL_RATES.
    # The two differ when a prompt is served from cache or a provider is
    # cheaper than the listed price. Empty for any other route, and for rows
    # written before 2026-10-15 evening, when the column was added.
    "billed_cost_usd",
]


def billed_cost(response) -> float | None:
    """What OpenRouter charged for a litellm response, or None when the
    response carries no charge (any non-OpenRouter route)."""
    usage = getattr(response, "usage", None)
    cost = getattr(usage, "cost", None)
    try:
        return float(cost) if cost is not None else None
    except (TypeError, ValueError):
        return None


def extend_token_log_header(path: Path) -> bool:
    """Extend an existing log's header to TOKEN_LOG_FIELDNAMES, in place.

    Only when the old header is a strict prefix of the current one: columns
    are only ever appended, so every existing row stays aligned and simply has
    no value for the new columns. Any other mismatch raises. Returns whether
    the header changed. log_token_usage() refuses to write under a stale
    header; this is the sanctioned way past that.
    """
    raw = io.open(path, encoding="utf-8", newline="").read()
    newline = "\r\n" if raw.split("\n", 1)[0].endswith("\r") else "\n"
    first, _, rest = raw.partition(newline)
    header = first.split(",")
    if header == TOKEN_LOG_FIELDNAMES:
        return False
    if TOKEN_LOG_FIELDNAMES[:len(header)] != header:
        raise RuntimeError(f"{path} header {header} is not a prefix of {TOKEN_LOG_FIELDNAMES}")
    io.open(path, "w", encoding="utf-8", newline="").write(
        ",".join(TOKEN_LOG_FIELDNAMES) + newline + rest)
    return True


def _row_spend(row: dict) -> float:
    """One row's OpenRouter spend: the billed charge when recorded, else the
    listed-rate estimate written with the row, else the row's tokens at the
    rate on file now. Zero for any other route.

    The last fallback exists because a row written before its model had a rate
    carries 0.0, and a cap that read it at face value would count those calls
    as free - the round-2 Qwen runs were exactly that. It re-prices only what
    the cap counts; the row itself is never rewritten.
    """
    model = row.get("model") or ""
    if not model.startswith("openrouter/"):
        return 0.0
    for key in ("billed_cost_usd", "estimated_cost_usd"):
        try:
            value = float(row.get(key) or 0.0)
        except ValueError:
            continue
        if value > 0.0:
            return value
    try:
        prompt, completion = int(row.get("prompt_tokens") or 0), int(row.get("completion_tokens") or 0)
    except ValueError:
        return 0.0
    rate = MODEL_RATES.get(model)
    return (prompt * rate[0] + completion * rate[1]) / 1_000_000 if rate else 0.0


# Running total of the v2 log's OpenRouter spend, read from disk once per log
# path and then advanced by every row this process writes, so the check before
# each call costs nothing.
_spend_cache: dict = {"path": None, "usd": 0.0}


def round_spend_usd() -> float:
    """OpenRouter spend recorded in the current token log."""
    if _spend_cache["path"] != TOKEN_LOG_PATH:
        total = 0.0
        if TOKEN_LOG_PATH.exists():
            with TOKEN_LOG_PATH.open(encoding="utf-8", newline="") as f:
                total = sum(_row_spend(row) for row in csv.DictReader(f))
        _spend_cache.update(path=TOKEN_LOG_PATH, usd=total)
    return _spend_cache["usd"]


def check_budget(model: str) -> None:
    """Raise BudgetExceededError before a paid call once the round is spent.

    Only OpenRouter calls are paid from the round's credit; a free-tier or Jev
    call is never stopped by it.
    """
    cap = round_budget_usd()
    if cap is None or not model.startswith("openrouter/"):
        return
    spent = round_spend_usd()
    if spent >= cap:
        raise BudgetExceededError(
            f"Round budget reached: ${spent:.4f} of OpenRouter spend in {TOKEN_LOG_PATH} "
            f"against ROUND_BUDGET_USD=${cap:.2f}. No further call was made. Raise the cap "
            "deliberately, or set ROUND_BUDGET_USD=off, to continue."
        )


def served_by(response) -> str:
    """The provider that answered a litellm response, or "" when unknown."""
    provider = getattr(response, "provider", None)
    if provider:
        return str(provider)
    hidden = getattr(response, "_hidden_params", None) or {}
    return str(hidden.get("custom_llm_provider") or "")


def token_log_paths() -> list[Path]:
    """Every token log, newest first. Read at call time rather than bound at
    import, so a test that points TOKEN_LOG_PATH elsewhere moves both the
    writer and every reader."""
    return [TOKEN_LOG_PATH, TOKEN_LOG_V1_PATH]


def find_run_log(run_id: str) -> Path | None:
    """The log holding this run_id's rows, or None when no log does."""
    for path in token_log_paths():
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as f:
            if any(row.get("run_id") == run_id for row in csv.DictReader(f)):
                return path
    return None


def log_token_usage(
    *,
    run_id: str,
    vendor_id: str,
    post_id: str,
    stage: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int | None = None,
    escalated: bool = False,
    fallback_used: bool = False,
    latency_ms: float | None = None,
    attempt: int = 1,
    outcome: str = "ok",
    provider: str = "",
    billed_cost_usd: float | None = None,
) -> None:
    """Append one row to the v2 token log, writing the header first if the file doesn't exist yet.

    Refuses to write to the frozen v1 log. Every README figure traces to a
    run_id in it, so a row appended there would sit beside evidence it was
    never part of.

    estimated_cost_usd is computed from MODEL_RATES. A model with no rate on
    file is priced 0.0 and warned about - see the note above MODEL_RATES for
    why an unknown rate is never guessed, and why rows written before that
    landed are left at 0.0 rather than recomputed.
    """
    if TOKEN_LOG_PATH.resolve() == TOKEN_LOG_V1_PATH.resolve():
        raise RuntimeError(
            f"{TOKEN_LOG_V1_PATH} is the frozen v1 token log and is never appended to. "
            "New rows go to the v2 log."
        )
    TOKEN_LOG_PATH.parent.mkdir(exist_ok=True)
    write_header = not TOKEN_LOG_PATH.exists()
    if not write_header:
        # The header is written once, when the file is created, so a column
        # added later never reaches an existing log: new rows would carry
        # values the header does not name, and every reader would misread
        # them. That happened on 2026-10-15 - attempt/outcome/provider rows
        # under a header without them, read back as answered calls. Refuse.
        with TOKEN_LOG_PATH.open(encoding="utf-8", newline="") as f:
            header = next(csv.reader(f), [])
        if header != TOKEN_LOG_FIELDNAMES:
            raise RuntimeError(
                f"{TOKEN_LOG_PATH} has columns {header}, but rows are written with "
                f"{TOKEN_LOG_FIELDNAMES}. Extend it with extend_token_log_header() "
                "before logging to it."
            )

    row = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vendor_id": vendor_id,
        "post_id": post_id,
        "stage": stage,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens if total_tokens is not None else prompt_tokens + completion_tokens,
        "escalated": escalated,
        "fallback_used": fallback_used,
        "estimated_cost_usd": estimate_cost_usd(model, prompt_tokens, completion_tokens),
        "latency_ms": "" if latency_ms is None else round(latency_ms, 1),
        "attempt": attempt,
        "outcome": outcome,
        "provider": provider,
        "billed_cost_usd": "" if billed_cost_usd is None else billed_cost_usd,
    }

    with TOKEN_LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TOKEN_LOG_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    if _spend_cache["path"] == TOKEN_LOG_PATH:
        _spend_cache["usd"] += _row_spend(row)


def read_run_usage(run_id: str, log_path: Path | None = None) -> dict:
    """Per-stage call counts, token totals and latency for one run, read back
    from the token log.

    log_path names the log to read; by default it is whichever log holds the
    run_id (find_run_log), falling back to the v2 log for a run with no rows.
    "log" in the result says which file was read.

    Deliberately not accumulated in memory as we go: the log is what actually
    reached litellm, so a stage that silently fell back to a different model,
    or never called one at all, shows up here and cannot be papered over by
    the runner's own bookkeeping.

    Lives next to log_token_usage() (its writer) and TOKEN_LOG_PATH so the
    reader and writer of the log share one definition of its shape. Both
    scripts/run_pipeline.py and scripts/verify_run.py read through this.
    """
    path = log_path or find_run_log(run_id) or TOKEN_LOG_PATH
    empty = {"calls": 0, "invalid_attempts": 0, "prompt_tokens": 0, "completion_tokens": 0,
             "total_tokens": 0, "fallbacks": 0, "cost_usd": 0.0, "priced_calls": 0,
             "billed_usd": 0.0, "billed_calls": 0,
             "latency_ms": 0.0, "timed_calls": 0, "by_stage": {}, "models": [],
             "providers": [], "log": path}
    if not path.exists():
        return empty

    by_stage: dict[str, dict] = {}
    models: Counter = Counter()
    providers: Counter = Counter()
    # calls counts answered calls only - the unit the gate's per-post band is
    # sized in. A schema-failed attempt is counted in invalid_attempts instead,
    # but its tokens and cost still go into every total: it was billed.
    totals = {"calls": 0, "invalid_attempts": 0, "prompt_tokens": 0, "completion_tokens": 0,
              "total_tokens": 0, "fallbacks": 0}
    # Tracked apart from the token totals because cost is only as trustworthy as
    # its coverage: rows predating MODEL_RATES, and rows for a model with no rate
    # on file, both carry 0.0. priced_calls < calls means the total understates,
    # and a caller quoting the figure has to say so.
    cost_usd = 0.0
    priced_calls = 0
    # Same coverage rule as cost: a mean latency is over timed_calls, never
    # over calls, because v1 rows and fallback rows carry no time at all.
    latency_ms = 0.0
    timed_calls = 0
    billed_usd = 0.0
    billed_calls = 0

    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("run_id") != run_id:
                continue

            def _int(key: str) -> int:
                try:
                    return int(row.get(key) or 0)
                except ValueError:
                    return 0

            stage = row.get("stage") or "unknown"
            entry = by_stage.setdefault(
                stage,
                {"calls": 0, "invalid_attempts": 0, "prompt_tokens": 0, "completion_tokens": 0,
                 "total_tokens": 0, "cost_usd": 0.0, "latency_ms": 0.0, "timed_calls": 0},
            )
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                entry[key] += _int(key)
                totals[key] += _int(key)
            # v1 rows have no outcome column; every one of them was an answer.
            invalid = (row.get("outcome") or "ok") == "invalid"
            counter = "invalid_attempts" if invalid else "calls"
            entry[counter] += 1
            totals[counter] += 1

            try:
                row_cost = float(row.get("estimated_cost_usd") or 0.0)
            except ValueError:
                row_cost = 0.0
            entry["cost_usd"] += row_cost
            cost_usd += row_cost
            if row_cost > 0.0:
                priced_calls += 1
            if row.get("billed_cost_usd") not in (None, ""):
                try:
                    billed_usd += float(row["billed_cost_usd"])
                    billed_calls += 1
                except ValueError:
                    pass
            try:
                row_latency = float(row.get("latency_ms") or "")
            except ValueError:
                row_latency = None
            # Latency is per answered call, like calls: a runaway invalid
            # attempt would otherwise drag the model's mean with it.
            if row_latency is not None and not invalid:
                entry["latency_ms"] += row_latency
                entry["timed_calls"] += 1
                latency_ms += row_latency
                timed_calls += 1
            if (row.get("fallback_used") or "").strip().lower() == "true":
                totals["fallbacks"] += 1
            models[row.get("model") or "unknown"] += 1
            if row.get("provider"):
                providers[row["provider"]] += 1

    return {**totals, "cost_usd": cost_usd, "priced_calls": priced_calls,
            "latency_ms": latency_ms, "timed_calls": timed_calls,
            "billed_usd": billed_usd, "billed_calls": billed_calls,
            "by_stage": by_stage, "models": sorted(models), "providers": sorted(providers),
            "log": path}


# How a call asks for JSON. "json_object" (the default, and the only mode
# before 2026-10-15) asks for any JSON object and leaves the schema to
# _extract_json() and model_validate(). "json_schema" sends the Pydantic schema
# itself, so a provider that supports structured outputs constrains decoding to
# it. Qwen 2.5 7B under json_object returned invented labels and keys on 44 of
# 71 first attempts in the September autos runs ("log", "sync_confidence",
# {"post_type": "log", "data": {...}}) - shapes no retry message fixes.
STRUCTURED_OUTPUT_MODES = ("json_object", "json_schema")


def _strict_json_schema(schema: Type[BaseModel]) -> dict:
    """schema's JSON Schema in the form strict structured outputs require:
    every property required and no extra properties, at every level.

    A field with a default becomes required too. That is what strict mode
    demands, and it is harmless here: a field like TriageResult.escalated is
    overwritten by our own code after the call either way.
    """
    def tighten(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for value in node.values():
                tighten(value)
        elif isinstance(node, list):
            for value in node:
                tighten(value)
        return node

    return tighten(schema.model_json_schema())


def response_format_kwargs(schema: Type[BaseModel], structured_output: str, model: str) -> dict:
    """The litellm.completion() kwargs that ask `model` for JSON in the given
    mode. Shared by complete_structured() and the vision Pass B calls, which
    build their own multimodal requests.

    For json_schema on OpenRouter it also sets provider.require_parameters, so
    OpenRouter routes only to providers that honour response_format. Without
    it a request can land on a provider that ignores the schema, and the call
    behaves exactly like json_object while the config claims otherwise.
    """
    if structured_output == "json_object":
        return {"response_format": {"type": "json_object"}}
    if structured_output == "json_schema":
        kwargs: dict = {"response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "strict": True,
                            "schema": _strict_json_schema(schema)},
        }}
        if model.startswith("openrouter/"):
            kwargs["extra_body"] = {"provider": {"require_parameters": True}}
        return kwargs
    raise ValueError(
        f"structured_output must be one of {STRUCTURED_OUTPUT_MODES}, got {structured_output!r}"
    )


# Reasoning settings a config may name. None (the key absent) sends nothing,
# so the provider's own default applies - which is what every run before
# 2026-10-15 got. Qwen3-14B's default is to reason: in smoke run
# r2_family_qwen_20261015T140152Z one Stage 3 attempt spent all 4,096 output
# tokens and 199 s reasoning, and returned an empty answer.
REASONING_SETTINGS = ("off", "low", "medium", "high")


def request_kwargs(
    schema: Type[BaseModel],
    structured_output: str,
    model: str,
    reasoning: str | None = None,
) -> dict:
    """Every litellm.completion() kwarg a stage's config controls: the JSON
    mode (response_format_kwargs) plus the reasoning setting, merged into one
    extra_body for OpenRouter.

    reasoning is OpenRouter's request parameter, so it is refused for any
    other route rather than silently dropped - a config that says "off" must
    not run with reasoning on.
    """
    kwargs = response_format_kwargs(schema, structured_output, model)
    if reasoning is None:
        return kwargs
    if reasoning not in REASONING_SETTINGS:
        raise ValueError(f"reasoning must be one of {REASONING_SETTINGS}, got {reasoning!r}")
    if not model.startswith("openrouter/"):
        raise ValueError(
            f"reasoning: {reasoning!r} is only wired for openrouter/ models; {model!r} would "
            "ignore it. Remove the key or route the model through OpenRouter."
        )
    setting = {"enabled": False} if reasoning == "off" else {"effort": reasoning}
    kwargs["extra_body"] = {**kwargs.get("extra_body", {}), "reasoning": setting}
    return kwargs


def _field_guide(schema: Type[BaseModel]) -> str:
    """One line per top-level field: its name, and its allowed values when it
    is a closed set. The retry message used to name the fields only, so a
    model that invented a label was told the field exists but not what may go
    in it."""
    properties = schema.model_json_schema().get("properties", {})
    lines = []
    for name, spec in properties.items():
        allowed = spec.get("enum")
        if allowed:
            lines.append(f"- {name}: one of {', '.join(map(str, allowed))}")
        else:
            lines.append(f"- {name}: {spec.get('type', 'see schema')}")
    return "\n".join(lines)


def _validation_summary(exc: ValidationError) -> str:
    """What was wrong, one short line per error, without pydantic's URLs."""
    parts = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error.get("loc", ())) or "(root)"
        parts.append(f"{where}: {error.get('msg')} (got {error.get('input')!r})")
    return "; ".join(parts)


def complete_structured(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: Type[T],
    stage_name: str = "unknown",
    temperature: float = 0.0,
    max_tokens: int = 1024,
    max_retries: int = 2,
    max_transient_retries: int = 6,
    run_id: str | None = None,
    vendor_id: str | None = None,
    post_id: str | None = None,
    escalated: bool = False,
    fallback_used: bool = False,
    structured_output: str = "json_object",
    reasoning: str | None = None,
) -> T:
    """Call `model` and parse the response JSON into `schema`.

    Args:
        model:                 litellm model string, e.g. "openai/gpt-4o-mini"
                               or "openrouter/google/gemini-flash-1.5-8b".
        system_prompt:         System message text.
        user_prompt:           User message text.
        schema:                Pydantic BaseModel class to validate the response.
        stage_name:            Label used in log/error messages and the token log's
                               "stage" column.
        temperature:           Sampling temperature (default 0.0 for determinism).
        max_tokens:            Max tokens for the response.
        max_retries:           Extra attempts if the response is bad JSON or
                               fails schema validation. 0 = single-shot.
        max_transient_retries: Max retries for 429 / 5xx provider errors.
                               Backoff doubles each time: 2, 4, 8 ... seconds.
        run_id:                Groups every log_token_usage() row from one
                               experiment run together. Token usage is only
                               logged when this is set - omit it (the
                               default) for a one-off script or REPL probe
                               you don't want polluting the token log.
        vendor_id:             Account label, for the token log's "vendor_id"
                               column. Only used when run_id is set.
        post_id:               Post id, for the token log's "post_id" column
                               - pass "" for an account-level call (e.g.
                               Stage 1) that isn't about one specific post.
                               Only used when run_id is set.
        escalated:             Whether this call is a cascade's vision/Pass B
                               escalation, for the token log's "escalated"
                               column. Only used when run_id is set.
        fallback_used:         Whether this call is a retry after a prior
                               fallback, for the token log's "fallback_used"
                               column. Only used when run_id is set.
        structured_output:     "json_object" (default) or "json_schema" -
                               see response_format_kwargs().
        reasoning:             None (provider default), "off", "low",
                               "medium" or "high" - see request_kwargs().

    Returns:
        A validated instance of `schema`.

    Raises:
        RuntimeError: If every attempt (including retries) fails.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    format_kwargs = request_kwargs(schema, structured_output, model, reasoning)
    last_content_error: Exception | None = None
    transient_attempts = 0

    for attempt in range(1, max_retries + 2):
        logger.info("[%s] Calling model: %s (attempt %d)", stage_name, model, attempt)

        # --- API call with transient-error retry loop ---
        response = None
        while True:
            try:
                check_budget(model)
                throttle()
                call_started = time.perf_counter()
                response = litellm.completion(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **format_kwargs,
                    metadata={"run_name": stage_name, "tags": [stage_name, f"attempt_{attempt}"]},
                )
                break  # success - exit transient retry loop
            except _TRANSIENT_EXCEPTIONS as exc:
                # Fail fast on a permanent error wearing a transient error's
                # type - an exhausted balance is not a blip, and backing off
                # from it just hides the cause behind minutes of waiting.
                if _is_permanent_provider_error(exc):
                    raise RuntimeError(
                        f"[{stage_name}] Permanent provider error on {model} - not retrying. "
                        f"Check the account balance for the key in use. Last: {exc}"
                    ) from exc
                transient_attempts += 1
                if transient_attempts > max_transient_retries:
                    raise RuntimeError(
                        f"[{stage_name}] Provider error on {model} after "
                        f"{max_transient_retries} transient retries. Last: {exc}"
                    ) from exc
                wait = 2 ** transient_attempts  # 2, 4, 8, 16, 32, 64s
                logger.warning(
                    "[%s] Transient provider error (%s) - waiting %ds (retry %d/%d)",
                    stage_name, type(exc).__name__, wait,
                    transient_attempts, max_transient_retries,
                )
                time.sleep(wait)
                # continue inner loop - don't advance content-attempt counter

        latency_ms = (time.perf_counter() - call_started) * 1000
        raw_text: str = response.choices[0].message.content or ""
        logger.debug("[%s] Raw response:\n%s", stage_name, raw_text)

        # --- Parse and validate JSON ---
        result = None
        try:
            parsed = _extract_json(raw_text)
            result = schema.model_validate(parsed)
        except json.JSONDecodeError as exc:
            last_content_error = exc
            error_detail = f"non-JSON response: {raw_text!r}"
        except ValidationError as exc:
            last_content_error = exc
            error_detail = f"schema mismatch: {_validation_summary(exc)}"

        # Logged whether or not it parsed: a response that failed the schema
        # was still billed, and a run that needed three attempts per post
        # costs three times what one row per post would claim.
        if run_id is not None:
            usage = getattr(response, "usage", None)
            log_token_usage(
                run_id=run_id,
                vendor_id=vendor_id or "unknown",
                post_id=post_id or "",
                stage=stage_name,
                model=model,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(usage, "total_tokens", None),
                escalated=escalated,
                fallback_used=fallback_used,
                latency_ms=latency_ms,
                attempt=attempt,
                outcome="ok" if result is not None else "invalid",
                provider=served_by(response),
                billed_cost_usd=billed_cost(response),
            )
        if result is not None:
            return result

        if attempt <= max_retries:
            logger.warning("[%s] Attempt %d failed (%s) - retrying", stage_name, attempt, error_detail)
            # Built from the schema so the correction is specific: a model that
            # returned {} needs the fields it missed, and one that invented a
            # label needs the labels it may use, not just "try again".
            messages.append({"role": "assistant", "content": raw_text})
            messages.append({
                "role": "user",
                "content": (
                    f"Your response was invalid: {error_detail}.\n"
                    f"Return a JSON object with exactly these top-level fields and "
                    f"no others:\n{_field_guide(schema)}\n"
                    f"Return ONLY the JSON object, no explanation, no markdown."
                ),
            })

    raise RuntimeError(
        f"[{stage_name}] Failed to get valid structured output from {model} after "
        f"{max_retries + 1} attempt(s). Last error: {last_content_error}"
    )