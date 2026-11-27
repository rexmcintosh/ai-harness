"""Weekly "all well" line for Rex, Monday morning.

It runs the watchdog's own checks (backup, disk, services, the Mac mini guard,
alert-watch, cron logs) and sends one short Telegram message. If every check is fine
it says so; if not, it lists what needs a look. If the message does not arrive on a
Monday, something in the alerting itself is broken.

    python3 -m watchdog.weekly            # send
    python3 -m watchdog.weekly --dry-run  # print only
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import run

CHAT_ID = "7735693897"
TG_SEND = os.environ.get("WATCHDOG_TG_SEND") or str(run.BASE / "bin" / "tg-send")


def uptime_days() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text().split()[0]) / 86400
    except (OSError, ValueError, IndexError):
        return None


def guard_heartbeat() -> dict | None:
    key = Path(os.environ.get("MINI_GUARD_KEY") or Path.home() / ".ssh" / "mini_guard_read")
    if not key.exists():
        return None
    text = run.read_guard_heartbeat(key)
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def summary(statuses, now_epoch: float, up_days: float | None, guard: dict | None) -> str:
    day = time.strftime("%a %d %b", time.localtime(now_epoch))
    bad = [s for s in statuses if s.level != "ok"]
    head = f"Weekly check, {day}: " + ("all well." if not bad else f"{len(bad)} thing(s) need a look.")
    lines = [head]
    for s in bad:
        lines.append(f"- {s.level.upper()}: {s.summary}")
    facts = []
    if up_days is not None:
        facts.append(f"server up {up_days:.0f} days")
    backup = next((s for s in statuses if s.name == "backup"), None)
    if backup is not None and backup.level == "ok":
        facts.append(backup.summary)
    disk = next((s for s in statuses if s.name == "disk"), None)
    if disk is not None:
        facts.append(disk.summary.replace("root filesystem", "server disk"))
    if guard:
        facts.append(f"Mac mini guard in {guard.get('mode', '?')} mode, "
                     f"{guard.get('starts_24h', 0)} start(s) in the last 24 h")
    if facts:
        lines.append("Facts: " + "; ".join(facts) + ".")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Send the weekly all-well line.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    now_epoch = time.time()
    statuses, _ = run.collect(int(now_epoch))
    text = summary(statuses, now_epoch, uptime_days(), guard_heartbeat())
    if args.dry_run:
        print(text)
        return 0
    proc = subprocess.run([TG_SEND, CHAT_ID, "-"], input=text, text=True, timeout=60)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
