import json
from loom import cli

def test_default_config_paths():
    cfg = cli.default_config()
    assert str(cfg.projects_dir).endswith(".claude/projects")
    assert str(cfg.state_path).endswith("loom/state.json")

def test_default_config_has_v1_paths():
    cfg = cli.default_config()
    assert str(cfg.wiki_worktree).endswith("wiki-loom-shadow")
    assert str(cfg.ledger_path).endswith("loom/weave_ledger.json")
    assert str(cfg.claude_dir).endswith(".claude")

def test_backfill_uses_venice_skips_distill_and_caps(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "absorb",
                        lambda cfg, **k: seen.update(k) or {"committed": 0})
    rc = cli.main(["backfill", "--max-targets", "3", "--max-per-target", "2"])
    assert rc == 0
    assert seen["backend"] == "venice" and seen["shadow"] is False
    assert seen["max_targets"] == 3 and seen["max_per_target"] == 2
    assert seen["distill"] is False          # default: weave-only, no distill
    assert seen["max_distill"] is None

def test_backfill_distill_flag_enables_venice_distill(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "absorb",
                        lambda cfg, **k: seen.update(k) or {"committed": 0})
    rc = cli.main(["backfill", "--distill", "25"])
    assert rc == 0
    assert seen["backend"] == "venice"
    assert seen["distill"] is True and seen["max_distill"] == 25

def test_absorb_live_flag_uses_claude_and_distills(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "absorb",
                        lambda cfg, **k: seen.update(k) or {"committed": 0})
    cli.main(["absorb", "--live"])
    assert seen["backend"] == "claude" and seen["shadow"] is False
    assert seen.get("distill", True) is True   # absorb distills (default)

def test_promote_and_rollback_dispatch(monkeypatch):
    calls = {}
    monkeypatch.setattr(cli, "promote", lambda **k: calls.setdefault("promote", k) or {"applied": 1})
    monkeypatch.setattr(cli, "rollback", lambda **k: calls.setdefault("rollback", k) or {"restored": 1})
    assert cli.main(["promote"]) == 0
    assert cli.main(["rollback", "--ts", "20260608T010101"]) == 0
    assert "promote" in calls and calls["rollback"]["ts"] == "20260608T010101"


def test_promote_auto_skips_when_gate_refuses(monkeypatch, capsys):
    """--auto must consult the gate and NOT promote when it says no."""
    called = {"promote": 0}
    monkeypatch.setattr(cli, "auto_promote_check",
                        lambda **k: {"go": False, "reason": "staged-claude",
                                     "commits": 3, "articles": [], "staged": ["x"]})
    monkeypatch.setattr(cli, "promote", lambda **k: called.update(promote=1) or {})
    rc = cli.main(["promote", "--auto"])
    assert rc == 0
    assert called["promote"] == 0                      # did NOT promote
    assert "staged-claude" in capsys.readouterr().out


def test_promote_auto_promotes_when_gate_allows(monkeypatch):
    called = {}
    monkeypatch.setattr(cli, "auto_promote_check",
                        lambda **k: {"go": True, "reason": "", "commits": 2,
                                     "articles": ["a.md"], "staged": []})
    monkeypatch.setattr(cli, "promote", lambda **k: called.update(k) or {"applied": 0})
    assert cli.main(["promote", "--auto"]) == 0
    assert called                                       # promote ran


def test_promote_without_auto_still_promotes_directly(monkeypatch):
    called = {}
    monkeypatch.setattr(cli, "promote", lambda **k: called.update(k) or {"applied": 0})
    assert cli.main(["promote"]) == 0
    assert called


def test_hold_sets_and_clears(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cli, "set_hold", lambda d, day: seen.update(set=day))
    monkeypatch.setattr(cli, "clear_hold", lambda d: seen.update(cleared=True))
    cli.main(["hold"])
    assert "set" in seen
    cli.main(["hold", "--clear"])
    assert seen.get("cleared") is True


def test_auto_promote_output_says_what_landed(monkeypatch, capsys):
    """The briefing line is built from this JSON. Without the article list it
    would cheerfully report '0 articles landed' after a successful promote."""
    monkeypatch.setattr(cli, "auto_promote_check",
                        lambda **k: {"go": True, "reason": "", "commits": 7,
                                     "articles": ["a.md", "b.md"], "staged": [], "dirty": False})
    monkeypatch.setattr(cli, "promote", lambda **k: {"applied": 0, "ts": "T"})
    cli.main(["promote", "--auto"])
    out = json.loads(capsys.readouterr().out)
    assert out["promoted"] is True
    assert out["articles"] == ["a.md", "b.md"]
    assert out["commits"] == 7


