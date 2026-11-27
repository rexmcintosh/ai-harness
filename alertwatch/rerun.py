"""Run a listed job once more, as its cron line does: `python3 -m alertwatch.rerun <job>`.

The only way an alert-watch run may start a job. The job list, with each job's
exact command, lock and log, is `[rerun.*]` in alertwatch/config.toml. A job is
rerun at most once per `rerun_window_s`. It runs with its normal environment:
MEETTRACK_DB_READ_ONLY is removed, because a rerun is the job doing its usual work.
ALERT_SOURCE tags any Telegram message it sends, so the dispatcher drops the copy.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from alertwatch.config import Config, load_config


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{path.name}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def main(argv: list[str] | None = None, *, cfg: Config | None = None,
         now: Callable[[], float] = time.time) -> int:
    ap = argparse.ArgumentParser(prog="alertwatch.rerun")
    ap.add_argument("job")
    args = ap.parse_args(argv)
    cfg = cfg or load_config()
    if cfg.mode != "act":
        print(f"rerun: mode is {cfg.mode}; reruns need mode = act", file=sys.stderr)
        return 2
    return rerun(cfg, args.job, now=now)


def rerun(cfg: Config, name: str, *, now: Callable[[], float] = time.time,
          source: str = "alert-watch-rerun", started: list | None = None,
          precheck: Callable[[], bool] | None = None) -> int:
    """Rerun one listed job, at most once per rerun_window_s across every caller (the
    alert-watch run through main(), and watchdog.fix_jobs, which calls this directly
    with no AI involved). Returns the job's exit code; 1 or 2 when it did not run.
    `started`, when given, gets True appended once the job has really started, so a
    caller can tell a refusal from the job's own exit code. `precheck`, when given, is
    asked again once the job's lock is held, just before the start: False means the job
    no longer needs a rerun (a scheduled run finished meanwhile), so nothing runs and the
    reservation is given back."""
    job = cfg.rerun.get(name)
    if job is None:
        print(f"rerun: {name!r} is not on the rerun list ({', '.join(sorted(cfg.rerun))})",
              file=sys.stderr)
        return 2
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    marks_path = cfg.state_dir / "reruns.json"
    # Reserve the rerun under a lock; a rerun that never started (lock busy,
    # spawn failure) gives its reservation back, one that started keeps it.
    with open(cfg.state_dir / "reruns.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marks = _load(marks_path)
        t = now()
        last = float(marks.get(name) or 0)
        if t - last < cfg.rerun_window_s:
            print(f"rerun: {name} was already rerun {int((t - last) / 60)} min ago; "
                  f"one rerun per {cfg.rerun_window_s // 3600} h", file=sys.stderr)
            return 2
        marks[name] = t
        _save(marks_path, marks)

    def give_back() -> None:
        with open(cfg.state_dir / "reruns.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = _load(marks_path)
            if current.get(name) == t:
                if last:
                    current[name] = last
                else:
                    current.pop(name, None)
                _save(marks_path, current)

    env = {k: v for k, v in os.environ.items() if k != "MEETTRACK_DB_READ_ONLY"}
    env["ALERT_SOURCE"] = f"{source}:{name}"
    argv_job = ["/bin/sh", "-c", job.cmd]
    # Take the job's own cron lock here (the same flock(2) lock `flock -n` uses), so
    # a busy lock is known before the job starts, and the job's exit code is its own.
    # Any failure before the job starts gives the reservation back.
    held = logf = None
    try:
        if job.lock:
            held = open(job.lock, "a")
            try:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                held.close()
                held = None
                give_back()
                print(f"rerun {name}: lock busy (the job is running now); not counted")
                return 1
        if precheck is not None and not precheck():
            if held:
                held.close()
                held = None
            give_back()
            print(f"rerun {name}: no longer needed; not counted")
            return 1
        Path(job.log).parent.mkdir(parents=True, exist_ok=True)
        logf = open(job.log, "ab")
        logf.write(f"--- {source} {name} {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))}\n"
                   .encode())
        logf.flush()
        # The job inherits the locked file, so the lock stays held for the job's whole
        # life even if this helper dies first (flock locks follow the open file).
        proc = subprocess.Popen(argv_job, cwd=job.cwd, env=env, stdout=logf,
                                stderr=subprocess.STDOUT, start_new_session=True,
                                pass_fds=(held.fileno(),) if held else ())
        if started is not None:
            started.append(True)
    except OSError as e:
        for fh in (held, logf):
            if fh:
                fh.close()
        give_back()
        print(f"rerun {name}: could not start ({e.__class__.__name__}); not counted")
        return 1
    try:
        rc = proc.wait(timeout=job.timeout_s)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        print(f"rerun {name}: timed out after {job.timeout_s} s log={job.log}")
        return 124
    finally:
        logf.close()
    if held:
        held.close()
    print(f"rerun {name}: rc={rc} log={job.log}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
