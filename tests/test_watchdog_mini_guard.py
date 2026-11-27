"""The watchdog's check on the Mac mini guard."""
import json

from watchdog import run
from watchdog.triage import check_mini_guard

NOW = 1_800_000_000.0


def test_fresh_heartbeat_is_ok():
    assert check_mini_guard(json.dumps({"time": NOW - 130}), NOW).level == "ok"


def test_stale_heartbeat_is_crit():
    s = check_mini_guard(json.dumps({"time": NOW - 1800}), NOW)
    assert s.level == "crit" and "30 min" in s.summary


def test_unreachable_and_unreadable_are_separate_warnings():
    a = check_mini_guard(None, NOW)
    b = check_mini_guard("garbage", NOW)
    assert a.level == b.level == "warn" and a.summary != b.summary


def test_collect_skips_the_check_until_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_GUARD_KEY", str(tmp_path / "absent"))
    statuses, _ = run.collect(int(NOW))
    assert not [s for s in statuses if s.name == "mini-guard"]


def test_collect_reads_the_heartbeat_over_ssh(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text("x")
    monkeypatch.setenv("MINI_GUARD_KEY", str(key))
    for answer, level, word in [(json.dumps({"time": NOW - 60}), "ok", "ran"),
                                (None, "warn", "cannot reach"),
                                ("Welcome!\n", "warn", "unreadable")]:
        monkeypatch.setattr(run, "read_guard_heartbeat", lambda k, a=answer: a)
        statuses, _ = run.collect(int(NOW))
        guard = [s for s in statuses if s.name == "mini-guard"]
        assert len(guard) == 1 and guard[0].level == level and word in guard[0].summary


def test_ssh_exit_codes_are_told_apart(monkeypatch, tmp_path):
    import subprocess
    for rc, out, expect in [(255, "", None), (1, "", ""), (0, '{"time": 1}', '{"time": 1}')]:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, rc, out, ""))
        assert run.read_guard_heartbeat(tmp_path / "k") == expect
