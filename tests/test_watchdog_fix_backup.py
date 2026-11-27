"""watchdog.fix_backup: start one backup run when the last verified one is too old."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from watchdog import fix_backup, fixkit
from watchdog.triage import CheckStatus

NOW = 1_800_000_000.0
OLD = CheckStatus("backup", "warn", "no verified off-site backup for 31 h")
OK = CheckStatus("backup", "ok", "off-site backup verified 1 h ago")


def test_healthy_backup_does_nothing():
    assert fix_backup.decide(OK, "inactive", {}, {}, NOW) == ({}, False, [])


def test_old_backup_starts_one_run_then_fixed():
    new, start, msgs = fix_backup.decide(OLD, "inactive", {}, {}, NOW)
    assert start and msgs == []
    msg = fix_backup.record_start(new, OLD, NOW, True)
    assert msg.startswith("STARTED: no verified off-site backup for 31 h")
    # Running: wait.
    new, start, msgs = fix_backup.decide(OLD, "activating", {}, new, NOW + 900)
    assert not start and msgs == []
    new, start, msgs = fix_backup.decide(OK, "inactive", {}, new, NOW + 7200)
    assert msgs == ["FIXED: the off-site backup passed its check again."] and "started_at" not in new


def test_failed_run_is_reported_once_and_not_retried():
    new, _, _ = fix_backup.decide(OLD, "inactive", {}, {}, NOW)
    fix_backup.record_start(new, OLD, NOW, True)
    new, start, msgs = fix_backup.decide(OLD, "failed", {"status": "failed", "failed_stage": "backup"},
                                         new, NOW + 3600)
    assert not start and len(msgs) == 1 and msgs[0].startswith("PROBLEM") and "stage: backup" in msgs[0]
    for t in (NOW + 7200, NOW + 8 * 3600, NOW + 30 * 3600):
        new, start, msgs = fix_backup.decide(OLD, "failed", {}, new, t)
        assert not start and msgs == []
    # A good backup (the timer's own run) clears it and says so, since Rex was told
    # PROBLEM; a later stale spell may start again.
    new, _, msgs = fix_backup.decide(OK, "inactive", {}, new, NOW + 31 * 3600)
    assert msgs == ["FIXED: the off-site backup passed its check again."]
    assert new.get("reported") is None
    _, start, _ = fix_backup.decide(OLD, "inactive", {}, new, NOW + 40 * 3600)
    assert start


def test_never_starts_while_a_backup_runs_or_inside_cooldown():
    _, start, _ = fix_backup.decide(OLD, "activating", {}, {}, NOW)
    assert not start
    _, start, _ = fix_backup.decide(OLD, "inactive", {}, {"last_start": NOW - 3600}, NOW)
    assert not start


def test_start_command_failure_is_reported_and_not_retried():
    new, _, _ = fix_backup.decide(OLD, "inactive", {}, {}, NOW)
    msg = fix_backup.record_start(new, OLD, NOW, False)
    assert msg.startswith("PROBLEM") and "starting a backup run failed" in msg
    _, start, msgs = fix_backup.decide(OLD, "inactive", {}, new, NOW + 7 * 3600)
    assert not start and msgs == []


def _setup(tmp_path, monkeypatch, verified_hours_ago, unit="inactive"):
    state_dir = tmp_path / "restic"
    state_dir.mkdir()
    when = datetime.fromtimestamp(NOW - verified_hours_ago * 3600, timezone.utc).isoformat()
    (state_dir / "last-success.json").write_text(json.dumps({"verified": True, "verified_at": when}))
    monkeypatch.setattr(fix_backup, "STATE_DIR", state_dir)
    monkeypatch.setattr(fixkit, "STATE", tmp_path / "heal")
    monkeypatch.setattr(fix_backup.time, "time", lambda: NOW)
    calls, sent = [], []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw.get("env", {}).get("XDG_RUNTIME_DIR")))
        return SimpleNamespace(returncode=0, stdout=unit + "\n")
    monkeypatch.setattr(fix_backup.subprocess, "run", fake_run)
    monkeypatch.setattr(fixkit, "tg_send", lambda text: sent.append(text) or True)
    return calls, sent


def test_main_starts_the_unit_with_the_user_bus(tmp_path, monkeypatch):
    calls, sent = _setup(tmp_path, monkeypatch, verified_hours_ago=31)
    assert fix_backup.main([]) == 0
    assert calls[-1][0] == ["systemctl", "--user", "start", "--no-block", "restic-offsite.service"]
    assert calls[-1][1] and calls[-1][1].startswith("/run/user/")
    assert len(sent) == 1 and sent[0].startswith("STARTED")
    # The next pass, inside the cooldown, does nothing more.
    calls.clear()
    fix_backup.main([])
    assert all(c[0][2] != "start" for c in calls) and len(sent) == 1


def test_main_does_nothing_when_paused_or_healthy(tmp_path, monkeypatch):
    calls, sent = _setup(tmp_path, monkeypatch, verified_hours_ago=31)
    (tmp_path / "heal").mkdir(parents=True, exist_ok=True)
    (tmp_path / "heal" / "pause").write_text("")
    monkeypatch.setattr(fixkit, "paused", lambda now: True)
    fix_backup.main([])
    assert calls == [] and sent == []


def test_main_healthy(tmp_path, monkeypatch):
    calls, sent = _setup(tmp_path, monkeypatch, verified_hours_ago=2)
    fix_backup.main([])
    assert all("start" not in c[0] for c in calls) and sent == []


def test_dry_run_changes_nothing(tmp_path, monkeypatch, capsys):
    calls, sent = _setup(tmp_path, monkeypatch, verified_hours_ago=40)
    fix_backup.main(["--dry-run"])
    assert all("start" not in c[0] for c in calls) and sent == []
    assert "would start" in capsys.readouterr().out
    assert not (tmp_path / "heal" / "backup.json").exists()


def test_crash_during_the_start_still_counts_it(tmp_path, monkeypatch):
    calls, sent = _setup(tmp_path, monkeypatch, verified_hours_ago=31)
    real = fix_backup.subprocess.run

    def crash(cmd, **kw):
        if "start" in cmd:
            calls.append((cmd, None))
            raise KeyboardInterrupt
        return real(cmd, **kw)
    monkeypatch.setattr(fix_backup.subprocess, "run", crash)
    try:
        fix_backup.main([])
    except KeyboardInterrupt:
        pass
    state = fixkit.load_state("backup")
    assert state.get("last_start") == NOW and state.get("started_at") == NOW
    _, start, _ = fix_backup.decide(fix_backup.check_backup(None, NOW), "inactive", {}, state, NOW + 60)
    assert not start
