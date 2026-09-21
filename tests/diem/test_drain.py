# tests/diem/test_drain.py
from datetime import datetime
from pathlib import Path
import pytest
from diem.config import DiemConfig, Checkpoint
from diem.drain import run_checkpoint, floor_for, next_deadline
from diem.queue import QueueDir, new_item
from diem.runners import RunResult
from diem.state import Estimates, Reviewed, set_pause

NOW = datetime(2026, 7, 3, 23, 5)
NOW_ISO = "2026-07-03T23:05:00"

class FakeBalance:
    """Scripted balance readings; drains by `burn` per run when scripted list empties."""
    def __init__(self, readings):
        self.readings = list(readings)
        self.calls = 0
    def diem_balance(self):
        self.calls += 1
        return self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]

class FakeRunner:
    def __init__(self, results=None):
        self.ran = []
        self.results = results or {}
    def __call__(self, item, **kw):
        self.ran.append(item)
        return self.results.get(item.id, RunResult(True, 60.0, output_path="/o"))

def _cfg(tmp_path, **kw):
    base = dict(daily_diem=100.0, repos=[], state_dir=tmp_path / "state",
                outputs_dir=tmp_path / "out",
                checkpoints=[Checkpoint("21:00", 0.40), Checkpoint("23:00", 0.15),
                             Checkpoint("00:15", 0.0)])
    base.update(kw)
    return DiemConfig(**base)

def _bits(tmp_path, cfg):
    q = QueueDir(cfg.state_dir)
    return (q, Estimates(cfg.state_dir / "estimates.json", cfg.seeds),
            Reviewed(cfg.state_dir / "reviewed.json"))

def test_floor_for_picks_latest_checkpoint():
    cfg = _cfg(Path("/tmp/x"))
    assert floor_for(cfg, datetime(2026, 7, 3, 21, 30)) == 40.0
    assert floor_for(cfg, datetime(2026, 7, 3, 23, 5)) == 15.0
    assert floor_for(cfg, datetime(2026, 7, 4, 0, 20)) == 0.0
    assert floor_for(cfg, datetime(2026, 7, 3, 20, 0)) == 40.0  # pre-first: conservative
    # after midnight but before 00:15, last-fired checkpoint is yesterday 23:00
    assert floor_for(cfg, datetime(2026, 7, 4, 0, 5)) == 15.0

def test_next_deadline_before_and_after_midnight():
    cfg = _cfg(Path("/tmp/x"))
    assert next_deadline(cfg, datetime(2026, 7, 3, 23, 5)) == datetime(2026, 7, 4, 0, 50)
    assert next_deadline(cfg, datetime(2026, 7, 4, 0, 20)) == datetime(2026, 7, 4, 0, 50)
    assert next_deadline(cfg, datetime(2026, 7, 4, 0, 55)) == datetime(2026, 7, 4, 0, 50)
    assert next_deadline(cfg, datetime(2026, 7, 4, 1, 30)) == datetime(2026, 7, 5, 0, 50)

def test_drains_until_floor(tmp_path):
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    for n in range(3):
        q.add(new_item("ask", {"question": f"q{n}", "panel": "decision"}, created=NOW_ISO))
    # floor at 23:05 = 15.0; readings: 40 → run → 25 → run → 14 (≤ floor, stop)
    bal = FakeBalance([40.0, 25.0, 25.0, 14.0, 14.0])
    r = FakeRunner()
    summary = run_checkpoint(cfg, now=NOW, balance=bal, queue=q,
                             estimates=est, reviewed=rev, runner=r)
    assert len(r.ran) == 2 and summary["aborted"] is None
    assert len(q.pending(NOW_ISO)) == 1  # third ask survives for the 00:15 pass

def test_balance_unavailable_aborts(tmp_path):
    from diem.balance import BalanceUnavailable
    class Down:
        def diem_balance(self):
            raise BalanceUnavailable("nope")
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    q.add(new_item("ask", {"question": "q", "panel": "decision"}, created=NOW_ISO))
    summary = run_checkpoint(cfg, now=NOW, balance=Down(), queue=q,
                             estimates=est, reviewed=rev, runner=FakeRunner())
    assert summary["aborted"] == "balance_unavailable"
    assert len(q.pending(NOW_ISO)) == 1  # nothing consumed

