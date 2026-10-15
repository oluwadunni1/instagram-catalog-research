"""Shared pytest setup.

There is no broad test suite by design (CLAUDE.md) - the eval harness is the
verification mechanism for model quality. These tests cover only the things
the harness structurally cannot: credential handling, per-post error
isolation inside the harness itself, and deterministic Stage 5 routing. All
of them are offline; nothing here may make a network call or read .env.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The repo root for `pipeline.*`, and eval/ for `harness` - eval/ is not a
# package (no __init__.py) and harness.py is written to be run as a script.
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "eval"))


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removes the courtesy sleeps from media_fingerprint's finally blocks so
    credential tests don't pay 0.3s each."""
    import pipeline.media_fingerprint as mf

    monkeypatch.setattr(mf.time, "sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def isolated_token_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Points both token logs at absent files under tmp_path for every test.

    Readers now search the v1 log as well as the v2 one (find_run_log), so a
    test that redirects only TOKEN_LOG_PATH would otherwise read the real
    report/token_log.csv - and a test whose code path logs a call would append
    to the real v2 log. Tests that need a log monkeypatch TOKEN_LOG_PATH (or
    TOKEN_LOG_V1_PATH) over these.
    """
    import pipeline.llm_client as llm_client

    monkeypatch.setattr(llm_client, "TOKEN_LOG_PATH", tmp_path / "token_log_v2.csv")
    monkeypatch.setattr(llm_client, "TOKEN_LOG_V1_PATH", tmp_path / "token_log_v1.csv")