# ---------------------------------------------------------------- resolve

def _tmp_cfg(tmp_path):
    from loom.run import Config
    return Config(projects_dir=tmp_path / "projects", loom_dir=tmp_path / "loom",
                  state_path=tmp_path / "loom" / "state.json",
                  wiki_worktree=tmp_path / "shadow", wiki_master=tmp_path / "wiki",
                  claude_dir=tmp_path / ".claude",
                  ledger_path=tmp_path / "loom" / "weave_ledger.json")


def _quarantined_ledger(cfg, lid="sid#0", target="memory/foo.md"):
    from loom.ledger import WeaveLedger
    led = WeaveLedger(cfg.ledger_path)
    led.plan(lid, target, "append")
    led.quarantine(lid, "sentinel:priv-write")
    learnings = cfg.loom_dir / "learnings"
    learnings.mkdir(parents=True, exist_ok=True)
    (learnings / "sid.md").write_text(
        "- type: fact\n  subject: sudo docs\n  learning: >\n    sudoers is documented\n",
        encoding="utf-8")
    return led


def test_resolve_accept_marks_committed_and_clears_pending(monkeypatch, tmp_path, capsys):
    from loom.ledger import WeaveLedger
    cfg = _tmp_cfg(tmp_path)
    monkeypatch.setattr(cli, "default_config", lambda: cfg)
    _quarantined_ledger(cfg)
    rc = cli.main(["resolve", "sid#0", "--accept"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["resolved"] == "sid#0" and out["status"] == "committed"
    assert out["reason"] == "hand-resolved" and out["was"] == "quarantined"
    assert out["target"] == "memory/foo.md"
    assert "sudoers is documented" in out["text"]      # human saw what they resolved
    led = WeaveLedger(cfg.ledger_path)                  # re-read from disk
    assert led.status_of("sid#0") == "committed"
    assert led.entry("sid#0")["reason"] == "hand-resolved"
    # what `loom pending` builds its decisions from — must now be empty
    assert led.quarantined() == []


def test_resolve_reject_marks_rejected(monkeypatch, tmp_path, capsys):
    from loom.ledger import WeaveLedger
    cfg = _tmp_cfg(tmp_path)
    monkeypatch.setattr(cli, "default_config", lambda: cfg)
    _quarantined_ledger(cfg)
    rc = cli.main(["resolve", "sid#0", "--reject"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["status"] == "rejected" and out["reason"] == "hand-rejected"
    led = WeaveLedger(cfg.ledger_path)
    assert led.status_of("sid#0") == "rejected"
    assert led.quarantined() == []


def test_resolve_unknown_id_errors_without_writing(monkeypatch, tmp_path, capsys):
    cfg = _tmp_cfg(tmp_path)
    monkeypatch.setattr(cli, "default_config", lambda: cfg)
    rc = cli.main(["resolve", "nope#9", "--accept"])
    assert rc == 1
    assert "unknown learning id" in json.loads(capsys.readouterr().out)["error"]
    assert not cfg.ledger_path.exists()                 # nothing was created


def test_resolve_requires_exactly_one_of_accept_reject(monkeypatch, tmp_path):
    import pytest
    cfg = _tmp_cfg(tmp_path)
    monkeypatch.setattr(cli, "default_config", lambda: cfg)
    with pytest.raises(SystemExit):
        cli.main(["resolve", "sid#0"])                   # neither flag
    with pytest.raises(SystemExit):
        cli.main(["resolve", "sid#0", "--accept", "--reject"])


def test_resolve_refuses_non_quarantined_entries(monkeypatch, tmp_path, capsys):
    """An id typo must never silently rewrite a settled entry (council HIGH)."""
    from loom.ledger import WeaveLedger
    cfg = _tmp_cfg(tmp_path)
    monkeypatch.setattr(cli, "default_config", lambda: cfg)
    led = WeaveLedger(cfg.ledger_path)
    led.plan("sid#1", "memory/bar.md", "append")
    led.mark("sid#1", "committed", reason="woven")
    rc = cli.main(["resolve", "sid#1", "--reject"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["status"] == "committed"
    led2 = WeaveLedger(cfg.ledger_path)
    assert led2.status_of("sid#1") == "committed"       # untouched
    assert led2.entry("sid#1")["reason"] == "woven"