def test_deadline_skips_long_jobs(tmp_path):
    cfg = _cfg(tmp_path, seeds={"images": {"cost": 1.0, "duration_s": 3600},
                                "ask": {"cost": 1.0, "duration_s": 60}})
    q, est, rev = _bits(tmp_path, cfg)
    q.add(new_item("images", {"repo": "/r", "count": 9,
                              "command": ["x"]}, created=NOW_ISO))
    q.add(new_item("ask", {"question": "q", "panel": "decision"}, created=NOW_ISO))
    late = datetime(2026, 7, 4, 0, 30)  # 20 min to 00:50 deadline
    r = FakeRunner()
    summary = run_checkpoint(cfg, now=late, balance=FakeBalance([50.0, 40.0, 40.0]),
                             queue=q, estimates=est, reviewed=rev, runner=r)
    assert [i.type for i in r.ran] == ["ask"]  # images (60 min est) skipped
    assert any(s["reason"] == "deadline" for s in summary["skipped"])

def test_paused_aborts(tmp_path):
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    set_pause(cfg.state_dir, "2026-07-04T01:00:00")
    summary = run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0]), queue=q,
                             estimates=est, reviewed=rev, runner=FakeRunner())
    assert summary["aborted"] == "paused"

def test_failure_requeues_then_archives(tmp_path):
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    it = new_item("ask", {"question": "q", "panel": "decision"}, created=NOW_ISO)
    q.add(it)
    fail = FakeRunner({it.id: RunResult(False, 5.0, error="exit 2: boom")})
    run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0, 50.0, 50.0]), queue=q,
                   estimates=est, reviewed=rev, runner=fail)
    pend = q.pending(NOW_ISO)
    assert len(pend) == 1 and pend[0].attempts == 1  # requeued once
    run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0, 50.0, 50.0]), queue=q,
                   estimates=est, reviewed=rev, runner=fail)
    assert q.pending(NOW_ISO) == []  # attempts == max_attempts → archived failed

def test_review_range_success_advances_reviewed_sha(tmp_path):
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    rev.set("/r/a", "old")
    q.add(new_item("review", {"repo": "/r/a", "range": "old..new", "head": "new"},
                   created=NOW_ISO))
    run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0, 49.0, 49.0]), queue=q,
                   estimates=est, reviewed=rev, runner=FakeRunner())
    assert rev.get("/r/a") == "new"

def test_filler_backfill_tops_up_empty_queue(tmp_path):
    cfg = _cfg(tmp_path, backfill_max_per_night=2, backfill_chunk=3)
    q, est, rev = _bits(tmp_path, cfg)
    r = FakeRunner()
    run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0, 40.0, 30.0, 20.0, 14.0]),
                   queue=q, estimates=est, reviewed=rev, runner=r)
    assert 1 <= len(r.ran) <= 2
    assert all(i.type == "backfill" and i.payload["max_targets"] == 3 for i in r.ran)
    assert q.night_count("backfill", "2026-07-03T01:00:00") <= 2  # cap respected

def test_late_checkpoint_in_gap_aborts(tmp_path):
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    q.add(new_item("ask", {"question": "q", "panel": "decision"}, created=NOW_ISO))
    r = FakeRunner()
    summary = run_checkpoint(cfg, now=datetime(2026, 7, 4, 0, 55),
                             balance=FakeBalance([80.0]), queue=q,
                             estimates=est, reviewed=rev, runner=r)
    assert summary["aborted"] == "past_deadline" and r.ran == []
    assert len(q.pending(NOW_ISO)) == 1  # nothing consumed

def test_post_reset_run_aborts(tmp_path):
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    summary = run_checkpoint(cfg, now=datetime(2026, 7, 4, 1, 30),
                             balance=FakeBalance([100.0]), queue=q,
                             estimates=est, reviewed=rev, runner=FakeRunner())
    assert summary["aborted"] == "no_checkpoint_fired"

def test_mid_day_run_aborts(tmp_path):
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    summary = run_checkpoint(cfg, now=datetime(2026, 7, 3, 20, 0),
                             balance=FakeBalance([100.0]), queue=q,
                             estimates=est, reviewed=rev, runner=FakeRunner())
    assert summary["aborted"] == "no_checkpoint_fired"

