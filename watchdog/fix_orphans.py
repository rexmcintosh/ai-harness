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
- Positive proof that nobody waits: its output (fd 1) is a pipe that no process holds
  open for reading. Output to a file, a terminal, /dev/null or a socket may be a
  deliberately detached run, so it is left alone (the watchdog still reports it).
- Never a process inside a systemd service (its main process or a worker it started):
  systemd still answers for it. T3 sessions run in scopes, not services.
- Only after 3 hours, so the fixer acts well before the watchdog warns at 6 hours.
- Identity is pinned before any signal: a pidfd is opened, then /proc/<pid>/stat is
  read again for an adopting parent (init, or Rex's own user manager), the same start
  time, the same executable name, and Rex's own uid. Every signal goes through that
  pidfd, so a reused pid is never hit; every rule is checked again just before each
  signal.
- TERM, up to 10 seconds, then KILL, up to 5 seconds. Each process is tried once
  (recorded before the signal, with the boot ID, and kept while the process runs). If
  it is still running, one PROBLEM names it. A stop cut off by a crash is reported on
  the next pass.
- Notes name the pid, age, executable and a known subcommand only: arguments can hold a
  prompt.
- Never while the pause file exists (planned work). Child processes that Codex itself
  started are not touched.
- Limit: a lost reader proves nobody gets the answer, not that the run has no other
  useful effect (files it writes). The 3-hour wait is the guard for that; a run meant
  to outlive its caller should write its output to a file, which keeps it safe here.

    python3 -m watchdog.fix_orphans            # check and act (cron, :10 and :40)
    python3 -m watchdog.fix_orphans --dry-run  # print what it would do
    python3 -m watchdog.fix_orphans --only-pid N --min-hours 0   # one chosen process
"""
from __future__ import annotations

import argparse
import os
import select
import signal
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
    try:
        # The program file itself, not the argv[0] a process may set as it likes. An
        # update of Codex while it runs shows as "... (deleted)".
        binary = _exe_name(os.readlink(proc / str(pid) / "exe").removesuffix(" (deleted)"))
    except OSError:
        binary = None
    # The command name in field 2 may hold spaces and ")"; split after the last one.
    rest = stat[stat.rfind(")") + 2:].split()
    try:
        return {"state": rest[0], "ppid": int(rest[1]), "start": int(rest[19]),
                "exe": _exe_name(argv0.decode(errors="replace")), "binary": binary, "uid": uid}
    except (IndexError, ValueError):
        return None


def _is_codex_name(name: str | None) -> bool:
    return bool(name) and (name == COMMAND or name.startswith(f"{COMMAND}-"))


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
    if not _is_codex_name(ident["exe"]) or not _is_codex_name(ident.get("binary")):
        return False                     # both the name it shows and its program file
    age = age_seconds(ident["start"])
    return age is not None and age >= min_minutes * 60


def boot_id(proc: Path | None = None) -> str:
    proc = proc or PROC
    try:
        return (proc / "sys" / "kernel" / "random" / "boot_id").read_text().strip()[:8]
    except OSError:
        return "noboot"


def in_service(pid: int, proc: Path | None = None) -> bool:
    """True when the process lives inside a systemd service, the user manager itself
    aside: a service's main process or a worker it started, so systemd still answers
    for it, and it is left alone. An unreadable group also counts as True. A Codex run
    left behind by a T3 session lives in a t3code-*.scope (a scope is not a service),
    so it is False and may be stopped."""
    proc = proc or PROC
    try:
        lines = (proc / str(pid) / "cgroup").read_text().splitlines()
    except OSError:
        return True
    path = next((ln.split(":", 2)[2] for ln in lines if ln.startswith("0::")), None)
    if path is None:
        return True
    return any(part.endswith(".service") and not part.startswith("user@")
               for part in path.split("/"))


def _wait_exit(pidfd: int, seconds: float) -> bool:
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)       # readable once the process has exited
    return bool(poller.poll(int(seconds * 1000)))


STOPPED, LEFT, FAILED = "stopped", "left", "failed"


def stop(pidfd: int, term_wait: float | None = None, kill_wait: float | None = None,
         same=lambda: True) -> str:
    """TERM, wait, then KILL once, all through the pidfd. `same()` (every rule) is checked
    again just before each signal. STOPPED when the process ended; LEFT when it no longer
    qualified before the first signal (nothing was sent); FAILED otherwise, including a
    process that ignored TERM and then stopped qualifying for the KILL."""
    term_wait = TERM_WAIT_S if term_wait is None else term_wait
    kill_wait = KILL_WAIT_S if kill_wait is None else kill_wait
    try:
        if not same():
            return LEFT
        signal.pidfd_send_signal(pidfd, signal.SIGTERM)
        if _wait_exit(pidfd, term_wait):
            return STOPPED
        if not same():
            return FAILED
        signal.pidfd_send_signal(pidfd, signal.SIGKILL)
    except ProcessLookupError:
        return STOPPED                           # it ended on its own
    except OSError:
        return FAILED
    return STOPPED if _wait_exit(pidfd, kill_wait) else FAILED


PIPE_MARGIN_S = 60       # a pipe is made, and its reader started, just before its writer


def _stat_fields(entry: Path) -> list | None:
    try:
        stat = (entry / "stat").read_text()
    except OSError:
        return None
    return stat[stat.rfind(")") + 2:].split()


def _ancestors() -> set:
    """This fixer and the processes that started it (cron's own root process too)."""
    out, pid = set(), os.getpid()
    while pid > 1 and str(pid) not in out:
        out.add(str(pid))
        fields = _stat_fields(PROC / str(pid))
        try:
            pid = int(fields[1])
        except (TypeError, ValueError, IndexError):
            break
    return out


def _hidden_cannot_hold(pid: str, oldest_holder: int, proc: Path, mine: set, memo: dict,
                        depth: int = 0) -> bool:
    """True when a process we cannot look into cannot hold a pipe made at
    `oldest_holder`. It must sit in another control group than the target (a holder
    that inherited the pipe along with it normally shares its group), and also be a
    kernel thread, this fixer or one of its parents, a process older
    than the pipe, or a newer one whose parent cannot hold it either (fds are inherited
    from the parent: a root cron or sshd child of an old daemon). A newer one adopted by
    init (it may have inherited the pipe, then detached) or started by a process we can
    read (`sudo` from a shell) may hold it: False. Unknown also gives False."""
    if pid in memo:
        return memo[pid]
    memo[pid] = False                            # a loop or a deep chain: unknown
    fields = _stat_fields(proc / pid)
    if fields is None or depth > 64:
        return False
    try:
        ppid, flags, started = fields[1], int(fields[6]), int(fields[19])
    except (ValueError, IndexError):
        return False
    if ppid in ("0", "2") or flags & 0x00200000:
        memo[pid] = True                         # a kernel thread
        return True
    group = _cgroup_path(proc / pid)
    target_group = memo.get("@target-group")
    if group is None or target_group is None or _related(group, target_group):
        return False                             # same group as the target, or unknown
    if pid in mine or started < oldest_holder:
        memo[pid] = True
    elif ppid != "1" and not _readable_fds(proc / ppid):
        memo[pid] = _hidden_cannot_hold(ppid, oldest_holder, proc, mine, memo, depth + 1)
    return memo[pid]


def _cgroup_path(entry: Path) -> str | None:
    try:
        lines = (entry / "cgroup").read_text().splitlines()
    except OSError:
        return None
    return next((ln.split(":", 2)[2] for ln in lines if ln.startswith("0::")), None)


def _related(a: str, b: str) -> bool:
    """The same control group, or one inside the other. A process starts in its
    parent's group and only a privileged manager moves it, so a hidden process that
    inherited the pipe along with the target is normally in the target's group."""
    if a == b:
        return True
    if "/" in (a, b):
        return False                             # the top group holds only kernel threads
    return a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")


def _readable_fds(entry: Path) -> bool:
    try:
        next(iter((entry / "fd").iterdir()), None)
        return True
    except OSError:
        return False


def output_abandoned(pid: int, proc: Path | None = None) -> bool:
    """The positive sign that nobody waits for the answer: the process writes its output
    (fd 1) into a pipe, and no process, the target included, holds that pipe open for
    reading. A file, a terminal, /dev/null or a socket is not proof (a deliberately
    detached run may be writing its result there), so those are False and the process
    is left alone.

    Another user's process cannot be looked into. A process gets the pipe from its
    parent when it starts (a pipeline such as `codex ... | sudo tee`, even if it later
    moves to a new session), so only after the pipe was made, which is about when the
    target started. See _hidden_cannot_hold for which hidden processes are cleared;
    any other one means unknown: False. A pipe handed to a process over a socket is the
    one case this cannot see. Any descriptor that cannot be read while it still exists
    also means unknown: False."""
    proc = proc or PROC
    try:
        target = os.readlink(proc / str(pid) / "fd" / "1")
    except OSError:
        return False
    if not target.startswith("pipe:["):
        return False
    fields = _stat_fields(proc / str(pid))
    try:
        oldest_holder = int(fields[19]) - PIPE_MARGIN_S * os.sysconf("SC_CLK_TCK")
    except (TypeError, ValueError, IndexError):
        return False
    mine = _ancestors() if proc == PROC else set()
    memo: dict = {"@target-group": _cgroup_path(proc / str(pid))}
    try:
        entries = [d for d in proc.iterdir() if d.name.isdigit()]
    except OSError:
        return False
    for entry in entries:
        try:
            fds = list((entry / "fd").iterdir())
        except FileNotFoundError:
            continue                             # gone
        except OSError:
            if not _hidden_cannot_hold(entry.name, oldest_holder, proc, mine, memo):
                if not entry.exists():
                    continue                     # gone meanwhile
                return False                     # unreadable, could be the reader: keep
            continue
        for fd in fds:
            if entry.name == str(pid) and fd.name == "1":
                continue                         # the write end we are asking about
            try:
                if os.readlink(fd) != target:
                    continue
            except FileNotFoundError:
                continue                         # closed meanwhile
            except OSError:
                # Listed but not readable (the user manager is like this): judge the
                # process as a hidden one.
                if _hidden_cannot_hold(entry.name, oldest_holder, proc, mine, memo):
                    break
                return False                     # still there, unreadable, could hold it
            try:
                info = (entry / "fdinfo" / fd.name).read_text()
                flags = int(next(ln for ln in info.splitlines() if ln.startswith("flags:")).split()[1], 8)
            except (OSError, StopIteration, ValueError, IndexError):
                return False                     # a holder we cannot read: keep it
            if flags & os.O_ACCMODE in (os.O_RDONLY, os.O_RDWR):
                return False                     # someone still reads the answer
    return True


# Codex subcommands that may appear in a note. Anything else in the arguments (a prompt,
# a token) never leaves the machine.
SUBCOMMANDS = {"exec", "review", "app-server", "mcp", "mcp-server", "proto", "resume", "apply"}


def _short(ps_args: str) -> str:
    """The executable name, plus its subcommand when it is a known one."""
    parts = ps_args.split()
    sub = parts[1] if len(parts) > 1 and parts[1] in SUBCOMMANDS else ""
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


def decide_pending(state: dict, boot: str, now_epoch: float) -> bool:
    """Give each stop that was cut off before its outcome an outcome now. True when
    anything changed (the caller saves before it queues, so this runs once)."""
    changed = False
    for key, info in state["pending"].items():
        if isinstance(info, dict) and "outcome" in info:
            continue
        info = info if isinstance(info, dict) else {}
        what = info.get("what", key)
        if _key_alive(key, boot):
            state["tried"][key] = state["tried"].get(key, now_epoch)
            state["reported"][key] = now_epoch
            info["outcome"] = (f"PROBLEM: a leftover Codex process is still running after I "
                               f"tried to stop it ({what}). It needs a look.")
        else:
            info["outcome"] = (f"STOPPED: a leftover Codex process that nothing was waiting for "
                               f"is gone ({what}). I was cut off before I could confirm it myself.")
        state["pending"][key] = info
        changed = True
    return changed


def reconcile(state: dict, waiting: list) -> list:
    """The notes of decided pending stops that are not already in the outbox. Runs before
    the outbox is flushed, so a note queued just before a crash is never repeated."""
    notes = [info["outcome"] for _, info in sorted(state["pending"].items())
             if info.get("outcome") and info["outcome"] not in waiting]
    state["pending"] = {}
    return notes


def eligible(pid: int, row_exe: str, min_minutes: float) -> dict | None:
    """Every rule at once: the identity, the parent, the age, not a service's main
    process, and nobody reading the output. Checked before the reservation and again
    just before each signal."""
    ident = identity(pid)
    if not still_ours(ident, row_exe, min_minutes):
        return None
    if in_service(pid) or not output_abandoned(pid):
        return None
    return ident


def _still_same(pid: int, ident: dict, row_exe: str, min_minutes: float):
    def check() -> bool:
        now = eligible(pid, row_exe, min_minutes)
        return bool(now) and now["start"] == ident["start"]
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
    boot = boot_id()
    state = _prune(fixkit.load_state(NAME), boot)
    if not args.dry_run:
        if state["pending"]:
            # Decide and save first, then queue what is not already waiting, then clear.
            # This runs before the flush, so a note queued before a crash is still in
            # the outbox and is not queued twice.
            if decide_pending(state, boot, now_epoch):
                fixkit.save_state(NAME, state)
            fixkit.queue(reconcile(state, fixkit._read_outbox("orphans-outbox")), "orphans-outbox")
            fixkit.save_state(NAME, state)
        fixkit.flush("orphans-outbox")   # a pause stops actions, not earlier reports
    if fixkit.paused(now_epoch):
        print("fix_orphans: paused for planned work")
        return 0
    messages = []
    ps = run._cmd(["ps", "-ww", "-eo", "pid,ppid,etime,args"])  # -ww: never cut the line
    rows = orphan_rows(ps, min_minutes=min_minutes, command=COMMAND) if ps.strip() else []
    if not ps.strip():
        print("fix_orphans: cannot list processes; doing nothing")
    for minutes, pid_text, ps_args in rows:
        pid = int(pid_text)
        if minutes < 0 or (args.only_pid is not None and pid != args.only_pid):
            continue                     # never act on an age we cannot read
        first = identity(pid)
        if not first or first["uid"] != os.getuid():
            continue                     # gone, or another user's: never ours to judge
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
            row_exe = _exe_name(ps_args.split()[0])
            ident = eligible(pid, row_exe, min_minutes)
            if ident is None:
                continue                 # not ours, supervised, or someone reads its output
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
            result = stop(pidfd, same=_still_same(pid, ident, row_exe, min_minutes))
            if result == STOPPED:
                note = (f"STOPPED: a leftover Codex process that nothing was waiting for "
                        f"(parent gone, nobody reading its output) ({what}).")
            elif result == LEFT:
                note = None              # it changed before any signal: left alone, no note
                del state["tried"][key]  # and it may be judged again later
            else:
                state["reported"][key] = now_epoch
                note = (f"PROBLEM: a leftover Codex process did not stop on TERM or KILL "
                        f"({what}). It needs a look.")
            # Outcome first, then the note, then clear: a crash at any step is
            # reconciled once on the next pass, never twice.
            state["pending"][key]["outcome"] = note
            fixkit.save_state(NAME, state)
            if note:
                fixkit.queue([note], "orphans-outbox")
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
