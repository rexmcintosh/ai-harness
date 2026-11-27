#!/usr/bin/python3
"""mini-guard: the Mac mini watches the Linux VM it hosts, and starts it when it stopped.

Runs every 2 minutes from launchd on the mini (`io.bebop.mini-guard`). Python 3.9,
standard library only. See README.md and docs/uptime-design-2026-11-27.md.

Rules (version 1):
- It only ever STARTS a VM that Lima reports as Stopped on two ticks in a row. It never
  stops or restarts a running VM: today every stop acts like a power cut.
- At most MAX_STARTS starts in 24 hours, counted before each attempt and kept across
  reboots. Then it alerts and stops acting.
- A planned-work pause (at most 2 hours) or the maintenance lock suppresses actions and
  outage alerts. An expired pause is removed and reported.
- Mode "observe" (the default) alerts but never acts. Mode "start" may start the VM.
- Messages only when something changes, one reminder every 6 hours, and a "fixed"
  message when it clears.

Commands: tick | status | pause MINUTES [REASON] | resume | test-alert
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("HOME", "/Users/bebop_admin"))
STATE_DIR = Path(os.environ.get("MINI_GUARD_STATE", HOME / ".local" / "state" / "mini-guard"))
CONFIG = Path(os.environ.get("MINI_GUARD_CONFIG", HOME / ".config" / "mini-guard" / "config.json"))
LIMACTL = os.environ.get("MINI_GUARD_LIMACTL", "/opt/homebrew/bin/limactl")
TAILSCALE = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
INSTANCE = "vps"
VM_TAILNET_NAME = "bebop-vm"
AUTOSTART_JOB = "io.lima-vm.autostart.vps"

TICK_S = 120
MAX_STARTS = 2
START_WINDOW_S = 24 * 3600
MAX_PAUSE_S = 2 * 3600
BOOT_GRACE_S = 600          # after the mini boots or the VM starts, give it 10 minutes
START_COOLDOWN_S = 600      # at least 10 minutes between two starts
REMIND_S = 6 * 3600
MAC_DISK_MIN_GB = 10
VM_DISK_MAX_PCT = 90

# How long a problem must last before it becomes an alert (seconds).
CONFIRM = {
    "vm-stopped": 0,
    "vm-status": 240,
    "guest": 600,
    "service": 360,
    "tailnet": 600,
    "vm-disk": 0,
    "mac-disk": 0,
    "battery": 0,
    "lima": 0,
}

# Services checked from inside the guest: any HTTP answer counts as alive.
SERVICES = {
    "t3": ("T3 Code", 3773),
    "home-assistant": ("Home Assistant", 8123),
    "finance": ("the finance tracker", 3000),
    "nutrition": ("the nutrition tracker", 3791),
}

GUEST_PROBE = (
    "for p in " + " ".join(str(port) for _, port in SERVICES.values()) + "; do "
    "c=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:$p/ 2>/dev/null); "
    "echo \"port $p ${c:-000}\"; done; "
    "echo \"disk $(df -P / | awk 'NR==2{print $5}')\""
)


# ---------------------------------------------------------------- running commands

class Runner:
    """Runs a command with a time limit. Tests replace it."""

    def run(self, argv: list, timeout: float) -> tuple:
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout
        except (OSError, subprocess.TimeoutExpired):
            return None, ""


def now() -> float:
    return time.time()


# ---------------------------------------------------------------- probes

def vm_status(runner: Runner) -> str:
    rc, out = runner.run([LIMACTL, "list", "--json", INSTANCE], 30)
    if rc != 0:
        return "Unknown"
    try:
        return json.loads(out.strip().splitlines()[0]).get("status") or "Unknown"
    except (ValueError, IndexError, AttributeError):
        return "Unknown"


def guest_probe(runner: Runner):
    """Service answers and disk use from inside the guest, or None if the guest is unreachable."""
    rc, out = runner.run([LIMACTL, "shell", "--workdir", "/", INSTANCE, "--", "sh", "-c", GUEST_PROBE], 60)
    if rc != 0 or "port " not in out:
        return None
    ports, disk = {}, None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "port":
            ports[int(parts[1])] = parts[2]
        elif len(parts) == 2 and parts[0] == "disk":
            try:
                disk = int(parts[1].rstrip("%"))
            except ValueError:
                pass
    return {"ports": ports, "disk_pct": disk}


def tailnet_ok(runner: Runner) -> bool:
    rc, _ = runner.run([TAILSCALE, "ping", "-c", "1", "--timeout", "5s", VM_TAILNET_NAME], 20)
    return rc == 0


def mac_free_gb(runner: Runner):
    rc, out = runner.run(["/bin/df", "-k", "/System/Volumes/Data"], 15)
    if rc != 0:
        return None
    try:
        return int(out.strip().splitlines()[-1].split()[3]) / (1024 * 1024)
    except (ValueError, IndexError):
        return None


def power(runner: Runner):
    """('ac' | 'battery' | None, percent or None) from pmset."""
    rc, out = runner.run(["/usr/bin/pmset", "-g", "batt"], 15)
    if rc != 0:
        return None, None
    source = "battery" if "'Battery Power'" in out or "'UPS Power'" in out else ("ac" if "'AC Power'" in out else None)
    pct = None
    for token in out.replace(";", " ").split():
        if token.endswith("%"):
            try:
                pct = int(token.rstrip("%"))
            except ValueError:
                pass
    return source, pct


def mac_uptime_s(runner: Runner):
    rc, out = runner.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], 10)
    if rc != 0:
        return None
    try:
        sec = int(out.split("sec =")[1].split(",")[0])
        return now() - sec
    except (IndexError, ValueError):
        return None


# ---------------------------------------------------------------- state

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, path)


def log(line: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = STATE_DIR / "guard.log"
    try:
        if path.stat().st_size > 1_000_000:
            os.replace(path, path.with_suffix(".log.1"))
    except OSError:
        pass
    with open(path, "a") as handle:
        handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")


def config() -> dict:
    data = load_json(CONFIG, {})
    data.setdefault("mode", "observe")
    return data


# ---------------------------------------------------------------- messages

def send(text: str, cfg: dict, opener=urllib.request.urlopen) -> bool:
    """Send to Telegram with the guard's own bot. The token never goes on a command line."""
    token_file = cfg.get("telegram_token_file")
    chat = cfg.get("chat_id")
    if not token_file or not chat:
        return False
    try:
        token = Path(token_file).read_text().strip()
    except OSError:
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        with opener(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10) as reply:
            return 200 <= reply.status < 300
    except Exception:
        return False


