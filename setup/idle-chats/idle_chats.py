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
PROJECTS = HOME / ".claude" / "projects"
STATE = HOME / ".local" / "state" / "idle-chats"
CHAT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ID_FLAGS = ("--resume", "--session-id")
TERM_WAIT = 20.0
CHILD_WAIT = 5.0


def boot_time():
    with open("/proc/stat") as handle:
        for line in handle:
            if line.startswith("btime "):
                return int(line.split()[1])
    raise RuntimeError("no btime in /proc/stat")


def read_stat(pid):
    """Return (ppid, start time in epoch seconds) for a pid, or None if it is gone.

    A zombie (exited, not yet reaped by its parent) counts as gone."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            text = handle.read()
    except OSError:
        return None
    # The command name is in brackets and may hold spaces; the fields after it are fixed.
    fields = text[text.rindex(")") + 2:].split()
    if fields[0] == "Z":
        return None
    ppid = int(fields[1])
    start = BOOT + int(fields[19]) / TICKS
    return ppid, start


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
            for path in folder.rglob("*"):
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
        for child, (ppid, _start) in table.items():
            if ppid == parent and child not in found:
                found.add(child)
                todo.append(child)
    return found


def find_chats(ccd_dir, projects, table):
    prefix = str(ccd_dir).rstrip("/") + "/"
    chats = []
    for pid, (_ppid, start) in sorted(table.items()):
        argv = read_argv(pid)
        if not argv or not argv[0].startswith(prefix):
            continue
        cid = chat_id(argv)
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            cwd = None
        chats.append({"pid": pid, "start": start, "chat": cid, "cwd": cwd,
                      "activity": last_activity(cid, projects) if cid else None})
    return chats


def decide(chat, now, hours, protected=()):
    """Return (stop?, reason, idle hours or None)."""
    if chat["pid"] in protected:
        return False, "runs this job", None
    if chat["chat"] is None:
        return False, "no chat id", None
    if chat["activity"] is None:
        return False, "no chat record", None
    idle = (now - max(chat["activity"], chat["start"])) / 3600
    if idle >= hours:
        return True, "idle", idle
    return False, "active", idle


def same_process(pid, start):
    stat = read_stat(pid)
    return stat is not None and abs(stat[1] - start) < 1


def wait_gone(pids_starts, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not any(same_process(pid, start) for pid, start in pids_starts):
            return True
        time.sleep(0.2)
    return not any(same_process(pid, start) for pid, start in pids_starts)


def signal_each(pids_starts, sig):
    for pid, start in pids_starts:
        if same_process(pid, start):
            try:
                os.kill(pid, sig)
            except OSError:
                pass


def stop(chat, table):
    """Stop a chat program and anything it left behind. Return a short result word."""
    pid, start = chat["pid"], chat["start"]
    if not same_process(pid, start):
        return "already gone"
    kids = [(kid, table[kid][1]) for kid in descendants(pid, table) if kid in table]
    signal_each([(pid, start)], signal.SIGTERM)
    result = "stopped"
    if not wait_gone([(pid, start)], TERM_WAIT):
        signal_each([(pid, start)], signal.SIGKILL)
        result = "killed"
        if not wait_gone([(pid, start)], CHILD_WAIT):
            return "would not stop"
    left = [(kid, kstart) for kid, kstart in kids if same_process(kid, kstart)]
    if left:
        signal_each(left, signal.SIGTERM)
        if not wait_gone(left, CHILD_WAIT):
            signal_each(left, signal.SIGKILL)
            if not wait_gone(left, CHILD_WAIT):
                return result + ", helpers left"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stop idle Claude desktop chat programs.")
    parser.add_argument("--apply", action="store_true", help="stop idle chats (default: report only)")
    parser.add_argument("--hours", type=float, default=24.0, help="idle time before a stop (default 24)")
    parser.add_argument("--log", type=Path, default=STATE / "runs.jsonl", help="JSON line log of each run")
    parser.add_argument("--ccd-dir", type=Path, default=CCD_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--projects", type=Path, default=PROJECTS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.hours > 0:
        parser.error("--hours must be more than 0")

    args.log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = open(args.log.with_name(args.log.name + ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("idle-chats: another run is in progress", file=sys.stderr)
        return 2

    now = time.time()
    table = proc_table()
    protected = ancestors(os.getpid(), table)
    chats = find_chats(args.ccd_dir, args.projects, table)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(now))
    mode = "apply" if args.apply else "report only"
    print(f"{stamp} idle-chats ({mode}, idle {args.hours:g} h): {len(chats)} chat programs")
    entries, failed = [], 0
    for chat in chats:
        should_stop, reason, idle = decide(chat, now, args.hours, protected)
        result = "kept"
        if should_stop:
            # Look again just before stopping, in case the chat woke up.
            fresh = last_activity(chat["chat"], args.projects)
            if fresh is not None and fresh > chat["activity"]:
                chat["activity"] = fresh
                should_stop, reason, idle = decide(chat, time.time(), args.hours, protected)
        if should_stop:
            result = stop(chat, table) if args.apply else "would stop"
            if result in ("would not stop",) or result.endswith("helpers left"):
                failed += 1
        idle_text = "?" if idle is None else f"{idle:.1f} h"
        print(f"  pid {chat['pid']:>8}  chat {chat['chat'] or '-'}  idle {idle_text:>8}  "
              f"{reason}: {result}  ({chat['cwd'] or '?'})")
        entries.append({"pid": chat["pid"], "chat": chat["chat"], "cwd": chat["cwd"],
                        "idle_hours": None if idle is None else round(idle, 2),
                        "reason": reason, "result": result})
    stopped = sum(1 for e in entries if e["result"] in ("stopped", "killed"))
    print(f"  done: {stopped} stopped, {len(entries) - stopped} left running")
    with open(args.log, "a") as handle:
        handle.write(json.dumps({"time": round(now), "apply": args.apply, "hours": args.hours,
                                 "chats": entries}) + "\n")
    return 1 if failed else 0


BOOT = boot_time()
TICKS = os.sysconf("SC_CLK_TCK")

if __name__ == "__main__":
    sys.exit(main())
