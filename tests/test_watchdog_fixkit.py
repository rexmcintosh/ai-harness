"""watchdog.fixkit: the parts the automatic fixers share."""
import json
import os

from watchdog import fixkit


def test_child_env_gives_systemctl_user_its_bus(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert fixkit.child_env()["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}"
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/elsewhere")
    assert fixkit.child_env()["XDG_RUNTIME_DIR"] == "/elsewhere"


def test_pause_file_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(fixkit, "STATE", tmp_path)
    assert not fixkit.paused(1000.0)
    (tmp_path / "pause").write_text("")
    mtime = (tmp_path / "pause").stat().st_mtime
    assert fixkit.paused(mtime + 60)
    assert not fixkit.paused(mtime + fixkit.MAX_PAUSE_S + 1)
    assert not (tmp_path / "pause").exists()


def test_one_run_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setattr(fixkit, "STATE", tmp_path)
    first = fixkit.take_lock("x")
    assert first is not None and fixkit.take_lock("x") is None
    assert fixkit.take_lock("y") is not None
    first.close()
    assert fixkit.take_lock("x") is not None


def test_state_round_trip_and_bad_file(tmp_path, monkeypatch):
    monkeypatch.setattr(fixkit, "STATE", tmp_path)
    assert fixkit.load_state("s") == {}
    fixkit.save_state("s", {"a": 1})
    assert fixkit.load_state("s") == {"a": 1}
    (tmp_path / "s.json").write_text("[1]")
    assert fixkit.load_state("s") == {}


def test_outbox_is_per_fixer_bounded_and_kept_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(fixkit, "STATE", tmp_path)
    fixkit.send_all(["a", "b"], "one", sender=lambda t: False)
    assert json.loads((tmp_path / "one.json").read_text()) == ["a", "b"]
    assert not (tmp_path / "two.json").exists()
    sent = []
    fixkit.send_all([f"m{i}" for i in range(8)], "one", sender=lambda t: sent.append(t) or True)
    assert sent == ["a", "b", "m0", "m1", "m2"]
    assert json.loads((tmp_path / "one.json").read_text()) == ["m3", "m4", "m5", "m6", "m7"]


def test_trim_keeps_every_problem_first():
    items = ["PROBLEM: old"] + [f"FIXED: {i}" for i in range(60)] + ["PROBLEM: new"]
    out = fixkit.trim(items, keep=5)
    assert out == ["PROBLEM: old", "FIXED: 57", "FIXED: 58", "FIXED: 59", "PROBLEM: new"]
    assert fixkit.trim(["a", "b"], keep=5) == ["a", "b"]
    many = [f"PROBLEM: {i}" for i in range(8)]
    assert fixkit.trim(many, keep=3) == ["PROBLEM: 5", "PROBLEM: 6", "PROBLEM: 7"]
