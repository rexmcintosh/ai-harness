"""The only way an alert-watch run restarts a meet writer: trusted code, not the model.

    python3 -m alertwatch.restart restart <sr_meet_id>   stop the writer; the supervisor relaunches it
    python3 -m alertwatch.restart history                restarts in the last 24 hours (JSON lines)

Checked here: act mode; the meet's PID file names a live process with the same
start time (so a reused PID is never killed) that runs a meet writer script; the
meet has one registry row with `poller_attempts` at or under
`restart_max_attempts` (the supervisor marks a meet failed at 5, and only a
database write undoes that). Then SIGTERM, and a line in the actions ledger.
The run's deny rules block kill, pkill and killall, so this is the path.
Exit 0 done, 2 refused.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Callable

from alertwatch import ledger
from alertwatch.config import Config, load_config

WRITER_SCRIPTS = ("poller.py", "track_pdf_meet.py", "lastheat_ingest.py", "reconcile_meet.py")
_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class Refused(Exception):
    pass


def _default_select(cfg: Config):
    sys.path.insert(0, str(cfg.projects_dir / "splash_poller"))
    import db  # splash_poller's client; reads are allowed under MEETTRACK_DB_READ_ONLY
    return db.select


def _starttime(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _argv(pid: int) -> list[str]:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
    except OSError:
        return []


def _is_writer(argv: list[str]) -> bool:
    """Exactly how the supervisor starts a writer: <python> <writer script> ..."""
    return (len(argv) >= 2 and Path(argv[0]).name.startswith("python")
            and Path(argv[1]).name in WRITER_SCRIPTS)


def do_restart(cfg: Config, key: str, select, send, now: float) -> dict:
    if cfg.mode != "act":
        raise Refused(f"mode is {cfg.mode}; restarts need mode = act")
    if not _KEY_RE.fullmatch(key) or ".." in key:
        raise Refused(f"bad meet key {key!r}")
    pid_file = cfg.meettrack_run_dir / f"poller-{key}.pid"
    try:
        parts = pid_file.read_text().split()
        pid, recorded = int(parts[0]), (parts[1] if len(parts) > 1 else None)
    except (OSError, ValueError, IndexError):
        raise Refused(f"no readable PID file {pid_file}") from None
    if recorded is None:
        raise Refused(f"the PID file for {key} has no start time; cannot prove it is the writer")
    # Pin the process first: the pidfd always means this one process, so the checks
    # below and the signal cannot land on a reused PID.
    try:
        pidfd = os.pidfd_open(pid)
    except ProcessLookupError:
        raise Refused(f"writer {pid} for {key} is not running (the supervisor relaunches it)") from None
    try:
        if _starttime(pid) != recorded:
            raise Refused(f"PID {pid} start time does not match the PID file; it may be a reused PID")
        argv = _argv(pid)
        if not _is_writer(argv):
            raise Refused(f"PID {pid} is not a meet writer ({' '.join(argv)[:120]})")
        rows = select("meet_registry", f"sr_meet_id=eq.{key}", "sr_meet_id,poller_attempts,status")
        if len(rows) != 1:
            raise Refused(f"{len(rows)} registry rows for {key}")
        attempts = int(rows[0].get("poller_attempts") or 0)
        if attempts > cfg.restart_max_attempts:
            raise Refused(f"{key} has poller_attempts={attempts} (restart only at "
                          f"{cfg.restart_max_attempts} or less; the supervisor gives up at 5)")
        try:
            send(pidfd, signal.SIGTERM)
        except ProcessLookupError:
            raise Refused(f"writer {pid} for {key} stopped by itself") from None
    finally:
        os.close(pidfd)
    action = {"kind": "restart", "at": now, "run_id": os.environ.get("ALERT_WATCH_RUN_ID", ""),
              "key": key, "pid": pid, "attempts": attempts, "registry_status": rows[0].get("status")}
    ledger.append_action(cfg.state_dir, action)
    return {"status": "restarted", **action}


def main(argv: list[str] | None = None, *, cfg: Config | None = None, select=None,
         send: Callable = signal.pidfd_send_signal, now: Callable[[], float] = time.time) -> int:
    ap = argparse.ArgumentParser(prog="alertwatch.restart")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("restart").add_argument("key")
    sub.add_parser("history")
    args = ap.parse_args(argv)
    cfg = cfg or load_config()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    if args.cmd == "history":
        cutoff = now() - 86400
        for a in ledger.actions(cfg.state_dir):
            if a.get("kind") == "restart" and float(a.get("at") or 0) >= cutoff:
                print(json.dumps(a))
        return 0
    try:
        out = do_restart(cfg, args.key, select or _default_select(cfg), send, now())
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
