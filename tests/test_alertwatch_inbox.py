import json
import os
import stat
import threading
import time

from alertwatch import inbox


def put(state, name, payload):
    d = state / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return p


def good(eid, text="broke", **extra):
    return {"version": 1, "id": eid, "captured_at": "2026-11-15T10:00:00.000000Z",
            "chat_id": "1", "text": text, "source": "job", "cwd": "/", "attempts": 0, **extra}


def test_ensure_dirs_mode_700(tmp_path):
    state = tmp_path / "s"
    inbox.ensure_dirs(state)
    for sub in ("", "inbox", "batch", "done", "failed", "runs"):
        assert stat.S_IMODE((state / sub).stat().st_mode) == 0o700, sub


def test_claim_moves_all_json_and_skips_tmp(tmp_path):
    inbox.ensure_dirs(tmp_path)
    put(tmp_path, "a.json", good("a"))
    put(tmp_path, "b.json", good("b"))
    put(tmp_path, ".tmp-c.json", "half")
    entries, broken = inbox.claim_all(tmp_path, "run1")
    assert sorted(e["id"] for e in entries) == ["a", "b"]
    assert broken == []
    assert sorted(p.name for p in (tmp_path / "batch" / "run1").glob("*.json")) == ["a.json", "b.json"]
    assert (tmp_path / "inbox" / ".tmp-c.json").exists()


def test_claim_empty_inbox_makes_no_batch(tmp_path):
    inbox.ensure_dirs(tmp_path)
    assert inbox.claim_all(tmp_path, "run1") == ([], [])
    assert not (tmp_path / "batch" / "run1").exists()


def test_two_claimers_never_share_an_entry(tmp_path):
    inbox.ensure_dirs(tmp_path)
    for i in range(60):
        put(tmp_path, f"{i:03}.json", good(f"{i:03}"))
    got = {}

    def claim(run_id):
        got[run_id] = {e["id"] for e in inbox.claim_all(tmp_path, run_id)[0]}

    threads = [threading.Thread(target=claim, args=(f"r{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    union = set().union(*got.values())
    assert len(union) == 60
    assert sum(len(v) for v in got.values()) == 60


def test_broken_entry_goes_to_failed_with_reason(tmp_path):
    inbox.ensure_dirs(tmp_path)
    put(tmp_path, "x.json", "not json")
    put(tmp_path, "y.json", {"version": 2, "id": "y", "text": "t"})
    put(tmp_path, "z.json", {"version": 1, "id": "z"})
    put(tmp_path, "ok.json", good("ok"))
    entries, broken = inbox.claim_all(tmp_path, "run1")
    assert [e["id"] for e in entries] == ["ok"]
    assert sorted(name for name, _ in broken) == ["x.json", "y.json", "z.json"]
    inbox.move_broken(tmp_path, "run1", broken)
    failed = tmp_path / "failed" / "run1"
    assert (failed / "x.json").exists() and (failed / "x.json.reason").read_text().strip()


def test_finish_moves_batch(tmp_path):
    inbox.ensure_dirs(tmp_path)
    put(tmp_path, "a.json", good("a"))
    inbox.claim_all(tmp_path, "run1")
    inbox.finish(tmp_path, "run1", "done")
    assert (tmp_path / "done" / "run1" / "a.json").exists()
    assert not (tmp_path / "batch" / "run1").exists()


def test_release_counts_attempt_and_fails_at_two(tmp_path):
    inbox.ensure_dirs(tmp_path)
    put(tmp_path, "a.json", good("a"))
    put(tmp_path, "b.json", good("b", attempts=1))
    entries, _ = inbox.claim_all(tmp_path, "run1")
    failed = inbox.release(tmp_path, "run1", entries, count_attempt=True)
    assert failed == ["b"]
    back = json.loads((tmp_path / "inbox" / "a.json").read_text())
    assert back["attempts"] == 1
    assert (tmp_path / "failed" / "run1" / "b.json").exists()
    assert not (tmp_path / "batch" / "run1").exists()


def test_release_without_counting(tmp_path):
    inbox.ensure_dirs(tmp_path)
    put(tmp_path, "a.json", good("a", attempts=1))
    entries, _ = inbox.claim_all(tmp_path, "run1")
    assert inbox.release(tmp_path, "run1", entries, count_attempt=False) == []
    assert json.loads((tmp_path / "inbox" / "a.json").read_text())["attempts"] == 1


def test_add_entry_round_trips_through_claim(tmp_path):
    inbox.ensure_dirs(tmp_path)
    eid = inbox.add_entry(tmp_path, "Check after alert-watch merge", "alert-watch-check")
    entries, broken = inbox.claim_all(tmp_path, "run1")
    assert broken == []
    assert entries[0]["id"] == eid
    assert entries[0]["source"] == "alert-watch-check"
    assert entries[0]["text"] == "Check after alert-watch merge"


def test_oldest_age(tmp_path):
    inbox.ensure_dirs(tmp_path)
    assert inbox.oldest_age_s(tmp_path, time.time()) is None
    put(tmp_path, "a.json", good("a", captured_at="2026-11-15T10:00:00.000000Z"))
    put(tmp_path, "b.json", good("b", captured_at="2026-11-15T10:10:00.000000Z"))
    now = 1794738000.0  # 2026-11-15T10:20:00Z
    assert inbox.oldest_age_s(tmp_path, now) == 1200.0


def test_prune_removes_only_old_batches(tmp_path):
    inbox.ensure_dirs(tmp_path)
    old = tmp_path / "done" / "old"
    new = tmp_path / "failed" / "new"
    old.mkdir()
    new.mkdir()
    (old / "a.json").write_text("{}")
    long_ago = time.time() - 40 * 86400
    os.utime(old, (long_ago, long_ago))
    assert inbox.prune(tmp_path, time.time(), days=30) == 1
    assert not old.exists() and new.exists()


def test_bad_id_is_broken(tmp_path):
    inbox.ensure_dirs(tmp_path)
    put(tmp_path, "evil.json", good("../../etc"))
    entries, broken = inbox.claim_all(tmp_path, "run1")
    assert entries == [] and broken == [("evil.json", "bad id")]
    inbox.move_broken(tmp_path, "run1", broken)


def test_recover_stale_moves_old_batches_to_failed(tmp_path):
    inbox.ensure_dirs(tmp_path)
    put(tmp_path, "a.json", good("a"))
    inbox.claim_all(tmp_path, "dead-run")
    assert inbox.recover_stale(tmp_path) == ["dead-run"]
    assert (tmp_path / "failed" / "dead-run" / "a.json").exists()
    assert not list((tmp_path / "inbox").glob("*.json"))
