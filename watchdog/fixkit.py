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


def send_all(messages: list, outbox_name: str, *, sender=None) -> None:
    """Send waiting messages oldest first, then new ones. Stop at the first failure and
    after MAX_SENDS_PER_RUN, so a Telegram outage never holds up the fixer. Keep what is
    left for the next run (at most 20). Each fixer has its own outbox_name."""
    sender = sender or tg_send
    outbox = STATE / f"{outbox_name}.json"
    try:
        waiting = json.loads(outbox.read_text())
    except (OSError, ValueError):
        waiting = []
    if not isinstance(waiting, list):
        waiting = []
    queue, left, sent = waiting + list(messages), [], 0
    for i, text in enumerate(queue):
        if sent >= MAX_SENDS_PER_RUN or not sender(text):
            left = queue[i:]
            break
        sent += 1
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = STATE / f"{outbox_name}.json.tmp"
    tmp.write_text(json.dumps(left[-20:]))
    os.replace(tmp, outbox)


def tg_send(text: str) -> bool:
    try:
        return subprocess.run([TG_SEND, CHAT_ID, text], timeout=45).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
