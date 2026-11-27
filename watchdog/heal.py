"""Restart a service that runs but does not answer. No AI: probe, one restart, message.

systemd (Restart=always) and Docker (restart unless-stopped) already bring back a
service that crashed. This covers the other case: the process is alive but its port
gives no HTTP answer. Run from cron every 5 minutes.

Rules:
- Two failed probes in a row (about 5 to 10 minutes) before acting.
- At most one restart per service per 6 hours; a second failure inside that window is
  reported for Rex instead of restarted again.
- T3 Code is never restarted automatically: restarting it stops every running T3
  thread. It is reported only.
- Nothing happens while the pause file ~/.local/state/heal/pause exists (planned work;
  it is ignored and removed after 2 hours, like the Mac mini guard's pause).

    python3 -m watchdog.heal            # probe and act
    python3 -m watchdog.heal --dry-run  # probe and print what it would do
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
STATE = Path(os.environ.get("HEAL_STATE") or Path.home() / ".local" / "state" / "heal")
COOLDOWN_S = 6 * 3600
MAX_PAUSE_S = 2 * 3600

# name -> (plain name, port, restart command or None for report-only)
SERVICES = {
    "home-assistant": ("Home Assistant", 8123, ["sudo", "-n", "docker", "restart", "homeassistant"]),
    "finance": ("the finance tracker", 3000, ["systemctl", "--user", "restart", "finance-tracker.service"]),
    "nutrition": ("the nutrition tracker", 3791, ["systemctl", "--user", "restart", "nutrition-tracker.service"]),
    "t3": ("T3 Code", 3773, None),
}


def probe(port: int) -> bool:
    """Any HTTP answer counts as alive (some answer 302 or 401)."""
    out = run._cmd(["curl", "-s", "-o", "/dev/null", "-m", "8", "-w", "%{http_code}", f"http://127.0.0.1:{port}/"])
    return out.strip() not in ("", "000")


def paused(now_epoch: float) -> bool:
    path = STATE / "pause"
    try:
        age = now_epoch - path.stat().st_mtime
    except OSError:
        return False
    if age > MAX_PAUSE_S:
        path.unlink(missing_ok=True)
        return False
    return True


def decide(state: dict, alive: dict, now_epoch: float) -> tuple[dict, list, list]:
    """Pure decision. Returns (new state, restarts to run, messages)."""
    restarts, messages = [], []
    new = {"fails": dict(state.get("fails", {})), "restarted": dict(state.get("restarted", {})),
           "reported": dict(state.get("reported", {}))}
    for key, ok in alive.items():
        name, _port, cmd = SERVICES[key]
        if ok:
            if new["fails"].get(key, 0) >= 2 and new["reported"].get(key):
                messages.append(f"FIXED: {name} answers again.")
            new["fails"][key] = 0
            new["reported"].pop(key, None)
            continue
        fails = new["fails"].get(key, 0) + 1
        new["fails"][key] = fails
        if fails < 2 or new["reported"].get(key):
            continue
        last = float(new["restarted"].get(key, 0))
        if cmd is None:
            messages.append(f"PROBLEM: {name} does not answer. It is not restarted automatically, "
                            "because that would stop every running T3 thread. On the server, run: "
                            "systemctl --user restart t3code.service")
            new["reported"][key] = now_epoch
        elif now_epoch - last < COOLDOWN_S:
            messages.append(f"PROBLEM: {name} stopped answering again within 6 hours of a restart. "
                            "I did not restart it again; it needs a look.")
            new["reported"][key] = now_epoch
        else:
            restarts.append(key)
            new["restarted"][key] = now_epoch
            new["fails"][key] = 0
    return new, restarts, messages


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Restart a service that does not answer.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    now_epoch = time.time()
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if paused(now_epoch):
        print("heal: paused for planned work")
        return 0
    try:
        state = json.loads((STATE / "state.json").read_text())
    except (OSError, ValueError):
        state = {}
    alive = {key: probe(port) for key, (_n, port, _c) in SERVICES.items()}
    new, restarts, messages = decide(state, alive, now_epoch)
    for key in restarts:
        name, _port, cmd = SERVICES[key]
        if args.dry_run:
            messages.append(f"(dry run) would restart {name}: {' '.join(cmd)}")
            continue
        try:
            rc = subprocess.run(cmd, capture_output=True, text=True, timeout=120).returncode
        except (OSError, subprocess.SubprocessError):
            rc = None
        messages.append(f"FIXED?: {name} was running but not answering, so I restarted it "
                        f"({'done' if rc == 0 else 'the restart command failed'}). "
                        "I will say again if it still does not answer.")
    if args.dry_run:
        print(json.dumps({"alive": alive, "restarts": restarts}, indent=1))
        print("\n".join(messages) or "nothing to do")
        return 0
    tmp = STATE / "state.json.tmp"
    tmp.write_text(json.dumps(new, indent=1))
    os.replace(tmp, STATE / "state.json")
    for text in messages:
        subprocess.run([TG_SEND, CHAT_ID, text], timeout=60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