MAX_SENDS_PER_TICK = 5


def deliver(messages: list, cfg: dict, sender=send) -> None:
    """Send new messages plus any still waiting from earlier ticks, oldest first. Stops at
    the first failure (Telegram or the network is down) and sends at most
    MAX_SENDS_PER_TICK, so a backlog never holds up the checks. Keeps at most 20."""
    outbox_path = STATE_DIR / "outbox.json"
    outbox = load_json(outbox_path, [])
    if not isinstance(outbox, list):
        outbox = []
    outbox += messages
    left, sent = [], 0
    for i, text in enumerate(outbox):
        if sent >= MAX_SENDS_PER_TICK or not sender(text, cfg):
            left = outbox[i:]
            break
        sent += 1
    for text in messages:
        log("message: " + text.replace("\n", " / "))
    save_json(outbox_path, left[-20:])


# ---------------------------------------------------------------- the tick

def pause_state(t: float):
    """(paused?, note). Removes an expired or too-long pause."""
    path = STATE_DIR / "pause.json"
    data = load_json(path, None)
    if not data:
        return False, None
    try:
        until = float(data.get("until", 0))
    except (TypeError, ValueError, AttributeError):
        until = float("nan")
    if not (until == until) or until in (float("inf"), float("-inf")) \
            or until <= t or until - t > MAX_PAUSE_S + 60:
        try:
            path.unlink()
        except OSError:
            pass
        return False, "pause ended"
    return True, data.get("reason") or "planned work"


def observe(runner: Runner, t: float) -> dict:
    status = vm_status(runner)
    obs = {"status": status, "guest": None, "tailnet": None,
           "mac_free_gb": mac_free_gb(runner), "uptime_s": mac_uptime_s(runner)}
    obs["power"], obs["battery_pct"] = power(runner)
    if status == "Running":
        obs["guest"] = guest_probe(runner)
        obs["tailnet"] = tailnet_ok(runner)
    return obs


