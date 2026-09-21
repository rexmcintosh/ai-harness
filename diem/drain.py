# diem/drain.py
"""The drain loop. All decisions are clock/budget arithmetic — no judgment.
Interactive evening use is honored implicitly: balance re-read between jobs
means a human burning DIEM pushes the balance to the floor and we stop."""
from __future__ import annotations
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .balance import BalanceUnavailable
from .discover import discover
from .queue import new_item
from .state import Lock, pause_until


def _backfill_did_nothing(res) -> bool:
    """True when a backfill run's saved loom summary reports zero work
    (committed==0 and distilled==0). Unreadable or absent output — including
    a failed run — is NOT treated as a no-op, so detection failures fall back
    to the old always-seed behavior rather than silently stopping the drain."""
    if not res.ok or not res.output_path:
        return False
    try:
        summary = json.loads(Path(res.output_path).read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(summary, dict):
        return False
    return summary.get("committed") == 0 and summary.get("distilled") == 0


def _at(now: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def next_deadline(cfg, now: datetime) -> datetime:
    """Deadline of the CURRENT DIEM day — the hard cutoff shortly before reset.
    Anchored via next_reset so a late-firing checkpoint never sees a ~24h-out
    deadline. When the deadline's clock time is at/after the reset's (e.g. 23:50
    before a 00:00 UTC reset), it belongs to the evening *before* the reset, so
    anchor it to the day preceding next_reset; otherwise (00:50 before 01:00) it
    shares the reset's date."""
    reset_dt = next_reset(cfg, now)
    dh, dm = map(int, cfg.deadline.split(":"))
    rh, rm = map(int, cfg.reset.split(":"))
    base = reset_dt if (dh, dm) < (rh, rm) else reset_dt - timedelta(days=1)
    return _at(base, cfg.deadline)


def next_reset(cfg, now: datetime) -> datetime:
    r = _at(now, cfg.reset)
    return r if now <= r else r + timedelta(days=1)


def _last_fired(cfg, now: datetime):
    """(fired_at, Checkpoint) of the latest checkpoint at-or-before now within
    the current DIEM day, or None if none has fired yet."""
    day_start = next_reset(cfg, now) - timedelta(days=1)
    best = None
    for cp in cfg.checkpoints:
        t = _at(day_start, cp.time)
        if t < day_start:
            t += timedelta(days=1)
        if t <= now and (best is None or t > best[0]):
            best = (t, cp)
    return best


def floor_for(cfg, now: datetime) -> float:
    """Latest checkpoint at-or-before now on the DIEM day (reset..reset).
    Checkpoint times are anchored to the DAY START, not now's date — at
    00:05 the operative checkpoint is *yesterday's* 23:00, and a 00:15
    checkpoint belongs to the day that started the previous 01:00.
    Before the first checkpoint fires, use the first (most conservative)."""
    best = _last_fired(cfg, now)
    frac = best[1].floor if best else cfg.checkpoints[0].floor
    return frac * cfg.daily_diem


def _flock_held(path) -> bool:
    """True when some process holds a flock on `path`. Read from /proc/locks instead of
    trying the lock: a probe that takes the lock, even for a microsecond, could make the
    runner's own non-blocking attempt fail and cost it the night. Anything unreadable or
    unexpected answers False."""
    try:
        st = os.stat(path)
        want = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
        with open("/proc/locks", encoding="ascii", errors="replace") as fh:
            return any("FLOCK" in line and want in line.split() for line in fh)
    except (OSError, ValueError):
        return False


def _reserve(cfg) -> float:
    """DIEM to leave on the table right now: cfg.reserve_diem while another job holds
    cfg.reserve_lock (a flock), else 0. Asked again before every job, so the reserve ends
    the moment that job lets go. A lock file that is missing means nobody is running: no
    reserve, the drain behaves as it always did."""
    path, amount = getattr(cfg, "reserve_lock", None), getattr(cfg, "reserve_diem", 0.0)
    if not path or amount <= 0:
        return 0.0
    return float(amount) if _flock_held(path) else 0.0


def _due(cfg, item, now: datetime) -> bool:
    """False while an item's `not_before` (HH:MM of the DIEM day) is still ahead. Anchored to
    the day start like the checkpoints, so 23:00 stays due at 00:20 under a 01:00 reset.
    A value that does not parse counts as absent."""
    nb = getattr(item, "not_before", None)
    if not nb:
        return True
    try:
        day_start = next_reset(cfg, now) - timedelta(days=1)
        t = _at(day_start, nb)
    except (ValueError, TypeError):
        return True
    if t < day_start:
        t += timedelta(days=1)
    return now >= t


def run_checkpoint(cfg, *, now: datetime, balance, queue, estimates, reviewed,
                   runner, run=subprocess.run) -> dict:
    now_iso = now.isoformat(timespec="seconds")
    floor = floor_for(cfg, now)
    deadline = next_deadline(cfg, now)
    summary = {"aborted": None, "floor": floor, "reserve": 0.0, "started_balance": None,
               "ended_balance": None, "ran": [], "skipped": [],
               "deadline": deadline.isoformat(timespec="seconds")}

    pu = pause_until(cfg.state_dir)
    if pu and pu > now_iso:
        summary["aborted"] = "paused"
        return summary

    if now > deadline:
        summary["aborted"] = "past_deadline"
        return summary
    if _last_fired(cfg, now) is None:
        summary["aborted"] = "no_checkpoint_fired"  # off-schedule run (post-reset or mid-day)
        return summary

    lock = Lock(cfg.state_dir / "drain.lock")
    if not lock.acquire():
        summary["aborted"] = "locked"
        return summary
    try:
        day_start_iso = (next_reset(cfg, now) - timedelta(days=1)) \
            .isoformat(timespec="seconds")
        discover(cfg, queue, reviewed, now_iso, day_start_iso=day_start_iso,
                 run=run)
        elapsed = 0.0    # simulated wall-clock from job durations (tests inject now)
        attempted = set()  # ids run this checkpoint — failures retry NEXT checkpoint
        skipped_ids = set()  # dedupe: an unfittable item is re-seen every pass
        filler_ids = set()   # backfills THIS checkpoint seeded (vs banked items)
        filler_dry = False   # a seeded backfill reported zero work — stop seeding.
        # Without this, an empty-loom morning checkpoint burned all
        # backfill_max_per_night slots on ~0.3s no-ops in seconds, so the
        # evening checkpoints could not seed at all (2026-08-20..23 outage).
        while True:
            try:
                bal = balance.diem_balance()
            except BalanceUnavailable:
                summary["aborted"] = "balance_unavailable"
                return summary
            if summary["started_balance"] is None:
                summary["started_balance"] = bal
            summary["ended_balance"] = bal
            reserve = _reserve(cfg)
            summary["reserve"] = max(summary["reserve"], reserve)
            keep = max(floor, reserve)          # what this pass must leave unspent
            if bal <= keep:
                return summary

            eff_now = now + timedelta(seconds=elapsed)
            pend = [it for it in queue.pending(now_iso) if _due(cfg, it, eff_now)]
            picked, skipped_this_pass = None, []
            for it in pend:
                if it.id in attempted:
                    continue
                cost, dur = estimates.estimate(it.type)
                if bal - cost < keep:
                    reason = "budget"
                elif eff_now + timedelta(seconds=dur) > deadline:
                    reason = "deadline"
                else:
                    picked = it
                    break
                if it.id not in skipped_ids:
                    skipped_ids.add(it.id)
                    skipped_this_pass.append({"id": it.id, "type": it.type,
                                              "reason": reason})
            summary["skipped"].extend(skipped_this_pass)

            if picked is None:
                # Filler ONLY on a truly empty queue — items that merely don't
                # fit (budget/deadline) must not spawn backfill noise, and a
                # dry loom (a seeded backfill that did zero work) must not
                # burn the nightly cap on repeat probes.
                if (not pend and not filler_dry
                        and queue.night_count("backfill", day_start_iso)
                        < cfg.backfill_max_per_night):
                    it = new_item("backfill",
                                  {"max_targets": cfg.backfill_chunk},
                                  created=now_iso)
                    queue.add(it)
                    filler_ids.add(it.id)
                    continue  # picked up through the normal budget/deadline gate
                return summary

            attempted.add(picked.id)
            deadline_epoch = (deadline - eff_now).total_seconds()
            res = runner(picked, deadline_epoch=deadline_epoch)
            elapsed += res.duration_s
            try:
                after = balance.diem_balance()
            except BalanceUnavailable:
                after = bal
            cost = max(0.0, bal - after)
            estimates.record(picked.type, cost=cost, duration_s=res.duration_s)
            entry = {"id": picked.id, "type": picked.type, "ok": res.ok,
                     "cost": cost, "duration_s": res.duration_s,
                     "output_path": res.output_path, "error": res.error}
            if getattr(res, "note", None):  # e.g. a healed review baseline
                entry["note"] = res.note
            if picked.id in filler_ids and _backfill_did_nothing(res):
                filler_dry = True
                entry["noop"] = True
            summary["ran"].append(entry)
            if res.ok:
                meta = {"ok": True, "cost": cost,
                        "output_path": res.output_path}
                if getattr(res, "note", None):
                    meta["note"] = res.note  # durable: heals stay queryable
                queue.archive(picked, meta)
                if picked.type == "review" and picked.payload.get("head"):
                    reviewed.set(picked.payload["repo"], picked.payload["head"])
            else:
                picked.attempts += 1
                if picked.attempts < picked.max_attempts:
                    queue.requeue(picked)
                else:
                    queue.archive(picked, {"ok": False, "error": res.error})
    finally:
        lock.release()
