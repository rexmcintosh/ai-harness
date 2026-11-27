"""Stop leftover Codex processes that nothing is waiting for. No AI.

The Codex CLI on PATH is a Node wrapper around a native binary. When the session that
started it dies, or a helper's timeout kills the wrapper, the native binary keeps
running, adopted by init (PPID 1) or by the user's `systemd --user` manager (where T3
and its sessions run), and nobody reads its answer. The watchdog's `proc:orphans` check
reported one such process (pid 424321) five times on 25 and 26 Nov 2026 before a
restart ended it.

Rules:
- The same matcher as the watchdog check (watchdog.triage.orphan_rows): an executable
  named `codex` or `codex-*` whose parent is init or the user manager. Nothing else is
  ever signalled.
- Never the main process of a systemd service: that one is supervised, not left
  behind. When the service answer cannot be read, nothing is signalled.
- Only after 3 hours, so the fixer acts well before the watchdog warns at 6 hours.
- Identity is pinned before any signal: a pidfd is opened, then /proc/<pid>/stat is
  read again for an adopting parent (init, or Rex's own user manager), the same start
  time, the same executable name, and Rex's own uid. Every signal goes through that
  pidfd, so a reused pid is never hit; start time and name are checked again just
  before each signal.
- TERM, up to 10 seconds, then KILL, up to 5 seconds. Each process is tried once
  (recorded before the signal, with the boot ID, and kept while the process runs). If
  it is still running, one PROBLEM names it. A stop cut off by a crash is reported on
  the next pass.
- Notes name the pid, age, executable and subcommand only: arguments can hold a prompt.
- Never while the pause file exists (planned work). Child processes that Codex itself
  started are not touched.

    python3 -m watchdog.fix_orphans            # check and act (cron, :10 and :40)
    python3 -m watchdog.fix_orphans --dry-run  # print what it would do
    python3 -m watchdog.fix_orphans --only-pid N --min-hours 0   # one chosen process
"""
from __future__ import annotations

import argparse
import os
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import fixkit, run
from .triage import is_user_manager, orphan_rows

NAME = "orphans"
COMMAND = "codex"
MIN_MINUTES = 3 * 60
TERM_WAIT_S = 10.0
KILL_WAIT_S = 5.0
PIDFD_PROBLEM_EVERY_S = 86400
PROC = Path("/proc")


def _exe_name(argv0: str) -> str:
    return argv0.rsplit("/", 1)[-1]


def identity(pid: int, proc: Path | None = None) -> dict | None:
    """PPID, start time (clock ticks since boot), state, executable name and owner, read
    from /proc. None when the process is gone or /proc cannot be read."""
    proc = proc or PROC
    try:
        stat = (proc / str(pid) / "stat").read_text()
        argv0 = (proc / str(pid) / "cmdline").read_bytes().split(b"\0", 1)[0]
        uid = (proc / str(pid)).stat().st_uid
    except OSError:
        return None
    # The command name in field 2 may hold spaces and ")"; split after the last one.
    rest = stat[stat.rfind(")") + 2:].split()
    try:
        return {"state": rest[0], "ppid": int(rest[1]), "start": int(rest[19]),
                "exe": _exe_name(argv0.decode(errors="replace")), "uid": uid}
    except (IndexError, ValueError):
        return None


def is_reaper(ppid: int, proc: Path | None = None) -> bool:
    """Init, or Rex's own `systemd --user`: the parents an orphan is adopted by."""
    if ppid == 1:
        return True
    proc = proc or PROC
    try:
        argv = (proc / str(ppid) / "cmdline").read_bytes().split(b"\0")
        uid = (proc / str(ppid)).stat().st_uid
    except OSError:
        return False
    return uid == os.getuid() and is_user_manager(" ".join(a.decode(errors="replace") for a in argv))


