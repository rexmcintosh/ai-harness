"""Shared parts of the small automatic fixers (heal, fix_backup, fix_jobs, cleanup).

Each fixer has its own lock, state file and outbox under ~/.local/state/heal/, so a slow
fixer never holds up another one, and two never write the same file. They share the
pause file (planned work) and the Telegram sender.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
from pathlib import Path

from . import run

CHAT_ID = "7735693897"
TG_SEND = os.environ.get("WATCHDOG_TG_SEND") or str(run.BASE / "bin" / "tg-send")
STATE = Path(os.environ.get("HEAL_STATE") or Path.home() / ".local" / "state" / "heal")
MAX_PAUSE_S = 2 * 3600
MAX_SENDS_PER_RUN = 5
OUTBOX_KEEP = 50


def child_env() -> dict:
    """The environment for commands a fixer runs. Cron gives no XDG_RUNTIME_DIR, and
    without it `systemctl --user` fails ("Failed to connect to bus")."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def paused(now_epoch: float) -> bool:
    """True while ~/.local/state/heal/pause exists (planned work). A pause file older
    than 2 hours is removed and ignored, so a forgotten pause never lasts."""
    path = STATE / "pause"
    try:
        age = now_epoch - path.stat().st_mtime
    except OSError:
        return False
    if age > MAX_PAUSE_S:
        path.unlink(missing_ok=True)
        return False
    return True


def take_lock(name: str):
    """An exclusive lock for one fixer, or None when another run of it is still going.
    Keep the returned file open for the whole run."""
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    fh = open(STATE / f"{name}.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def load_state(name: str) -> dict:
    try:
        data = json.loads((STATE / f"{name}.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(name: str, data: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = STATE / f"{name}.json.tmp"
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, STATE / f"{name}.json")


def _outbox(outbox_name: str) -> Path:
    return STATE / f"{outbox_name}.json"


def _read_outbox(outbox_name: str) -> list:
    try:
        waiting = json.loads(_outbox(outbox_name).read_text())
    except (OSError, ValueError):
        return []
    return waiting if isinstance(waiting, list) else []


def _write_outbox(outbox_name: str, items: list) -> None:
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = STATE / f"{outbox_name}.json.tmp"
    tmp.write_text(json.dumps(trim(items)))
    os.replace(tmp, _outbox(outbox_name))


def queue(messages: list, outbox_name: str) -> None:
    """Write new notes to the fixer's outbox before anything is sent. A fixer queues its
    notes, then saves its state, then flushes: a crash anywhere repeats a note at worst,
    and never loses one."""
    if messages:
        _write_outbox(outbox_name, _read_outbox(outbox_name) + list(messages))


def flush(outbox_name: str, *, sender=None) -> None:
    """Send queued notes oldest first. Stop at the first failure and after
    MAX_SENDS_PER_RUN, so a Telegram outage never holds up the fixer; the rest stay
    (at most OUTBOX_KEEP, PROBLEM lines first). A send that timed out may have been
    delivered; it is sent again (a duplicate is better than a lost PROBLEM)."""
    sender = sender or tg_send
    waiting = _read_outbox(outbox_name)
    left, sent = [], 0
    for i, text in enumerate(waiting):
        if sent >= MAX_SENDS_PER_RUN or not sender(text):
            left = waiting[i:]
            break
        sent += 1
    if waiting:
        _write_outbox(outbox_name, left)


def send_all(messages: list, outbox_name: str, *, sender=None) -> None:
    """queue() then flush(), for a fixer with no state to save in between."""
    queue(messages, outbox_name)
    flush(outbox_name, sender=sender)


def trim(items: list, keep: int = None) -> list:
    """At most `keep` items in order: every PROBLEM line first (newest if too many), then
    the newest others."""
    keep = OUTBOX_KEEP if keep is None else keep
    if len(items) <= keep:
        return list(items)
    problems = [i for i, t in enumerate(items) if str(t).startswith("PROBLEM")]
    chosen = set(problems[-keep:])
    for i in range(len(items) - 1, -1, -1):
        if len(chosen) >= keep:
            break
        chosen.add(i)
    return [items[i] for i in sorted(chosen)]


def tg_send(text: str) -> bool:
    try:
        return subprocess.run([TG_SEND, CHAT_ID, text], timeout=45).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
