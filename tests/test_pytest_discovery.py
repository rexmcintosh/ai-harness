"""Broad pytest discovery must never collect the archived regression snapshots.

`tools/regress/fixtures/*/head/` holds byte-exact snapshots of other repos at a
reviewed commit. Some snapshots carry their own `tests/test_*.py`. When pytest
collects one it imports it, the import writes `__pycache__/*.pyc` into the
snapshot, and `harness.validate_fixture` then (correctly) rejects the snapshot
because its files no longer match the manifest. The correction is collection
config in pyproject.toml, never a weaker validator.
"""
from __future__ import annotations

import json
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


def _probe_env() -> dict[str, str]:
    env = dict(os.environ)
    # If the exclusion ever regresses, this probe must not be the thing that
    # writes bytecode into a hash-validated snapshot.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    # Keeps ambient third-party plugins out of the probe (system python3 autoloads anyio).
    # Proven harmless on 2026-09-18, pytest 9.0.3, venv and system python: node ids are
    # identical with and without it for every probe here, for a simulated regression
    # (the 9 archived ids are still seen) and for the full repo (1304 ids). If the repo
    # config ever needs an autoloaded plugin, the probe exits non-zero and the
    # returncode assert below reports it; it cannot pass silently.
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def _collected_node_ids(*args: str, cwd: Path) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=cwd, env=_probe_env(), capture_output=True, text=True, timeout=120,
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
    assert set(PYTEST_DEFAULT_NORECURSEDIRS) <= set(patterns), "a pytest default was dropped"
    assert SNAPSHOTS in patterns
    # Exactly the restated defaults plus the two snapshot patterns: no dropped entry,
    # no silent extra, no duplicate.
    assert sorted(patterns) == sorted([*PYTEST_DEFAULT_NORECURSEDIRS, SNAPSHOTS, SNAPSHOTS + "/*"])


def test_restated_defaults_equal_the_installed_pytest_builtin_default(tmp_path):
    """Fails loudly when a pytest upgrade changes its built-in `norecursedirs`.

    Then update PYTEST_DEFAULT_NORECURSEDIRS above AND the list in pyproject.toml.
    Public API only: a throwaway rootdir with an empty pytest.ini, and a conftest.py
    whose `pytest_configure` hook writes `config.getini("norecursedirs")` as JSON.
    No private attributes, no parsing of pytest's printed output.
    """
    out = tmp_path / "norecursedirs.json"
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "import json, os, pathlib\n\n\n"
        "def pytest_configure(config):\n"
        "    pathlib.Path(os.environ['NORECURSEDIRS_OUT']).write_text(\n"
        "        json.dumps(config.getini('norecursedirs')))\n"
    )
    env = _probe_env()
    env["NORECURSEDIRS_OUT"] = str(out)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "-c", "pytest.ini", "--rootdir", "."],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode in (0, 5), result.stdout + result.stderr
    assert sorted(json.loads(out.read_text())) == sorted(PYTEST_DEFAULT_NORECURSEDIRS)