def problems(obs: dict, in_grace: bool) -> dict:
    """Current problems: key -> plain-English text. Grace hides slow-start problems."""
    found = {}
    status = obs["status"]
    if status == "Stopped":
        found["vm-stopped"] = "The server (bebop-vm) is stopped."
    elif status != "Running":
        found["vm-status"] = f"Lima reports the server as '{status}'."
    elif obs["guest"] is None:
        if not in_grace:
            found["guest"] = "The server is running but does not answer (it may be hung)."
    else:
        if not in_grace:
            for key, (name, port) in SERVICES.items():
                if obs["guest"]["ports"].get(port, "000") == "000":
                    found["service:" + key] = f"{name} does not answer on the server."
            if obs["tailnet"] is False:
                found["tailnet"] = ("The server is fine, but the Mac mini cannot reach it over "
                                    "Tailscale, so your devices may not reach it either.")
        disk = obs["guest"].get("disk_pct")
        if disk is not None and disk >= VM_DISK_MAX_PCT:
            found["vm-disk"] = f"The server's disk is {disk}% full."
    free = obs.get("mac_free_gb")
    if free is not None and free < MAC_DISK_MIN_GB:
        found["mac-disk"] = f"The Mac mini has only {free:.0f} GB of disk left."
    if obs.get("power") == "battery":
        pct = obs.get("battery_pct")
        found["battery"] = "The Mac mini is running on its UPS battery" + (f" ({pct}% left)." if pct is not None else ".")
    return found


def confirm_s(key: str) -> int:
    return CONFIRM.get(key.split(":")[0], 0)


def outage_key(key: str) -> bool:
    """Problems that planned work causes on purpose; a pause hides them."""
    return key.split(":")[0] in ("vm-stopped", "vm-status", "guest", "service", "tailnet")


def tick(runner: Runner, cfg: dict, t: float, lock_held: bool) -> list:
    """One check. Returns the messages to send. Saves state."""
    state = load_json(STATE_DIR / "state.json", {})
    incidents = state.get("incidents", {})
    starts = [s for s in state.get("starts", []) if t - s < START_WINDOW_S]
    messages = []

    paused, pause_note = pause_state(t)
    if lock_held:
        paused, pause_note = True, pause_note or "maintenance lock held"
    obs = observe(runner, t)

    uptime = obs.get("uptime_s")
    last_start = max(starts) if starts else 0
    in_grace = (uptime is not None and uptime < BOOT_GRACE_S) or (t - last_start < BOOT_GRACE_S)
    found = problems(obs, in_grace)

    if pause_note == "pause ended" and obs["status"] != "Running":
        messages.append("Planned-work pause ended, and the server is still off.")

    # Act: start a VM that was Stopped on this tick and the one before.
    action_note = None
    if "vm-stopped" in found and not paused:
        seen_before = "vm-stopped" in incidents
        booting = uptime is not None and uptime < BOOT_GRACE_S
        if cfg.get("mode") != "start":
            action_note = "watch-only mode: I did not start it."
        elif not seen_before or booting:
            action_note = "I will start it on the next check if it is still stopped."
        elif starts and t - max(starts) < START_COOLDOWN_S:
            action_note = None          # give the last start time to work
        elif len(starts) >= MAX_STARTS:
            action_note = (f"I have already tried to start it {len(starts)} times in 24 hours, so I stopped trying. "
                           "On the mini, open Terminal and run: limactl start vps")
        else:
            starts.append(t)
            state["starts"] = starts
            save_json(STATE_DIR / "state.json", state)      # count the attempt before trying
            rc, _ = runner.run(["/bin/launchctl", "kickstart", f"gui/{os.getuid()}/{AUTOSTART_JOB}"], 30)
            if rc == 0:
                action_note = f"I started it (start attempt {len(starts)} of {MAX_STARTS} allowed in 24 hours)."
            else:
                action_note = "I tried to start it, but launchd refused. On the mini, run: limactl start vps"
            log(f"start attempt rc={rc}")

    # Alerts: on change, with a reminder every 6 hours, and a "fixed" note.
    new_incidents = {}
    for key, text in found.items():
        if paused and outage_key(key):
            continue
        inc = incidents.get(key) or {"since": t, "alerted": False, "reminded": 0}
        inc["text"] = text
        note = action_note if key == "vm-stopped" else None
        if not inc["alerted"] and t - inc["since"] >= confirm_s(key):
            messages.append(f"PROBLEM: {text}" + (f" {note}" if note else ""))
            inc["alerted"], inc["reminded"], inc["note"] = True, t, note
        elif inc["alerted"] and note and note != inc.get("note"):
            messages.append(f"UPDATE: {note}")       # a new action or a new limit, said once
            inc["note"] = note
        elif inc["alerted"] and t - inc["reminded"] >= REMIND_S:
            hours = (t - inc["since"]) / 3600
            messages.append(f"STILL A PROBLEM after {hours:.0f} h: {text}")
            inc["reminded"] = t
        new_incidents[key] = inc
    for key, inc in incidents.items():
        if key not in new_incidents and inc.get("alerted"):
            if paused and outage_key(key):
                new_incidents[key] = inc   # judge it again after the pause
                continue
            minutes = (t - inc["since"]) / 60
            messages.append(f"FIXED after {minutes:.0f} min: {inc.get('text', key)}")
        elif key not in new_incidents and not inc.get("alerted"):
            blips = state.get("blips", 0) + 1
            state["blips"] = blips

    state["incidents"] = new_incidents
    state["starts"] = starts
    state["last_tick"] = t
    save_json(STATE_DIR / "state.json", state)
    save_json(STATE_DIR / "last-tick.json", {
        "time": t, "mode": cfg.get("mode"), "paused": paused, "status": obs["status"],
        "problems": sorted(new_incidents), "starts_24h": len(starts)})
    return messages


