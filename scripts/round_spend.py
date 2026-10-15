#!/usr/bin/env python3
"""
Round spend: what the v2 token log says round 2 has spent, beside what
OpenRouter says the key has spent, and how much of the cap is left.

    uv run scripts/round_spend.py            # table per run_id + totals
    uv run scripts/round_spend.py --offline  # skip the OpenRouter call

Per run_id it prints the listed-rate estimate (estimated_cost_usd) and the
charge OpenRouter reported per call (billed_cost_usd, recorded since
2026-10-15 evening). Logged spend - billed where recorded, else the estimate -
is what the cap in pipeline/llm_client.py counts.

OpenRouter's own figure is the key's lifetime usage, read with the key in an
Authorization header (never a URL - see CLAUDE.md). It lags the calls by
minutes, includes calls made without a run_id (diagnostics, smoke probes)
that the log never records, and includes nothing from any other key. So the
two should be close, not equal; a large gap with the log ABOVE the bill
usually means the bill has not caught up.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import llm_client  # noqa: E402
from pipeline.settings import redact_tokens  # noqa: E402

KEY_URL = "https://openrouter.ai/api/v1/key"


def per_run(log_path: Path) -> list[dict]:
    """One summary row per run_id in the log, in first-seen order."""
    runs: dict[str, dict] = defaultdict(lambda: {"calls": 0, "estimated": 0.0, "billed": 0.0,
                                                  "billed_calls": 0, "spend": 0.0})
    if not log_path.exists():
        return []
    with log_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            entry = runs[row.get("run_id") or "unknown"]
            entry["calls"] += 1
            try:
                entry["estimated"] += float(row.get("estimated_cost_usd") or 0.0)
            except ValueError:
                pass
            if row.get("billed_cost_usd") not in (None, ""):
                entry["billed"] += float(row["billed_cost_usd"])
                entry["billed_calls"] += 1
            entry["spend"] += llm_client._row_spend(row)
    return [{"run_id": run_id, **entry} for run_id, entry in runs.items()]


def openrouter_key_usage() -> float | None:
    """The key's lifetime usage in dollars, or None when it cannot be read."""
    key = os.environ.get("OPENROUTER_API_KEY") or ""
    if not key:
        return None
    try:
        resp = requests.get(KEY_URL, headers={"Authorization": f"Bearer {key}"}, timeout=20)
        resp.raise_for_status()
        return float(resp.json()["data"]["usage"])
    except Exception as exc:  # noqa: BLE001 - the summary still prints without it
        print(f"(could not read OpenRouter usage: {redact_tokens(exc)})")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--log", type=Path, default=None,
                        help="Token log to read (default: the v2 log, which holds round 2)")
    parser.add_argument("--offline", action="store_true", help="Skip the OpenRouter usage call")
    args = parser.parse_args()
    log_path = args.log or llm_client.TOKEN_LOG_PATH

    rows = per_run(log_path)
    print(f"Round spend from {log_path}")
    print(f"  {'run_id':<42} {'calls':>5} {'listed':>9} {'billed':>9} {'counted':>9}")
    for r in rows:
        billed = f"${r['billed']:.4f}" if r["billed_calls"] else "-"
        print(f"  {r['run_id']:<42} {r['calls']:>5} ${r['estimated']:>8.4f} {billed:>9} "
              f"${r['spend']:>8.4f}")
    spent = sum(r["spend"] for r in rows)
    cap = llm_client.round_budget_usd()
    print(f"\n  Counted against the cap: ${spent:.4f}")
    if cap is None:
        print("  Cap: off (ROUND_BUDGET_USD=off)")
    else:
        print(f"  Cap: ${cap:.2f}  ->  left ${cap - spent:.4f} ({spent / cap:.1%} used)")

    if not args.offline:
        usage = openrouter_key_usage()
        if usage is not None:
            print(f"  OpenRouter key usage (lifetime, lags by minutes): ${usage:.4f}")


if __name__ == "__main__":
    main()