def test_skipped_entries_unique(tmp_path):
    cfg = _cfg(tmp_path, seeds={"images": {"cost": 1.0, "duration_s": 3600},
                                "ask": {"cost": 1.0, "duration_s": 60}})
    q, est, rev = _bits(tmp_path, cfg)
    q.add(new_item("images", {"repo": "/r", "count": 9, "command": ["x"]}, created=NOW_ISO))
    q.add(new_item("ask", {"question": "a", "panel": "decision"}, created=NOW_ISO))
    q.add(new_item("ask", {"question": "b", "panel": "decision"}, created=NOW_ISO))
    summary = run_checkpoint(cfg, now=datetime(2026, 7, 4, 0, 30),
                             balance=FakeBalance([50.0, 45.0, 45.0, 40.0, 40.0]),
                             queue=q, estimates=est, reviewed=rev, runner=FakeRunner())
    ids = [s["id"] for s in summary["skipped"]]
    assert len(ids) == len(set(ids))

def test_estimates_recorded_from_balance_delta(tmp_path):
    cfg = _cfg(tmp_path)
    q, est, rev = _bits(tmp_path, cfg)
    q.add(new_item("ask", {"question": "q", "panel": "decision"}, created=NOW_ISO))
    run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0, 47.0, 14.0]), queue=q,
                   estimates=est, reviewed=rev, runner=FakeRunner())
    cost, _dur = est.estimate("ask")
    assert cost == pytest.approx(0.5 + 0.3 * (3.0 - 0.5))  # EMA toward observed 3.0


# --- UTC / midnight-reset (00:00 UTC epoch) config ---

def _utc_cfg(**kw):
    return _cfg(Path("/tmp/x"), reset="00:00", deadline="23:50",
                checkpoints=[Checkpoint("21:00", 0.40), Checkpoint("23:00", 0.15),
                             Checkpoint("23:45", 0.0)], **kw)

def test_next_deadline_midnight_reset():
    cfg = _utc_cfg()
    # evening: deadline is 23:50 the same evening, just before the 00:00 reset
    assert next_deadline(cfg, datetime(2026, 7, 3, 23, 5)) == datetime(2026, 7, 3, 23, 50)
    assert next_deadline(cfg, datetime(2026, 7, 3, 21, 0)) == datetime(2026, 7, 3, 23, 50)
    # just after the reset: deadline rolls to the coming night
    assert next_deadline(cfg, datetime(2026, 7, 4, 0, 30)) == datetime(2026, 7, 4, 23, 50)

def test_floor_for_utc_evening_checkpoints():
    cfg = _utc_cfg()
    assert floor_for(cfg, datetime(2026, 7, 3, 20, 0)) == 40.0  # pre-first: conservative
    assert floor_for(cfg, datetime(2026, 7, 3, 21, 30)) == 40.0
    assert floor_for(cfg, datetime(2026, 7, 3, 23, 5)) == 15.0
    assert floor_for(cfg, datetime(2026, 7, 3, 23, 50)) == 0.0


class FileRunner(FakeRunner):
    """FakeRunner that also writes a loom summary JSON for backfill items,
    mimicking runners.run_item saving stdout to an output log."""
    def __init__(self, tmp_path, summary_json):
        super().__init__()
        self.dir = tmp_path / "joblogs"
        self.dir.mkdir(exist_ok=True)
        self.summary_json = summary_json
    def __call__(self, item, **kw):
        self.ran.append(item)
        out = self.dir / f"{item.id}.log"
        out.write_text(self.summary_json)
        return RunResult(True, 10.0, output_path=str(out))


def test_filler_stops_after_first_noop_backfill(tmp_path):
    """2026-08-20..23 regression: an empty-loom morning checkpoint burned all
    60 daily filler slots on 0.3s no-ops, starving the evening checkpoints.
    A filler that reports zero work must stop the seeding, not repeat."""
    cfg = _cfg(tmp_path, backfill_max_per_night=60, backfill_chunk=3)
    q, est, rev = _bits(tmp_path, cfg)
    noop = ('{"distilled": 0, "quarantined": 0, "failed": 0, "committed": 0, '
            '"deferred": 0}')
    r = FileRunner(tmp_path, noop)
    summary = run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0]),
                             queue=q, estimates=est, reviewed=rev, runner=r)
    assert len(r.ran) == 1                       # one probe, then stop
    assert summary["aborted"] is None
    assert q.night_count("backfill", "2026-07-03T01:00:00") == 1  # cap intact


