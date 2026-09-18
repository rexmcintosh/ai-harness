"""The one-off operator script docs/loom-bm2-quarantine-triage/apply.py mutates LIVE,
gitignored loom data (state.json, learnings/, quarantine/). These tests pin its guards:
it must refuse before writing anything when it is pointed at the wrong place or a loom
run is live, never regress a session's state, and never delete without a backup."""
from __future__ import annotations

import fcntl
import importlib.util
import json
from pathlib import Path

import pytest

from loom.run import _parse_learnings

SCRIPT = Path(__file__).resolve().parents[2] / "docs/loom-bm2-quarantine-triage/apply.py"
BM2_A = "bm2-363556c5-bf8f-4ada-95ee-79e435d75d34"
BM2_B = "bm2-c2b2fe92-aca1-4140-9f0f-105efcbec759"
ORIGINAL_B = "c2b2fe92-aca1-4140-9f0f-105efcbec759"      # pre-existing quarantine entry: out of scope


@pytest.fixture
def triage():
    spec = importlib.util.spec_from_file_location("bm2_triage_apply", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path):
    loom = tmp_path / "repo" / "loom"
    (loom / "quarantine").mkdir(parents=True)
    (loom / "learnings").mkdir()
    for sid in (BM2_A, BM2_B, ORIGINAL_B):
        (loom / "quarantine" / f"{sid}.md").write_text(f"- learning: broken: yaml for {sid}\n")
    (loom / "state.json").write_text(json.dumps({
        BM2_A: {"state": "quarantined"}, BM2_B: {"state": "quarantined"},
        ORIGINAL_B: {"state": "quarantined"}, "other": {"state": "committed"}}))
    return tmp_path / "repo"


def _state(repo: Path) -> dict:
    return json.loads((repo / "loom" / "state.json").read_text())


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _run(triage, repo, tmp_path, **kw):
    kw.setdefault("running_check", lambda: False)
    return triage.apply(repo, backup_dir=tmp_path / "backup", **kw)


def test_refuses_a_path_with_no_loom_state_and_creates_nothing(triage, tmp_path, capsys):
    # The repo was renamed after this script was first written: pointed at the old name it
    # used to build a fresh loom/ tree and a four-entry state.json there, and report success.
    ghost = tmp_path / "projects" / "old-repo-name"
    assert _run(triage, ghost, tmp_path) == 1
    assert "state.json" in capsys.readouterr().err
    assert not ghost.exists() and not (tmp_path / "backup").exists()


def test_refuses_when_the_state_file_does_not_know_the_bm2_sessions(triage, repo, tmp_path, capsys):
    (repo / "loom" / "state.json").write_text(json.dumps({"other": {"state": "committed"}}))
    before = _tree(repo)
    assert _run(triage, repo, tmp_path) == 1
    assert BM2_A in capsys.readouterr().err
    assert _tree(repo) == before


def test_refuses_while_a_loom_run_is_live_or_holds_the_lock(triage, repo, tmp_path, capsys):
    before = _tree(repo)
    assert _run(triage, repo, tmp_path, running_check=lambda: True) == 1
    assert "running" in capsys.readouterr().err
    with open(repo / "loom" / ".run.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert _run(triage, repo, tmp_path) == 1
    assert ".run.lock" in capsys.readouterr().err
    before[str(Path("loom") / ".run.lock")] = b""
    assert _tree(repo) == before


def test_apply_salvages_settles_and_backs_up_before_deleting(triage, repo, tmp_path):
    malformed = {sid: (repo / "loom" / "quarantine" / f"{sid}.md").read_bytes() for sid in (BM2_A, BM2_B)}
    assert _run(triage, repo, tmp_path) == 0
    state = _state(repo)
    for sid in (BM2_A, BM2_B):
        salvage = repo / "loom" / "learnings" / f"{sid}-salvage.md"
        assert _parse_learnings(salvage.read_text())                       # loom can weave it
        assert state[f"{sid}-salvage"]["state"] == "distilled"
        assert state[sid]["state"] == "committed"
        assert not (repo / "loom" / "quarantine" / f"{sid}.md").exists()
        assert (tmp_path / "backup" / f"{sid}.md").read_bytes() == malformed[sid]
    # the original session's own quarantine entry is a different, older item
    assert (repo / "loom" / "quarantine" / f"{ORIGINAL_B}.md").exists()
    assert state[ORIGINAL_B] == {"state": "quarantined"} and state["other"] == {"state": "committed"}


def test_rerun_changes_nothing_and_never_pulls_a_woven_salvage_back_to_distilled(triage, repo, tmp_path):
    assert _run(triage, repo, tmp_path) == 0
    state = _state(repo)
    state[f"{BM2_A}-salvage"]["state"] = "committed"                        # the next backfill wove it
    (repo / "loom" / "state.json").write_text(json.dumps(state))
    before = _tree(repo)
    assert _run(triage, repo, tmp_path) == 0
    assert _tree(repo) == before
    assert _state(repo)[f"{BM2_A}-salvage"]["state"] == "committed"


def test_refuses_to_overwrite_a_different_artifact_already_in_learnings(triage, repo, tmp_path, capsys):
    clash = repo / "loom" / "learnings" / f"{BM2_A}-salvage.md"
    clash.write_text("- learning: \"something else entirely\"\n")
    before = _tree(repo)
    assert _run(triage, repo, tmp_path) == 1
    assert "differs" in capsys.readouterr().err
    assert _tree(repo) == before
