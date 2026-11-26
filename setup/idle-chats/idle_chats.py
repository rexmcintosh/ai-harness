#!/usr/bin/python3 -I
"""Stop Claude desktop chat programs that have been idle for a day.

The Claude desktop app keeps one CLI program running on this server for every
chat in its sidebar, and it never stops them. Each one holds a few hundred MB.
A stopped chat is not lost: the next message typed in it starts a new program
that resumes the same chat record.

A chat counts as idle when nothing has written to its chat record, and the
program has not been started, for --hours (default 24). The chat record is
~/.claude/projects/*/<chat id>.jsonl plus the <chat id>/ folder next to it.

Without --apply it only reports. Usage:
  idle-chats [--apply] [--hours H] [--log PATH]
"""
import argparse
import fcntl
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

HOME = Path.home()
CCD_DIR = HOME / ".claude" / "remote" / "ccd-cli"
SERVER_DIR = HOME / ".claude" / "remote" / "srv"
PROJECTS = HOME / ".claude" / "projects"
STATE = HOME / ".local" / "state" / "idle-chats"
CHAT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ID_FLAGS = ("--resume", "--session-id")
TERM_WAIT = 20.0
CHILD_WAIT = 5.0
POLL = 0.2


def boot_time():
    with open("/proc/stat") as handle:
        for line in handle:
            if line.startswith("btime "):
                return int(line.split()[1])
    raise RuntimeError("no btime in /proc/stat")


