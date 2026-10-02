# tests/loom/test_autopromote.py
"""The unattended-promote gate. Auto-promote is only safe because these refuse."""
import subprocess
from pathlib import Path
import pytest
from loom.autopromote import auto_promote_check, set_hold, clear_hold, is_held


def _git(root, *a):
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True)


@pytest.fixture
def wiki(tmp_path):
    """master seeded; loom-shadow one weave ahead; no _staged/."""
    w = tmp_path / "wiki"; w.mkdir()
    _git(w, "init", "-q"); _git(w, "config", "user.email", "t@t"); _git(w, "config", "user.name", "t")
    (w / "people").mkdir(); (w / "people" / "liam.md").write_text("# Liam\nv0\n")
    _git(w, "add", "-A"); _git(w, "commit", "-qm", "seed")
    _git(w, "branch", "loom-shadow")
    _git(w, "checkout", "-q", "loom-shadow")
    (w / "people" / "liam.md").write_text("# Liam\nv1 woven\n")
    _git(w, "add", "-A"); _git(w, "commit", "-qm", "weave: people/liam.md")
    _git(w, "checkout", "-q", "master")
    return w


def _stage_claude_swap(w):
    _git(w, "checkout", "-q", "loom-shadow")
    p = w / "_staged" / ".claude" / "memory" / "new-pref.md"
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text("a preference\n")
    _git(w, "add", "-A"); _git(w, "commit", "-qm", "stage claude swap")
    _git(w, "checkout", "-q", "master")


def test_clean_shadow_promotes_unattended(wiki, tmp_path):
    r = auto_promote_check(wiki_root=wiki, loom_dir=tmp_path / "loom", today="2026-07-21")
    assert r["go"] is True


def test_staged_claude_swap_blocks_unattended_promote(wiki, tmp_path):
    """Wiki prose can land unreviewed; changes to Rex's live memories/skills cannot.
    That path has never run and its blast radius is the agent's own behaviour."""
    _stage_claude_swap(wiki)
    r = auto_promote_check(wiki_root=wiki, loom_dir=tmp_path / "loom", today="2026-07-21")
    assert r["go"] is False
    assert r["reason"] == "staged-claude"


def test_hold_stops_tonight_only(wiki, tmp_path):
    loom = tmp_path / "loom"
    set_hold(loom, "2026-07-21")
    assert auto_promote_check(wiki_root=wiki, loom_dir=loom, today="2026-07-21")["reason"] == "hold"
    # self-expiring: a hold is for ONE night, never a silent permanent stop
    assert auto_promote_check(wiki_root=wiki, loom_dir=loom, today="2026-07-22")["go"] is True


def test_clear_hold_resumes_same_night(wiki, tmp_path):
    loom = tmp_path / "loom"
    set_hold(loom, "2026-07-21")
    clear_hold(loom)
    assert is_held(loom, "2026-07-21") is False
    assert auto_promote_check(wiki_root=wiki, loom_dir=loom, today="2026-07-21")["go"] is True


def test_nothing_to_promote_is_not_an_error(wiki, tmp_path):
    _git(wiki, "merge", "-q", "--no-ff", "-m", "promote", "loom-shadow")
    r = auto_promote_check(wiki_root=wiki, loom_dir=tmp_path / "loom", today="2026-07-21")
    assert r["go"] is False and r["reason"] == "nothing-pending"


def test_check_reports_how_much_would_land(wiki, tmp_path):
    r = auto_promote_check(wiki_root=wiki, loom_dir=tmp_path / "loom", today="2026-07-21")
    assert r["commits"] == 1
    assert r["articles"] == ["people/liam.md"]


def test_dirty_wiki_stands_down_instead_of_crashing(wiki, tmp_path):
    """Rex edits this wiki in Obsidian, so uncommitted changes are NORMAL, not an
    error. promote() rightly refuses a dirty tree — the gate must catch that first
    and stand down quietly, or the nightly run fails (and pings) every time he
    leaves a note half-written."""
    (wiki / "people" / "liam.md").write_text("# Liam\nedited in Obsidian, uncommitted\n")
    r = auto_promote_check(wiki_root=wiki, loom_dir=tmp_path / "loom", today="2026-07-21")
    assert r["go"] is False
    assert r["reason"] == "wiki-dirty"