def age_seconds(start_ticks: int, proc: Path | None = None) -> float | None:
    proc = proc or PROC
    try:
        uptime = float((proc / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return uptime - start_ticks / os.sysconf("SC_CLK_TCK")


def still_ours(ident: dict | None, row_exe: str, min_minutes: float) -> bool:
    """True only for a live process that still matches what the matcher saw."""
    if not ident or ident["state"] in ("Z", "X"):
        return False
    if not is_reaper(ident["ppid"]) or ident["uid"] != os.getuid() or ident["exe"] != row_exe:
        return False
    if ident["exe"] != COMMAND and not ident["exe"].startswith(f"{COMMAND}-"):
        return False
    age = age_seconds(ident["start"])
    return age is not None and age >= min_minutes * 60


def boot_id(proc: Path | None = None) -> str:
    proc = proc or PROC
    try:
        return (proc / "sys" / "kernel" / "random" / "boot_id").read_text().strip()[:8]
    except OSError:
        return "noboot"


def service_main_pid(pid: int, proc: Path | None = None, runner=None) -> bool:
    """True when systemd runs this process as the main process of a service, so it is
    supervised, not left behind. An unreadable answer also counts as True: when in
    doubt, nothing is signalled. A process in a scope, or one left in a service it is
    not the main process of (a T3 session's leftover in t3code.service), is False."""
    proc = proc or PROC
    runner = runner or subprocess.run
    try:
        lines = (proc / str(pid) / "cgroup").read_text().splitlines()
    except OSError:
        return True
    path = next((ln.split(":", 2)[2] for ln in lines if ln.startswith("0::")), None)
    if path is None:
        return True
    unit = path.rstrip("/").rsplit("/", 1)[-1]
    if not unit.endswith(".service"):
        return False
    cmd = ["systemctl"] + (["--user"] if "/user@" in path else []) + [
        "show", "-p", "MainPID", "--value", unit]
    try:
        out = runner(cmd, capture_output=True, text=True, timeout=20, env=fixkit.child_env())
    except (OSError, subprocess.SubprocessError):
        return True
    answer = (out.stdout or "").strip()
    if out.returncode != 0 or not answer.isdigit():
        return True
    return int(answer) == pid


def _wait_exit(pidfd: int, seconds: float) -> bool:
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)       # readable once the process has exited
    return bool(poller.poll(int(seconds * 1000)))


def stop(pidfd: int, term_wait: float = TERM_WAIT_S, kill_wait: float = KILL_WAIT_S,
         same=lambda: True) -> bool:
    """TERM, wait, then KILL once, all through the pidfd. `same()` is checked again just
    before each signal, so a process that started another program in the meantime is
    left alone. True when the process ended."""
    try:
        if not same():
            return False
        signal.pidfd_send_signal(pidfd, signal.SIGTERM)
        if _wait_exit(pidfd, term_wait):
            return True
        if not same():
            return False
        signal.pidfd_send_signal(pidfd, signal.SIGKILL)
    except ProcessLookupError:
        return True                              # it ended on its own
    except OSError:
        return False
    return _wait_exit(pidfd, kill_wait)


_WORD = re.compile(r"[a-z][a-z-]{0,20}")


def _short(ps_args: str) -> str:
    """The executable name and its subcommand word only. Arguments can hold a prompt or
    a token, so they never go into a note."""
    parts = ps_args.split()
    sub = parts[1] if len(parts) > 1 and _WORD.fullmatch(parts[1]) else ""
    return f"{_exe_name(parts[0])} {sub}".strip()


def _age(minutes: int) -> str:
    return f"{minutes // 1440}d" if minutes >= 1440 else f"{minutes // 60}h"


def _key_alive(key: str, boot: str) -> bool:
    """True while the process this key names (boot:pid:start) still runs."""
    try:
        kboot, pid, start = key.split(":")
        ident = identity(int(pid)) if kboot == boot else None
    except ValueError:
        return False
    return bool(ident) and str(ident["start"]) == start and ident["state"] not in ("Z", "X")


def _prune(state: dict, boot: str) -> dict:
    """Keep records only for processes that still run. A record lasts as long as its
    process, so a survivor is never tried or reported twice."""
    out = {"pending": dict(state.get("pending") or {})}
    for name in ("tried", "reported"):
        out[name] = {k: v for k, v in (state.get(name) or {}).items() if _key_alive(k, boot)}
    out["pidfd_error_at"] = float(state.get("pidfd_error_at") or 0)
    return out


def reconcile(state: dict, boot: str, now_epoch: float) -> list:
    """Stops that were cut off before their outcome was written: report each once."""
    notes = []
    for key, info in sorted(state["pending"].items()):
        what = info.get("what", key) if isinstance(info, dict) else key
        if _key_alive(key, boot):
            state["tried"][key] = state["tried"].get(key, now_epoch)
            state["reported"][key] = now_epoch
            notes.append(f"PROBLEM: a leftover Codex process is still running after I tried to "
                         f"stop it ({what}). It needs a look.")
        else:
            notes.append(f"STOPPED: a leftover Codex process that nothing was waiting for is gone "
                         f"({what}). I was cut off before I could confirm it myself.")
    state["pending"] = {}
    return notes


def _still_same(pid: int, ident: dict):
    def check() -> bool:
        now = identity(pid)
        return bool(now) and now["start"] == ident["start"] and now["exe"] == ident["exe"]
    return check


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Stop leftover Codex processes nothing waits for.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only-pid", type=int, help="act on this one process only")
    parser.add_argument("--min-hours", type=float, help="age limit (only with --only-pid)")
    args = parser.parse_args(argv)
    if args.min_hours is not None and args.only_pid is None:
        parser.error("--min-hours needs --only-pid: a lower age limit is never global")
    min_minutes = MIN_MINUTES if args.min_hours is None else args.min_hours * 60
    now_epoch = time.time()
    lock = fixkit.take_lock(NAME)
    if lock is None:
        print("fix_orphans: another run is still going")
        return 0
    if not args.dry_run:
        fixkit.flush("orphans-outbox")   # a pause stops actions, not earlier reports
    if fixkit.paused(now_epoch):
        print("fix_orphans: paused for planned work")
        return 0
    boot = boot_id()
    state = _prune(fixkit.load_state(NAME), boot)
    messages = []
    if not args.dry_run and state["pending"]:
        messages += reconcile(state, boot, now_epoch)
    ps = run._cmd(["ps", "-ww", "-eo", "pid,ppid,etime,args"])  # -ww: never cut the line
    rows = orphan_rows(ps, min_minutes=min_minutes, command=COMMAND) if ps.strip() else []
    if not ps.strip():
        print("fix_orphans: cannot list processes; doing nothing")
    for minutes, pid_text, ps_args in rows:
        pid = int(pid_text)
        if minutes < 0 or (args.only_pid is not None and pid != args.only_pid):
            continue                     # never act on an age we cannot read
        try:
            pidfd = os.pidfd_open(pid)
        except ProcessLookupError:
            continue                     # gone already
        except OSError as err:
            if now_epoch - state["pidfd_error_at"] >= PIDFD_PROBLEM_EVERY_S:
                state["pidfd_error_at"] = now_epoch
                messages.append(f"PROBLEM: I cannot pin leftover Codex processes to stop them "
                                f"(pidfd_open: {err.strerror}). The watchdog keeps reporting them.")
            continue
        try:
            ident = identity(pid)
            if not still_ours(ident, _exe_name(ps_args.split()[0]), min_minutes):
                continue
            if service_main_pid(pid):
                continue                 # supervised by systemd, or we cannot tell
            key = f"{boot}:{pid}:{ident['start']}"
            what = f"pid {pid}, {_age(minutes)}, {_short(ps_args)}"
            if key in state["tried"]:
                if key not in state["reported"]:
                    state["reported"][key] = now_epoch
                    messages.append(f"PROBLEM: a leftover Codex process is still running after I "
                                    f"tried to stop it ({what}). It needs a look: kill -9 {pid}")
                continue
            if args.dry_run:
                print(f"(dry run) would stop {what}")
                continue
            # Queue what is known so far, then reserve; a crash after the signal is
            # reported by reconcile() on the next pass.
            fixkit.queue(messages, "orphans-outbox")
            messages = []
            state["tried"][key] = now_epoch
            state["pending"][key] = {"at": now_epoch, "what": what}
            fixkit.save_state(NAME, state)
            if stop(pidfd, same=_still_same(pid, ident)):
                note = (f"STOPPED: a leftover Codex process that nothing was waiting for "
                        f"(parent gone) ({what}).")
            else:
                state["reported"][key] = now_epoch
                note = (f"PROBLEM: a leftover Codex process did not stop on TERM or KILL "
                        f"({what}). It needs a look.")
            fixkit.queue([note], "orphans-outbox")     # queue, then clear the reservation
            del state["pending"][key]
            fixkit.save_state(NAME, state)
        finally:
            os.close(pidfd)
    if args.dry_run:
        print("\n".join(messages) or "nothing more to do")
        return 0
    fixkit.queue(messages, "orphans-outbox")    # queue, save, flush
    fixkit.save_state(NAME, state)
    fixkit.flush("orphans-outbox")
    return 0


if __name__ == "__main__":
    sys.exit(main())