def test_filler_keeps_seeding_while_backfill_is_productive(tmp_path):
    cfg = _cfg(tmp_path, backfill_max_per_night=2, backfill_chunk=3)
    q, est, rev = _bits(tmp_path, cfg)
    busy = '{"distilled": 5, "committed": 4, "deferred": 100}'
    r = FileRunner(tmp_path, busy)
    run_checkpoint(cfg, now=NOW, balance=FakeBalance([50.0, 40.0, 30.0, 30.0]),
                   queue=q, estimates=est, reviewed=rev, runner=r)
    assert len(r.ran) == 2                       # productive → runs to the cap
    assert all(i.type == "backfill" for i in r.ran)


# --- reserve while another job still needs DIEM tonight -------------------------------
# backlog-run fires at 22:00 UTC and its council reviews land until midnight. The floor-0
# slot would empty the balance under it, and a review on an empty balance is a failed
# review. While the runner holds its lock, the drain leaves `reserve_diem` on the table.
import fcntl

ENDGAME = datetime(2026, 7, 4, 0, 20)          # floor 0.0 in _cfg
ENDGAME_ISO = "2026-07-04T00:20:00"


def _held(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fh


def _asks(q, n, created=ENDGAME_ISO, **kw):
    for i in range(n):
        q.add(new_item("ask", {"question": f"q{i}", "panel": "decision"}, created=created, **kw))


def test_the_drain_leaves_the_reserve_while_the_runner_holds_its_lock(tmp_path):
    lock = tmp_path / "runner" / "lock"
    fh = _held(lock)
    cfg = _cfg(tmp_path, reserve_lock=lock, reserve_diem=5.0, backfill_max_per_night=0)
    q, est, rev = _bits(tmp_path, cfg)
    _asks(q, 3)
    bal = FakeBalance([8.0, 6.0, 6.0, 4.9, 4.9])
    r = FakeRunner()
    summary = run_checkpoint(cfg, now=ENDGAME, balance=bal, queue=q, estimates=est, reviewed=rev, runner=r)
    fh.close()
    assert len(r.ran) == 2                      # 8 -> 6 -> 4.9, and 4.9 is under the 5.0 reserve
    assert summary["reserve"] == 5.0 and summary["floor"] == 0.0


def test_with_the_lock_free_the_drain_goes_to_the_floor_as_before(tmp_path):
    lock = tmp_path / "runner" / "lock"
    _held(lock).close()                         # the file exists, nobody holds it
    cfg = _cfg(tmp_path, reserve_lock=lock, reserve_diem=5.0, backfill_max_per_night=0)
    q, est, rev = _bits(tmp_path, cfg)
    _asks(q, 3)
    bal = FakeBalance([8.0, 6.0, 6.0, 4.0, 4.0, 2.0, 2.0])
    r = FakeRunner()
    summary = run_checkpoint(cfg, now=ENDGAME, balance=bal, queue=q, estimates=est, reviewed=rev, runner=r)
    assert len(r.ran) == 3 and summary["reserve"] == 0.0


def test_the_reserve_ends_the_moment_the_runner_lets_go(tmp_path):
    lock = tmp_path / "runner" / "lock"
    fh = _held(lock)
    cfg = _cfg(tmp_path, reserve_lock=lock, reserve_diem=5.0, backfill_max_per_night=0)
    q, est, rev = _bits(tmp_path, cfg)
    _asks(q, 3)

    class ReleasingRunner(FakeRunner):
        def __call__(self, item, **kw):
            fh.close()                          # the runner finishes while the drain works
            return super().__call__(item, **kw)

    bal = FakeBalance([8.0, 4.9, 4.9, 3.0, 3.0, 1.0, 1.0])
    r = ReleasingRunner()
    run_checkpoint(cfg, now=ENDGAME, balance=bal, queue=q, estimates=est, reviewed=rev, runner=r)
    assert len(r.ran) == 3                      # 4.9 is under the reserve, but the reserve is gone


def test_a_missing_lock_file_means_no_reserve(tmp_path):
    cfg = _cfg(tmp_path, reserve_lock=tmp_path / "nowhere" / "lock", reserve_diem=5.0, backfill_max_per_night=0)
    q, est, rev = _bits(tmp_path, cfg)
    _asks(q, 2)
    bal = FakeBalance([8.0, 4.0, 4.0, 2.0, 2.0])
    r = FakeRunner()
    summary = run_checkpoint(cfg, now=ENDGAME, balance=bal, queue=q, estimates=est, reviewed=rev, runner=r)
    assert len(r.ran) == 2 and summary["reserve"] == 0.0


def test_the_reserve_never_lowers_a_higher_floor(tmp_path):
    lock = tmp_path / "runner" / "lock"
    fh = _held(lock)
    cfg = _cfg(tmp_path, reserve_lock=lock, reserve_diem=5.0, backfill_max_per_night=0)
    q, est, rev = _bits(tmp_path, cfg)
    _asks(q, 3, created=NOW_ISO)
    bal = FakeBalance([40.0, 25.0, 25.0, 14.0, 14.0])      # floor at 23:05 is 15.0
    r = FakeRunner()
    run_checkpoint(cfg, now=NOW, balance=bal, queue=q, estimates=est, reviewed=rev, runner=r)
    fh.close()
    assert len(r.ran) == 2                      # stopped by the 15.0 floor, not by the 5.0 reserve


# --- leftovers-only work: `not_before` ---------------------------------------------------
# Work that should only ever soak up DIEM nobody else wanted carries a time of the DIEM day.
# Before that time the drain does not see it at all, so it cannot take the morning allowance
# and it cannot stop the loom filler from seeding (filler needs an empty queue).

def test_an_item_is_invisible_before_its_not_before_time(tmp_path):
    cfg = _cfg(tmp_path, backfill_max_per_night=0)
    q, est, rev = _bits(tmp_path, cfg)
    _asks(q, 1, created="2026-07-03T21:30:00", not_before="23:00")
    r = FakeRunner()
    summary = run_checkpoint(cfg, now=datetime(2026, 7, 3, 21, 30), balance=FakeBalance([90.0]),
                             queue=q, estimates=est, reviewed=rev, runner=r)
    assert r.ran == [] and summary["skipped"] == []
    assert len(q.pending("2026-07-03T21:30:00")) == 1       # still there for tonight


def test_the_same_item_runs_once_its_time_has_come(tmp_path):
    cfg = _cfg(tmp_path, backfill_max_per_night=0)
    q, est, rev = _bits(tmp_path, cfg)
    _asks(q, 1, created="2026-07-03T21:30:00", not_before="23:00")
    r = FakeRunner()
    run_checkpoint(cfg, now=NOW, balance=FakeBalance([90.0, 89.0]), queue=q, estimates=est, reviewed=rev, runner=r)
    assert len(r.ran) == 1


def test_not_before_follows_the_diem_day_across_midnight(tmp_path):
    cfg = _cfg(tmp_path, backfill_max_per_night=0)          # reset 01:00: 00:20 is still "tonight"
    q, est, rev = _bits(tmp_path, cfg)
    _asks(q, 1, created="2026-07-03T21:30:00", not_before="23:00")
    r = FakeRunner()
    run_checkpoint(cfg, now=ENDGAME, balance=FakeBalance([9.0, 8.0]), queue=q, estimates=est, reviewed=rev, runner=r)
    assert len(r.ran) == 1


def test_a_waiting_item_does_not_stop_the_loom_filler_from_seeding(tmp_path):
    cfg = _cfg(tmp_path, backfill_max_per_night=1)
    q, est, rev = _bits(tmp_path, cfg)
    _asks(q, 1, created="2026-07-03T21:30:00", not_before="23:00")
    r = FakeRunner()
    run_checkpoint(cfg, now=datetime(2026, 7, 3, 21, 30), balance=FakeBalance([90.0, 89.0]),
                   queue=q, estimates=est, reviewed=rev, runner=r)
    assert [i.type for i in r.ran] == ["backfill"]


def test_a_garbled_not_before_is_treated_as_absent(tmp_path):
    cfg = _cfg(tmp_path, backfill_max_per_night=0)
    q, est, rev = _bits(tmp_path, cfg)
    _asks(q, 1, created="2026-07-03T21:30:00", not_before="late")
    r = FakeRunner()
    run_checkpoint(cfg, now=datetime(2026, 7, 3, 21, 30), balance=FakeBalance([90.0, 89.0]),
                   queue=q, estimates=est, reviewed=rev, runner=r)
    assert len(r.ran) == 1
