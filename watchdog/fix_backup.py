"""Start an off-site backup run when the last verified one is too old. No AI.

The daily timer has Persistent=true, so a run missed while the server was off already
starts at boot. This covers the other case: a run that failed or was killed (on
2026-11-27 a restart killed the catch-up run, and nothing started another).

Rules:
- The trigger is the watchdog's own backup check (watchdog.triage.check_backup), so the
  two never disagree: no verified backup for 30 hours.
- Never while a backup runs, and never while the pause file exists (planned work).
- At most one start per 6 hours. It says STARTED, then FIXED when the run passes its
  check, or PROBLEM once if it does not. After a PROBLEM it starts nothing more until a
  backup passes again (the timer's own run, or one Rex starts).

    python3 -m watchdog.fix_backup            # check and act (cron, every 15 minutes)
    python3 -m watchdog.fix_backup --dry-run  # print what it would do
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import fixkit
from .triage import check_backup

NAME = "backup"
UNIT = "restic-offsite.service"
COOLDOWN_S = 6 * 3600
SETTLE_S = 10 * 60        # a start that never shows as running counts as ended after this
IDLE = ("inactive", "failed")     # any other answer ("unknown", an error) means: do nothing
STATE_DIR = Path(os.environ.get("RESTIC_OFFSITE_STATE")
                 or Path.home() / ".local" / "state" / "restic-offsite")


def _clock(epoch: float) -> str:
    return time.strftime("%H:%M", time.localtime(epoch))


def decide(check, unit_state: str, run_status: dict, state: dict, now_epoch: float):
    """Pure decision. Returns (new state, start a run?, messages)."""
    new = dict(state)
    started = new.get("started_at")
    if check.level == "ok":
        new.pop("started_at", None)
        new.pop("reported", None)
        return new, False, ["FIXED: the off-site backup passed its check again."] if started else []
    if unit_state not in IDLE:
        return new, False, []         # running, or a state we cannot trust
    if started and not new.get("reported") and now_epoch - started >= SETTLE_S:
        stage = run_status.get("failed_stage") or run_status.get("stage") or "unknown"
        new["reported"] = now_epoch
        return new, False, [f"PROBLEM: the off-site backup I started at {_clock(started)} did not "
                            f"pass (stage: {stage}). It needs a look: "
                            "systemctl --user status restic-offsite.service"]
    if new.get("reported"):
        return new, False, []     # one failed try is enough: wait for a good backup or Rex
    if now_epoch - float(new.get("last_start", 0)) < COOLDOWN_S:
        return new, False, []
    return new, True, []


def record_start(new: dict, check, now_epoch: float, ok: bool) -> str:
    new["last_start"] = now_epoch           # also after a failed start: no retry every pass
    if ok:
        new["started_at"] = now_epoch
        new.pop("reported", None)
        return (f"STARTED: {check.summary}, so I started a backup run. I will say FIXED "
                "when it passes its check, or PROBLEM if it does not.")
    new["reported"] = now_epoch
    return f"PROBLEM: {check.summary}, and starting a backup run failed. It needs a look."


def _read(path: Path):
    try:
        return path.read_text()
    except OSError:
        return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Start a backup when the last verified one is too old.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    now_epoch = time.time()
    if not STATE_DIR.is_dir():
        print("fix_backup: the off-site backup is not installed here")
        return 0
    lock = fixkit.take_lock(NAME)
    if lock is None:
        print("fix_backup: another run is still going")
        return 0
    fixkit.flush("backup-outbox")        # earlier notes go out even if this pass stops early
    if fixkit.paused(now_epoch):
        print("fix_backup: paused for planned work")
        return 0
    check = check_backup(_read(STATE_DIR / "last-success.json"), now_epoch)
    try:
        unit_state = subprocess.run(["systemctl", "--user", "is-active", UNIT], capture_output=True,
                                    text=True, timeout=20, env=fixkit.child_env()).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        unit_state = ""
    if not unit_state:
        print("fix_backup: cannot read the backup service state; doing nothing")
        return 0
    try:
        run_status = json.loads(_read(STATE_DIR / "status.json") or "{}")
    except ValueError:
        run_status = {}
    if not isinstance(run_status, dict):
        run_status = {}
    new, start, messages = decide(check, unit_state, run_status, fixkit.load_state(NAME), now_epoch)
    if start:
        if args.dry_run:
            messages.append(f"(dry run) would start {UNIT}: {check.summary}")
        else:
            # Reserve first: a crash after the start can then never allow a second one.
            reserved = dict(new, last_start=now_epoch, started_at=now_epoch)
            fixkit.save_state(NAME, reserved)
            try:
                ok = subprocess.run(["systemctl", "--user", "start", "--no-block", UNIT],
                                    capture_output=True, timeout=60,
                                    env=fixkit.child_env()).returncode == 0
            except subprocess.TimeoutExpired:
                ok = True                  # systemd may have taken it; the outcome check decides
            except (OSError, subprocess.SubprocessError):
                ok = False
            messages.append(record_start(new, check, now_epoch, ok))
    if args.dry_run:
        print(f"backup check: {check.level}: {check.summary}; service: {unit_state}")
        print("\n".join(messages) or "nothing to do")
        return 0
    fixkit.queue(messages, "backup-outbox")    # queue, save, flush: a note is never lost
    fixkit.save_state(NAME, new)
    fixkit.flush("backup-outbox")
    return 0


if __name__ == "__main__":
    sys.exit(main())
