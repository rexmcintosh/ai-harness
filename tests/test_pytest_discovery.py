"""Broad pytest discovery must never collect the archived regression snapshots.

`tools/regress/fixtures/*/head/` holds byte-exact snapshots of other repos at a
reviewed commit. Some snapshots carry their own `tests/test_*.py`. When pytest
collects one it imports it, the import writes `__pycache__/*.pyc` into the
snapshot, and `harness.validate_fixture` then (correctly) rejects the snapshot
because its files no longer match the manifest. The correction is collection
config in pyproject.toml, never a weaker validator.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = "tools/regress/fixtures"

# pytest's built-in `norecursedirs`. Setting the option REPLACES this list, so
# pyproject.toml has to restate it. Losing ".*" alone would send broad discovery
# into `.venv/` and into every `.claude/worktrees/*` copy of this repo.
PYTEST_DEFAULT_NORECURSEDIRS = (
    "*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}",
)


def _collected_node_ids(*args: str, cwd: Path) -> list[str]:
    env = dict(os.environ)
    # If the exclusion ever regresses, this probe must not be the thing that
    # writes bytecode into a hash-validated snapshot.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
    )
    # 0 = collected something, 5 = collected nothing; anything else is a broken probe.
    assert result.returncode in (0, 5), result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if "::" in line]


def test_the_probe_sees_real_tests_and_a_snapshot_really_carries_tests():
    # Guards the tests below against passing vacuously.
    assert any((ROOT / SNAPSHOTS).rglob("test_*.py"))
    own = _collected_node_ids("tests/test_pytest_discovery.py", cwd=ROOT)
    assert any(node.startswith("tests/test_pytest_discovery.py::") for node in own)


@pytest.mark.parametrize(
    ("args", "cwd"),
    [
        pytest.param(("tools",), ".", id="explicit-parent-path-from-root"),
        pytest.param((), "tools/regress", id="bare-pytest-from-a-subdirectory"),
        pytest.param((SNAPSHOTS,), ".", id="explicit-fixtures-directory"),
    ],
)
def test_discovery_never_collects_archived_snapshot_tests(args, cwd):
    collected = _collected_node_ids(*args, cwd=ROOT / cwd)
    assert [node for node in collected if SNAPSHOTS in node] == []


def test_norecursedirs_adds_the_snapshots_without_dropping_pytest_defaults():
    options = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    patterns = options["norecursedirs"]
    assert set(PYTEST_DEFAULT_NORECURSEDIRS) <= set(patterns)
    assert SNAPSHOTS in patterns