def heartbeat(cfg: dict, opener=urllib.request.urlopen) -> None:
    """Tell the outside check-in service that a tick completed."""
    url = cfg.get("healthcheck_url")
    if not url:
        return
    # Cloudflare refuses Python's default client name (error 1010), so send our own.
    req = urllib.request.Request(url, headers={"User-Agent": "mini-guard/1"})
    try:
        with opener(req, timeout=10):
            pass
    except Exception as e:
        log(f"heartbeat failed: {e.__class__.__name__} {getattr(e, 'code', '')}".rstrip())


# ---------------------------------------------------------------- commands

def try_lock(name: str):
    """A held flock on STATE_DIR/name, or None if someone else holds it."""
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = open(STATE_DIR / name, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


def cmd_tick() -> int:
    """One tick. Two locks:
    - tick.lock: only one tick at a time. A second tick changes nothing and exits.
    - lock: the maintenance lock. Restart scripts hold it while they work; a tick that
      cannot get it treats the time as planned work (no starts, no outage messages).
      A tick holds it while it may start the VM, so a restart script waits for it."""
    cfg = config()
    tick_handle = try_lock("tick.lock")
    if tick_handle is None:
        log("another tick is still running; skipped")
        return 0
    try:
        maint = try_lock("lock")
        try:
            messages = tick(Runner(), cfg, now(), lock_held=maint is None)
        except Exception as e:       # a broken tick must still be visible
            try:
                log(f"tick crashed: {e!r}")
                deliver([f"PROBLEM: the Mac mini guard crashed ({e.__class__.__name__}). "
                         "It will try again in 2 minutes."], cfg)
            except Exception:
                pass                 # the stale heartbeat still tells the VM and the outside check
            return 1
        finally:
            if maint:
                maint.close()
        try:                          # still under tick.lock: no second tick sends the same message
            deliver(messages, cfg)
            heartbeat(cfg)
        except Exception as e:       # disk full, permissions: log it; the VM sees a stale heartbeat
            try:
                log(f"delivery failed: {e!r}")
            except Exception:
                pass
            return 1
        return 0
    finally:
        tick_handle.close()


def cmd_pause(minutes: float, reason: str) -> int:
    seconds = min(max(minutes, 1) * 60, MAX_PAUSE_S)
    save_json(STATE_DIR / "pause.json", {"until": now() + seconds, "reason": reason})
    log(f"paused for {seconds / 60:.0f} min: {reason}")
    print(f"mini-guard paused for {seconds / 60:.0f} minutes ({reason})")
    return 0


def cmd_resume() -> int:
    try:
        (STATE_DIR / "pause.json").unlink()
    except OSError:
        pass
    log("resumed")
    print("mini-guard resumed")
    return 0


def cmd_status() -> int:
    print(json.dumps({"config_mode": config().get("mode"),
                      "last_tick": load_json(STATE_DIR / "last-tick.json", None),
                      "pause": load_json(STATE_DIR / "pause.json", None),
                      "outbox": len(load_json(STATE_DIR / "outbox.json", []))}, indent=1))
    return 0


def main(argv: list) -> int:
    if not argv or argv[0] == "tick":
        return cmd_tick()
    if argv[0] == "status":
        return cmd_status()
    if argv[0] == "pause" and len(argv) >= 2:
        return cmd_pause(float(argv[1]), " ".join(argv[2:]) or "planned work")
    if argv[0] == "resume":
        return cmd_resume()
    if argv[0] == "test-alert":
        ok = send("TEST: the Mac mini guard can reach you.", config())
        print("sent" if ok else "NOT sent")
        return 0 if ok else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
