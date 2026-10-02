"""`council.ci_review` / the `council-ci-review` console script were deleted on
2026-09-12. See docs/venice-review-shim-disposition-2026-09-12.md for the
evidence; the short version is that a packaged copy of the CI shim that nothing
runs does not stay in parity with the copy that does, and it spent six weeks
behind the live script's security hardening while looking installed and ready.

This test exists so that bringing it back is a deliberate act. If a future
migration really does move the fleet onto a packaged entry point, delete this
test in the same change that adds the entry point — and add a parity test
against scripts/venice_review.py in its place.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_packaged_ci_shim_is_gone():
    assert importlib.util.find_spec("council.ci_review") is None, (
        "council/ci_review.py is back. If that is intended, it needs a parity "
        "test against scripts/venice_review.py — see "
        "docs/venice-review-shim-disposition-2026-09-12.md")


def test_no_console_entry_point_advertises_the_packaged_shim():
    assert "council-ci-review" not in (ROOT / "pyproject.toml").read_text()


def test_the_canonical_shim_is_the_repo_script():
    """scripts/venice_review.py is what all 18 repos run. It must keep the
    hardening the packaged copy never received."""
    src = (ROOT / "scripts/venice_review.py").read_text()
    assert "COMMENT_AUTHOR" in src            # round 3: comment ownership
    assert "REQUIRED_ENV" in src              # round 2: fail closed on missing env
    assert "Retry-After" in src               # round 3: throttle retries
    assert "page=" in src                     # round 3: comment pagination
