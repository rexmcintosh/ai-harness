import json
import os
from pathlib import Path
from diem.state import Estimates, Reviewed, Lock, pause_until, set_pause, clear_pause

SEEDS = {"images": {"cost": 2.0, "duration_s": 180}}

def test_estimates_seed_then_ema(tmp_path):
    e = Estimates(tmp_path / "estimates.json", SEEDS)
    assert e.estimate("images") == (2.0, 180.0)
    assert e.estimate("unknown") == (1.0, 300.0)
    e.record("images", cost=4.0, duration_s=200)
    cost, dur = e.estimate("images")
    assert cost == 2.0 + 0.3 * (4.0 - 2.0)          # 2.6
    assert dur == 180 + 0.3 * (200 - 180)           # 186
    # persisted: fresh instance sees the update
    e2 = Estimates(tmp_path / "estimates.json", SEEDS)
    assert e2.estimate("images") == (cost, dur)

def test_reviewed_roundtrip(tmp_path):
    r = Reviewed(tmp_path / "reviewed.json")
    assert r.get("/r/a") is None
    r.set("/r/a", "abc123")
    assert Reviewed(tmp_path / "reviewed.json").get("/r/a") == "abc123"

def test_lock_excludes_second_holder(tmp_path):
    a, b = Lock(tmp_path / "l"), Lock(tmp_path / "l")
    assert a.acquire() and not b.acquire()
    a.release()
    assert b.acquire()

def test_lock_breaks_stale_dead_pid(tmp_path):
    (tmp_path / "l").write_text("999999999")  # no such pid
    assert Lock(tmp_path / "l").acquire()

def test_pause_roundtrip(tmp_path):
    assert pause_until(tmp_path) is None
    set_pause(tmp_path, "2026-07-04T01:00:00")
    assert pause_until(tmp_path) == "2026-07-04T01:00:00"
    clear_pause(tmp_path)
    assert pause_until(tmp_path) is None

def test_lock_acquire_survives_vanishing_lockfile(tmp_path, monkeypatch):
    lock = Lock(tmp_path / "l")
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self: True if self == lock.path else real_exists(self))
    assert lock.acquire()  # file "exists" but read finds nothing — must not raise

def test_atomic_write_leaves_no_tmp_and_persists(tmp_path):
    r = Reviewed(tmp_path / "reviewed.json")
    r.set("/r/a", "abc")
    assert list(tmp_path.glob("*.tmp")) == []
    assert Reviewed(tmp_path / "reviewed.json").get("/r/a") == "abc"
