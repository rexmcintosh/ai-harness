# tests/diem/test_discover.py
import json
import subprocess
from pathlib import Path
from diem.config import DiemConfig
from diem.discover import discover
from diem.queue import QueueDir
from diem.state import Reviewed

NOW = "2026-07-03T21:00:00"

def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)

def _mkrepo(tmp_path, name):
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "one")
    return repo

def _cfg(tmp_path, repos):
    return DiemConfig(daily_diem=100.0, repos=[Path(r) for r in repos],
                      state_dir=tmp_path / "state")

def _bits(tmp_path):
    return QueueDir(tmp_path / "state"), Reviewed(tmp_path / "state" / "reviewed.json")

def test_first_sighting_baselines_without_review(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    q, rev = _bits(tmp_path)
    added = discover(_cfg(tmp_path, [repo]), q, rev, NOW)
    assert added == [] and rev.get(str(repo)) is not None

def test_new_commits_queue_range_review(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    q, rev = _bits(tmp_path)
    discover(_cfg(tmp_path, [repo]), q, rev, NOW)          # baseline
    old = rev.get(str(repo))
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "two")
    added = discover(_cfg(tmp_path, [repo]), q, rev, NOW)
    assert [i.type for i in added] == ["review"]
    assert added[0].payload["range"].startswith(old)

def test_dirty_tree_queues_diff_review(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    q, rev = _bits(tmp_path)
    discover(_cfg(tmp_path, [repo]), q, rev, NOW)
    (repo / "x.py").write_text("x = 1\n")
    added = discover(_cfg(tmp_path, [repo]), q, rev, NOW)
    assert [i.payload.get("diff") for i in added] == [True]
    # second discovery same night: deduped
    assert discover(_cfg(tmp_path, [repo]), q, rev, NOW) == []

def test_feedstock_shortfall_queues_images(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    so_dir = repo / ".diem"; so_dir.mkdir()
    cand = repo / "candidates"; cand.mkdir()
    (cand / "t1.png").write_bytes(b"x")
    (so_dir / "standing-order.json").write_text(json.dumps(
        {"target": 4, "candidates_dir": "candidates",
         "command": ["python", "make.py"]}))
    q, rev = _bits(tmp_path)
    added = discover(_cfg(tmp_path, [repo]), q, rev, NOW)
    imgs = [i for i in added if i.type == "images"]
    assert len(imgs) == 1
    assert imgs[0].payload == {"repo": str(repo), "count": 3}  # no command key

def test_malformed_command_not_queued(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    so_dir = repo / ".diem"; so_dir.mkdir()
    cand = repo / "candidates"; cand.mkdir()
    (so_dir / "standing-order.json").write_text(json.dumps(
        {"target": 4, "candidates_dir": "candidates",
         "command": "python make.py"}))  # string, not a list — doomed item
    q, rev = _bits(tmp_path)
    added = discover(_cfg(tmp_path, [repo]), q, rev, NOW)
    assert all(i.type != "images" for i in added)

def test_no_standing_order_no_images(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    q, rev = _bits(tmp_path)
    assert all(i.type != "images" for i in discover(_cfg(tmp_path, [repo]), q, rev, NOW))

def test_broken_repo_skipped(tmp_path):
    notrepo = tmp_path / "plain"; notrepo.mkdir()
    q, rev = _bits(tmp_path)
    assert discover(_cfg(tmp_path, [notrepo]), q, rev, NOW) == []

def test_archived_tonight_not_rediscovered(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    q, rev = _bits(tmp_path)
    discover(_cfg(tmp_path, [repo]), q, rev, NOW)          # baseline
    (repo / "x.py").write_text("x = 1\n")
    day = "2026-07-03T01:00:00"
    added = discover(_cfg(tmp_path, [repo]), q, rev, NOW, day_start_iso=day)
    assert len(added) == 1
    q.archive(added[0], {"ok": True})                       # ran at 21:00 checkpoint
    # tree still dirty at the 23:00 checkpoint — must NOT re-queue tonight
    assert discover(_cfg(tmp_path, [repo]), q, rev, NOW, day_start_iso=day) == []
    # next DIEM day: eligible again
    assert len(discover(_cfg(tmp_path, [repo]), q, rev,
                        "2026-07-04T21:00:00", day_start_iso="2026-07-04T01:00:00")) == 1

def test_permission_blocked_standing_order_skips_not_crash(tmp_path):
    import os
    repo = _mkrepo(tmp_path, "a")
    blocked = repo / ".diem"; blocked.mkdir()
    (blocked / "standing-order.json").write_text(json.dumps(
        {"target": 2, "candidates_dir": "c", "command": ["x"]}))
    ok_repo = _mkrepo(tmp_path, "b")
    os.chmod(blocked, 0o000)
    try:
        q, rev = _bits(tmp_path)
        added = discover(_cfg(tmp_path, [repo, ok_repo]), q, rev, NOW)
        assert added == []                      # both repos baselined, no crash
        assert rev.get(str(ok_repo)) is not None  # second repo still processed
    finally:
        os.chmod(blocked, 0o755)

def test_both_triggers_queue_single_review_preferring_range(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    q, rev = _bits(tmp_path)
    discover(_cfg(tmp_path, [repo]), q, rev, NOW)   # baseline
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "two")
    (repo / "x.py").write_text("x = 1\n")
    added = discover(_cfg(tmp_path, [repo]), q, rev, NOW)
    assert len(added) == 1 and "range" in added[0].payload

def test_moving_head_not_rereviewed_same_night(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    q, rev = _bits(tmp_path)
    discover(_cfg(tmp_path, [repo]), q, rev, NOW)   # baseline
    day = "2026-07-03T01:00:00"
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "two")
    added = discover(_cfg(tmp_path, [repo]), q, rev, NOW, day_start_iso=day)
    q.archive(added[0], {"ok": True})
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "three")
    assert discover(_cfg(tmp_path, [repo]), q, rev, NOW, day_start_iso=day) == []

def test_absolute_candidates_dir_skipped(tmp_path):
    repo = _mkrepo(tmp_path, "a")
    (repo / ".diem").mkdir()
    (repo / ".diem" / "standing-order.json").write_text(json.dumps(
        {"target": 99, "candidates_dir": "/etc", "command": ["x"]}))
    q, rev = _bits(tmp_path)
    assert all(i.type != "images"
               for i in discover(_cfg(tmp_path, [repo]), q, rev, NOW))