def test_untracked_junk_also_stands_down(wiki, tmp_path):
    """An untracked stray file is enough to make promote's preflight abort."""
    (wiki / "scratch.txt").write_text("stray\n")
    r = auto_promote_check(wiki_root=wiki, loom_dir=tmp_path / "loom", today="2026-07-21")
    assert r["go"] is False and r["reason"] == "wiki-dirty"


# --- the off-by-one-day fix: a hold names the promote it stops ---------------
from datetime import datetime, timedelta, timezone
from loom.autopromote import next_promote_date

_LOCAL = timezone(timedelta(hours=1))       # the +01:00 the cron logs run in


def test_hold_set_at_0700_stops_the_next_promote_and_nothing_later(wiki, tmp_path):
    """The live bug: a veto typed at 07:00 on day D stored 'D', but the promote
    it targeted runs at 02:00 UTC on D+1 with today == 'D+1' — never matching.
    The hold must store the TARGET promote's date."""
    loom = tmp_path / "loom"
    typed_at = datetime(2026, 7, 23, 7, 0, tzinfo=_LOCAL)          # 07:00 local, day D
    target = next_promote_date(typed_at)
    assert target == "2026-07-24"                                   # D+1, tomorrow's run
    set_hold(loom, target)
    # the promote it was meant to stop (cli passes today = local date at 02:00 UTC)
    r = auto_promote_check(wiki_root=wiki, loom_dir=loom, today="2026-07-24")
    assert r["go"] is False and r["reason"] == "hold"
    # ...and nothing later: self-expiry survives the fix
    assert auto_promote_check(wiki_root=wiki, loom_dir=loom, today="2026-07-25")["go"] is True


def test_hold_set_in_predawn_window_stops_that_same_days_promote():
    """00:00-02:00 UTC (before the boundary) still targets the SAME day's run —
    the only window the old code handled correctly."""
    predawn = datetime(2026, 7, 23, 0, 30, tzinfo=timezone.utc)
    assert next_promote_date(predawn) == "2026-07-23"


def test_next_promote_date_crosses_utc_midnight():
    """23:30 UTC on day D is already past D's boundary — target is D+1."""
    late = datetime(2026, 7, 23, 23, 30, tzinfo=timezone.utc)
    assert next_promote_date(late) == "2026-07-24"


def test_next_promote_date_local_predawn_maps_to_utc_correctly():
    """01:00 local (+01:00) on day D is 00:00 UTC — still before D's boundary."""
    local_predawn = datetime(2026, 7, 23, 1, 0, tzinfo=_LOCAL)
    assert next_promote_date(local_predawn) == "2026-07-23"


def test_next_promote_date_at_exact_boundary_targets_tomorrow():
    """A hold set at the very instant the run fires cannot stop it (the cron
    triggers one second later); it targets the following night."""
    boundary = datetime(2026, 7, 23, 2, 0, 0, tzinfo=timezone.utc)
    assert next_promote_date(boundary) == "2026-07-24"


def test_cli_hold_stores_the_target_promote_date(monkeypatch, tmp_path, capsys):
    """`loom hold` must write next_promote_date(), never today's date."""
    import json as _json
    from loom import cli
    from loom.run import Config
    cfg = Config(projects_dir=tmp_path / "p", loom_dir=tmp_path / "loom",
                 state_path=tmp_path / "loom" / "state.json",
                 wiki_worktree=tmp_path / "shadow", wiki_master=tmp_path / "wiki",
                 claude_dir=tmp_path / ".claude",
                 ledger_path=tmp_path / "loom" / "weave_ledger.json")
    monkeypatch.setattr(cli, "default_config", lambda: cfg)
    monkeypatch.setattr(cli, "next_promote_date", lambda now=None: "2026-07-24")
    assert cli.main(["hold"]) == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["hold"] == "2026-07-24"
    assert is_held(cfg.loom_dir, "2026-07-24") is True
    assert is_held(cfg.loom_dir, "2026-07-23") is False
