import json
import subprocess

from watchdog import run
from watchdog.triage import check_outside_alarm

NOW = 1_800_000_000


def status(**kw):
    d = {"now": NOW, "alerts": True, "lastCron": NOW - 120,
         "checks": {"bebop-vm": {"lastSeen": NOW - 60, "announced": "up"},
                    "mini-guard": {"lastSeen": NOW - 30, "announced": "up"}}}
    d.update(kw)
    return json.dumps(d)


def test_healthy():
    s = check_outside_alarm(status(), NOW)
    assert (s.level, s.summary) == ("ok", "outside alarm ran 2 min ago")


def test_alerts_off_is_noted_but_ok():
    s = check_outside_alarm(status(alerts=False), NOW)
    assert s.level == "ok" and "Telegram not set up" in s.summary


def test_unreachable_warns():
    assert check_outside_alarm(None, NOW).level == "warn"


def test_unreadable_warns():
    assert check_outside_alarm("<html>", NOW).level == "warn"
    assert check_outside_alarm("[1]", NOW).level == "warn"


def test_stale_cron_is_crit():
    s = check_outside_alarm(status(lastCron=NOW - 25 * 60), NOW)
    assert s.level == "crit" and "25 min ago" in s.summary


def test_never_ran_is_crit():
    s = check_outside_alarm(status(lastCron=None), NOW)
    assert s.level == "crit" and "never" in s.summary


def test_reported_down_warns():
    checks = {"mini-guard": {"lastSeen": NOW - 3600, "announced": "down"}}
    s = check_outside_alarm(status(checks=checks), NOW)
    assert s.level == "warn" and s.summary.endswith("mini-guard")


def test_reader_keeps_secret_off_command_line(tmp_path, monkeypatch):
    secret = tmp_path / "secret"
    secret.write_text("s3cret\n")
    seen = {}

    def fake_run(args, **kw):
        seen["args"], seen["input"] = args, kw.get("input")
        return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")
    monkeypatch.setattr(run.subprocess, "run", fake_run)
    assert run.read_outside_alarm(secret) == "{}"
    assert "s3cret" not in " ".join(seen["args"])
    assert seen["input"] == 'url = "https://bebop-uptime.rex-651.workers.dev/status/s3cret"\n'


def test_reader_failure_is_none(tmp_path, monkeypatch):
    secret = tmp_path / "secret"
    secret.write_text("x")
    monkeypatch.setattr(run.subprocess, "run",
                        lambda args, **kw: subprocess.CompletedProcess(args, 22, stdout="", stderr=""))
    assert run.read_outside_alarm(secret) is None
    assert run.read_outside_alarm(tmp_path / "missing") is None
