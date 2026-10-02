"""Tests for tools/ci-usage/pull.sh and its repo list.

The script is the VPS half of the CI usage bridge: it lists the venice-review
runs in every repo of `repos.txt`, downloads their usage artifacts and ingests
them. Its failure mode matters more than its happy path — a repo it cannot
reach must be *loud*, because a silent skip looks exactly like "this repo had
no PRs this week" and quietly drops that repo's spend forever.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PULL = ROOT / "tools/ci-usage/pull.sh"
REPOS = ROOT / "tools/ci-usage/repos.txt"


def _repo_lines() -> list[str]:
    return [ln.strip() for ln in REPOS.read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def _stub_bin(tmp_path: Path, gh_body: str) -> Path:
    """A PATH directory holding a fake `gh` and a fake `venice-usage`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/usr/bin/env bash\n" + gh_body)
    gh.chmod(0o755)
    vu = bin_dir / "venice-usage"
    vu.write_text('#!/usr/bin/env bash\necho "STUB-INGEST $*"\n')
    vu.chmod(0o755)
    return bin_dir


def _run(tmp_path: Path, bin_dir: Path, repos: list[str]) -> subprocess.CompletedProcess:
    repos_file = tmp_path / "repos.txt"
    repos_file.write_text("# comment line\n" + "\n".join(repos) + "\n")
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    return subprocess.run(
        ["bash", str(PULL), "--repos", str(repos_file), "--dest", str(tmp_path / "dl")],
        capture_output=True, text=True, env=env, timeout=60)


# --- the repo list itself -----------------------------------------------------

def test_repo_list_has_no_duplicates_and_is_owner_slash_name():
    lines = _repo_lines()
    assert lines, "repos.txt is empty"
    assert len(set(lines)) == len(lines), "duplicate entries in repos.txt"
    for entry in lines:
        assert re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", entry), entry


def test_repo_list_uses_github_repo_names_not_local_directory_names():
    """Three entries were the ~/projects directory name, not the GitHub repo
    name (Santa_Amaro_Home_Renovation, liam_mobility, splash_poller). `gh run
    list` 404s on those, and the old script swallowed the 404 — so three repos'
    CI spend would never have been pulled. GitHub repo names in this fleet never
    contain an underscore; the local directory names are the only place they do."""
    offenders = [e for e in _repo_lines() if "_" in e]
    assert offenders == [], f"local directory names, not GitHub repo names: {offenders}"


# --- the script's failure behaviour -------------------------------------------

GH_404_FOR = '''
if [ "$1" = "run" ] && [ "$2" = "list" ]; then
  repo=""
  while [ $# -gt 0 ]; do [ "$1" = "--repo" ] && repo="$2"; shift; done
  if [ "$repo" = "%s" ]; then
    echo "HTTP 404: workflow venice-review.yml not found on the default branch" >&2
    exit 1
  fi
  exit 0            # reachable repo, no runs in the window
fi
exit 0
'''


def test_unreachable_repo_is_reported_not_silently_skipped(tmp_path):
    bin_dir = _stub_bin(tmp_path, GH_404_FOR % "rexmcintosh/typo-repo")
    p = _run(tmp_path, bin_dir, ["rexmcintosh/good-repo", "rexmcintosh/typo-repo"])
    combined = p.stdout + p.stderr
    assert "rexmcintosh/typo-repo" in combined
    assert "404" in combined or "error" in combined.lower()
    # The give-away of the old behaviour: an unreachable repo described as empty.
    assert "typo-repo: no runs in window" not in combined
    assert p.returncode != 0, "an unreachable repo must not exit 0"


def test_a_bad_repo_does_not_stop_the_rest_of_the_list(tmp_path):
    bin_dir = _stub_bin(tmp_path, GH_404_FOR % "rexmcintosh/typo-repo")
    p = _run(tmp_path, bin_dir,
             ["rexmcintosh/typo-repo", "rexmcintosh/good-repo"])
    combined = p.stdout + p.stderr
    assert "good-repo: no runs in window" in combined


def test_all_repos_reachable_and_empty_still_exits_zero(tmp_path):
    bin_dir = _stub_bin(tmp_path, "exit 0\n")
    p = _run(tmp_path, bin_dir, ["rexmcintosh/a", "rexmcintosh/b"])
    assert p.returncode == 0, p.stdout + p.stderr
    assert "nothing to ingest" in p.stdout
