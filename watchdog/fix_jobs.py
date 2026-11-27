"""Run a job once more when its last run is too old and nothing else is wrong. No AI.

Cron does not catch up: a job whose time passes while the server is off simply does
not run that day. On 2026-11-27 the server was off at 07:00, so the daily MeetTrack
coverage check never ran, and the watchdog warned about it 13 times.

Rules:
- Only the jobs in JOBS: each has a watchdog age limit and an entry in the alert-watch
  rerun list (alertwatch/config.toml), which gives its exact command, lock and log.
- Only when the run is too old AND the log after the last good run is clean. A log with
  errors means the job is failing, not missed; that stays a report for alert-watch/Rex.
- One rerun per job per 6 hours, shared with alert-watch (the same reruns.json mark), so
  the two never both rerun a job. A busy job lock means it is running now: no rerun.
- Never while the pause file exists (planned work).
- RERAN when the watchdog check passes afterwards; PROBLEM when it does not.

    python3 -m watchdog.fix_jobs            # check and act (cron, every 15 minutes)
    python3 -m watchdog.fix_jobs --dry-run  # print what it would do
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import fixkit, run

NAME = "jobs"
# watchdog cron label -> alert-watch rerun job
JOBS = {
    "meettrack-coverage": "coverage_check",
    "meettrack-discover": "discover_meets",
}


def _log_path(label: str) -> Path | None:
    return dict(run.CRON_LOGS).get(label)


def _read(path: Path | None):
    try:
        return path.read_text(errors="replace") if path else None
    except OSError:
        return None


def missed(label: str, text: str | None, now_epoch: float):
    """The watchdog's age status when the run is too old but its log is clean, else None."""
    if text is None:
        return None
    if run.cron_log_status(label, text).level != "ok":
        return None                   # failing, or a run did not finish: not a missed run
    aged = run.cron_status(label, text, now_epoch)
    return aged if aged.level != "ok" else None


def _what(status) -> str:
    """'no meettrack-coverage run has finished for 38.4h' from the watchdog summary."""
    return status.summary.split(" (limit", 1)[0]


def main(argv=None, *, rerun=None, cfg=None) -> int:
    parser = argparse.ArgumentParser(description="Rerun a job whose last run is too old.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    now_epoch = time.time()
    lock = fixkit.take_lock(NAME)
    if lock is None:
        print("fix_jobs: another run is still going")
        return 0
    if fixkit.paused(now_epoch):
        print("fix_jobs: paused for planned work")
        return 0
    if rerun is None or cfg is None:
        from alertwatch import rerun as rerun_mod
        from alertwatch.config import load_config
        rerun = rerun or rerun_mod.rerun
        cfg = cfg or load_config()
    messages = []
    state = fixkit.load_state(NAME)
    pending = dict(state.get("pending") or {})
    if not args.dry_run and pending:
        # A rerun this fixer started, whose outcome it never recorded (it was stopped).
        for job, info in pending.items():
            at = time.strftime("%H:%M", time.localtime(float(info.get("at", 0))))
            messages.append(f"PROBLEM: I started a rerun of {job} at {at}, but I was stopped "
                            f"before it finished. Check its log: {info.get('log')}")
        fixkit.queue(messages, "jobs-outbox")
        messages = []
        pending = {}
        fixkit.save_state(NAME, {**state, "pending": pending})
    for label, job in JOBS.items():
        if job not in cfg.rerun:
            continue
        path = _log_path(label)
        status = missed(label, _read(path), now_epoch)
        if status is None:
            continue
        if args.dry_run:
            messages.append(f"(dry run) would rerun {job}: {_what(status)}")
            continue
        started: list = []
        pending[job] = {"at": time.time(), "log": str(path)}
        fixkit.save_state(NAME, {**state, "pending": pending})      # before the rerun
        # Asked again under the job's lock: a scheduled run may have finished meanwhile.
        still = lambda: missed(label, _read(path), time.time()) is not None
        rc = rerun(cfg, job, source="fix-jobs-rerun", started=started, precheck=still)
        if not started:
            pending.pop(job, None)    # rerun already used in the last 6 h, or running now
            fixkit.save_state(NAME, {**state, "pending": pending})
            continue
        text = _read(path) or ""
        clean = run.cron_log_status(label, text)
        after = run.cron_status(label, text, time.time())
        if clean.level != "ok":
            after = clean
        if rc == 0 and clean.level == "ok" and after.level == "ok":
            messages.append(f"RERAN: {_what(status)}, probably missed while the server was off. "
                            "I ran it once and it finished fine.")
        else:
            messages.append(f"PROBLEM: {_what(status)}. I ran it once (exit {rc}), and its check "
                            f"still fails: {after.summary}. Log: {path}")
        fixkit.queue(messages, "jobs-outbox")       # queue, then clear the pending mark
        messages = []
        pending.pop(job, None)
        fixkit.save_state(NAME, {**state, "pending": pending})
    if args.dry_run:
        print("\n".join(messages) or "nothing to do")
        return 0
    fixkit.flush("jobs-outbox")
    return 0


if __name__ == "__main__":
    sys.exit(main())
