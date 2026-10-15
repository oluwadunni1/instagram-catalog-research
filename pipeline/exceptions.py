"""
Shared exception hierarchy for the pipeline.

Library functions (anything not directly behind an `if __name__ ==
"__main__":` guard) should raise these instead of `SystemExit`, so callers
other than a CLI script - e.g. a future harness path that calls
stage1_profile.get_or_create_profile() directly - can catch a real
exception instead of having the whole process torn down underneath them.

Each CLI entry point still gets the exact same user-facing behavior: catch
PipelineError in `__main__`, print the message, exit non-zero.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base class for expected, user-actionable pipeline failures."""


class MissingRawDumpError(PipelineError):
    """No raw dump found for an account - ingest.py needs to run first."""


class MissingCredentialsError(PipelineError):
    """A required credential (e.g. IG_ACCESS_TOKEN) is not set."""


class BudgetExceededError(BaseException):
    """Round spend has reached ROUND_BUDGET_USD; no further paid call is made.

    A BaseException, like KeyboardInterrupt, and deliberately not a
    PipelineError: every per-post isolation site in the harness, the runner
    and Stage 3 catches Exception, and Stage 3 turns what it catches into the
    regex fallback. An Exception here would be absorbed post by post - the run
    would carry on, quietly heuristic, until every remaining post had failed.
    The point of a cap is that the run stops where it is.
    """