def read_stat(pid):
    """Return (ppid, pgrp, start ticks) for a pid, or None if it is gone.

    A zombie (exited, not yet reaped by its parent) counts as gone."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            text = handle.read()
    except OSError:
        return None
    # The command name is in brackets and may hold any byte; the fields after
    # the last ")" are fixed: state, ppid, pgrp, session, ... starttime (index 19).
    try:
        fields = text[text.rindex(")") + 2:].split()
        if fields[0] == "Z":
            return None
        return int(fields[1]), int(fields[2]), int(fields[19])
    except (ValueError, IndexError):
        return None


def epoch(ticks):
    return BOOT + ticks / TICKS


def read_argv(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def chat_id(argv):
    """The chat id from --resume or --session-id, or None."""
    for index, arg in enumerate(argv):
        for flag in ID_FLAGS:
            value = None
            if arg == flag and index + 1 < len(argv):
                value = argv[index + 1]
            elif arg.startswith(flag + "="):
                value = arg.split("=", 1)[1]
            if value is not None and CHAT_ID.fullmatch(value):
                return value
    return None


def last_activity(cid, projects):
    """Newest write time of the chat record and its folder, or None if there is no record."""
    records = list(projects.glob(f"*/{cid}.jsonl"))
    if not records:
        return None
    newest = 0.0
    for record in records:
        try:
            newest = max(newest, record.stat().st_mtime)
        except OSError:
            pass
        folder = record.with_suffix("")
        if folder.is_dir():
            for path in [folder, *folder.rglob("*")]:
                try:
                    newest = max(newest, path.stat().st_mtime)
                except OSError:
                    pass
    return newest or None


def proc_table():
    table = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            stat = read_stat(int(entry))
            if stat is not None:
                table[int(entry)] = stat
    return table


def ancestors(pid, table):
    seen = set()
    while pid in table and pid not in seen and pid > 1:
        seen.add(pid)
        pid = table[pid][0]
    return seen


def descendants(pid, table):
    found, todo = set(), [pid]
    while todo:
        parent = todo.pop()
        for child, stat in table.items():
            if stat[0] == parent and child not in found:
                found.add(child)
                todo.append(child)
    return found


def under(argv, folder):
    return bool(argv) and argv[0].startswith(str(folder).rstrip("/") + "/")


def find_chats(ccd_dir, server_dir, projects, table):
    chats = []
    for pid, (ppid, pgrp, ticks) in sorted(table.items()):
        argv = read_argv(pid)
        if not under(argv, ccd_dir):
            continue
        cid = chat_id(argv)
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            cwd = None
        chats.append({"pid": pid, "ticks": ticks, "start": epoch(ticks), "chat": cid, "cwd": cwd,
                      "desktop": under(read_argv(ppid), server_dir), "leader": pgrp == pid,
                      "activity": last_activity(cid, projects) if cid else None})
    return chats


def decide(chat, now, hours, protected=()):
    """Return (stop?, reason, idle hours or None)."""
    if chat["pid"] in protected:
        return False, "runs this job", None
    if not chat["desktop"]:
        return False, "not started by the desktop app", None
    if chat["chat"] is None:
        return False, "no chat id", None
    if chat["activity"] is None:
        return False, "no chat record", None
    idle = (now - max(chat["activity"], chat["start"])) / 3600
    if idle >= hours:
        return True, "idle", idle
    return False, "active", idle


def same_process(pid, ticks):
    stat = read_stat(pid)
    return stat is not None and stat[2] == ticks


def signal_each(tracked, sig):
    for pid, ticks in list(tracked.items()):
        if same_process(pid, ticks):
            try:
                os.kill(pid, sig)
            except OSError:
                pass


def track(pid, group, tracked):
    """Add every live descendant of pid, and every member of the process group, to tracked."""
    table = proc_table()
    found = descendants(pid, table) if pid in table else set()
    if group is not None:
        found |= {other for other, stat in table.items() if stat[1] == group}
    for other in found:
        tracked.setdefault(other, table[other][2])


def wait_gone(tracked, seconds, rescan=None):
    deadline = time.monotonic() + seconds
    while True:
        if rescan is not None:
            rescan()
        if not any(same_process(pid, ticks) for pid, ticks in tracked.items()):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL)


def stop(chat):
    """Stop a chat program and every helper it started. Return a short result word.

    Helpers are found again and again while the program shuts down, so ones it
    starts late are caught too. Its process group catches helpers that were
    orphaned before they could be seen."""
    pid, ticks = chat["pid"], chat["ticks"]
    if not same_process(pid, ticks):
        return "already gone"
    group = pid if chat["leader"] else None
    main = {pid: ticks}
    helpers = {}
    track(pid, group, helpers)
    signal_each(main, signal.SIGTERM)
    result = "stopped"
    rescan = lambda: track(pid, group, helpers)  # noqa: E731
    if not wait_gone(main, TERM_WAIT, rescan):
        signal_each(main, signal.SIGKILL)
        result = "killed"
        if not wait_gone(main, CHILD_WAIT, rescan):
            return "would not stop"
    rescan()
    helpers.pop(pid, None)
    if not wait_gone(helpers, 0):
        signal_each(helpers, signal.SIGTERM)
        if not wait_gone(helpers, CHILD_WAIT, rescan):
            signal_each(helpers, signal.SIGKILL)
            if not wait_gone(helpers, CHILD_WAIT):
                return result + ", helpers left"
    return result


def write_log(path, entry):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as handle:
        handle.write(json.dumps(entry) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stop idle Claude desktop chat programs.")
    parser.add_argument("--apply", action="store_true", help="stop idle chats (default: report only)")
    parser.add_argument("--hours", type=float, default=24.0, help="idle time before a stop (default 24)")
    parser.add_argument("--log", type=Path, default=STATE / "runs.jsonl", help="JSON line log of each run")
    parser.add_argument("--ccd-dir", type=Path, default=CCD_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--server-dir", type=Path, default=SERVER_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--projects", type=Path, default=PROJECTS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.hours > 0:
        parser.error("--hours must be more than 0")

    args.log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = os.open(args.log.with_name(args.log.name + ".lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock_fd)
        print("idle-chats: another run is in progress", file=sys.stderr)
        return 2
    try:
        return run(args)
    finally:
        os.close(lock_fd)  # also released by the kernel if the run crashes


def run(args):
    now = time.time()
    table = proc_table()
    protected = ancestors(os.getpid(), table)
    chats = find_chats(args.ccd_dir, args.server_dir, args.projects, table)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(now))
    mode = "apply" if args.apply else "report only"
    print(f"{stamp} idle-chats ({mode}, idle {args.hours:g} h): {len(chats)} chat programs")
    entries, failed = [], 0
    for chat in chats:
        should_stop, reason, idle = decide(chat, now, args.hours, protected)
        if should_stop:
            # Look again just before stopping, in case the chat woke up.
            fresh = last_activity(chat["chat"], args.projects)
            if fresh is not None and fresh > chat["activity"]:
                chat["activity"] = fresh
            should_stop, reason, idle = decide(chat, time.time(), args.hours, protected)
        result = "kept"
        if should_stop:
            result = stop(chat) if args.apply else "would stop"
            if result == "would not stop" or result.endswith("helpers left"):
                failed += 1
        idle_text = "?" if idle is None else f"{idle:.1f} h"
        print(f"  pid {chat['pid']:>8}  chat {chat['chat'] or '-'}  idle {idle_text:>8}  "
              f"{reason}: {result}  ({chat['cwd'] or '?'})")
        entries.append({"pid": chat["pid"], "chat": chat["chat"], "cwd": chat["cwd"],
                        "idle_hours": None if idle is None else round(idle, 2),
                        "reason": reason, "result": result})
    stopped = sum(1 for e in entries if e["result"].startswith(("stopped", "killed")))
    print(f"  done: {stopped} stopped, {len(entries) - stopped} left running")
    write_log(args.log, {"time": round(now), "apply": args.apply, "hours": args.hours,
                         "chats": entries})
    return 1 if failed else 0


BOOT = boot_time()
TICKS = os.sysconf("SC_CLK_TCK")

if __name__ == "__main__":
    sys.exit(main())
