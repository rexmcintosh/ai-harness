"""Free disk space before the disk alerts fire. No AI, explicit paths only.

The watchdog warns at 85% full on the server, and the Mac mini guard at under 10 GB
free on the Mac. This runs first, at 80% (server) and under 15 GB (Mac), and removes
only things that are rebuilt or downloaded again on their own:

Step 1, always safe (server):
- old VS Code server copies in ~/.vscode-server/cli/servers: it keeps the two newest
  (lru.json), the copy that matches the newest `code-*` CLI, and any copy in use.
- the npm download cache (~/.npm/_cacache; never _npx, which holds the MCP servers),
  unreferenced pnpm store files (`pnpm store prune`), the apt cache (`apt-get clean`)
  and dangling Docker images (`docker image prune`, untagged layers only).
Report only, when the server is still 85% full or more after that: the big items
that need Rex's call, and `node_modules` (downloaded libraries, rebuilt with `npm ci`)
in projects with a lock file, no commit and no install for 30 days, not named in the
crontab or a user service, and with no process inside. That last list is never removed
automatically: no snapshot of cron, services and processes proves nothing needs them.
Mac step (over the VM's existing ssh to the mini): app updater leftovers
  (~/Library/Caches: VS Code and Bitwarden ShipIt, bitwarden-updater).

Never touched: chat history (~/.claude, ~/.codex), ~/.claude/remote, Playwright
browsers, project files, archives. What is left over is reported for Rex to decide.

At most one cleanup per 6 hours. CLEANED says what was freed and the disk before and
after; PROBLEM (at most once a day) when the server is still 85% full or more.

    python3 -m watchdog.cleanup            # check and act (cron, hourly)
    python3 -m watchdog.cleanup --dry-run  # list what it would remove, change nothing
    python3 -m watchdog.cleanup --force    # run step 1 now, whatever the disk use
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import fixkit

NAME = "cleanup"
HOME = Path.home()
TRIGGER_PCT = 80
STILL_FULL_PCT = 85
MAC_TRIGGER_GB = 15
COOLDOWN_S = 6 * 3600
PROBLEM_EVERY_S = 24 * 3600
STALE_S = 30 * 86400
MINI = "bebop_admin@mini"
VSCODE_SERVERS = HOME / ".vscode-server" / "cli" / "servers"
PROJECTS = HOME / "projects"
LOCK_FILES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")
MAC_CACHES = ("com.microsoft.VSCode.ShipIt", "com.bitwarden.desktop.ShipIt", "bitwarden-updater")
LEFT_FOR_REX = ("~/home/dev (a stray copy of projects, 4.6 GB on 2026-11-27)",
                "/var/lib/containerd (5.2 GB)", "~/projects/_archive (1.8 GB)",
                "Claude and Codex chat history (~/.claude/projects, ~/.codex)")


def df_pct(path: str = "/") -> int | None:
    try:
        out = subprocess.run(["df", "-P", path], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)%\s+\S+\s*$", out.strip())
    return int(m.group(1)) if m else None


def size_bytes(path: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda e: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_blocks * 512
            except OSError:
                pass
    return total


def gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def in_use_paths() -> list[str]:
    """Every running process's cwd and command line, to keep anything in use."""
    seen = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            seen.append(os.readlink(f"/proc/{pid}/cwd"))
        except OSError:
            pass
        try:
            seen.append(Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace"))
        except OSError:
            pass
    return seen


def old_vscode_servers(servers_dir: Path, cli_dir: Path, in_use: list[str]) -> list[Path]:
    """Server copies to remove: not among the two newest in lru.json, not the copy for
    the newest `code-*` CLI, not in use. Unreadable lru.json -> remove nothing."""
    try:
        lru = json.loads((servers_dir / "lru.json").read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(lru, list) or not lru:
        return []
    keep = set(str(x) for x in lru[:2])
    clis = sorted(cli_dir.glob("code-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if clis:
        keep.add("Stable-" + clis[0].name.removeprefix("code-"))
    out = []
    for d in sorted(servers_dir.glob("Stable-*")):
        if not d.is_dir() or d.is_symlink() or d.name in keep:
            continue
        if any(str(d) in p for p in in_use):
            continue
        out.append(d)
    return out


def referenced_projects(texts: list[str]) -> set[str]:
    """Project names that a crontab line or a user service names (~/projects/<name>)."""
    names = set()
    for text in texts:
        names.update(re.findall(r"projects/([A-Za-z0-9_.-]+)", text))
    return names


def stale_node_modules(projects: Path, referenced: set[str], in_use: list[str], now_epoch: float,
                       last_commit=None) -> list[Path]:
    last_commit = last_commit or _last_commit
    out = []
    for proj in sorted(projects.iterdir()):
        nm = proj / "node_modules"
        if proj.name.startswith((".", "_")) or not nm.is_dir() or nm.is_symlink():
            continue
        if proj.name in referenced or not any((proj / f).is_file() for f in LOCK_FILES):
            continue
        try:
            if now_epoch - nm.stat().st_mtime < STALE_S:
                continue
        except OSError:
            continue
        commit = last_commit(proj)
        if commit is None or now_epoch - commit < STALE_S:
            continue
        if any(_names(p, proj) for p in in_use):
            continue
        out.append(nm)
    return out


def _names(text: str, path: Path) -> bool:
    """True when text (a cwd or a command line) names path or anything inside it, and
    not merely a longer name that starts the same (projects/app vs projects/app-2)."""
    return re.search(re.escape(str(path)) + r"(?=/|\s|$)", text) is not None


def _last_commit(proj: Path) -> float | None:
    try:
        out = subprocess.run(["git", "-C", str(proj), "log", "-1", "--format=%ct"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        return float(out) if out else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _service_texts() -> list[str] | None:
    """The crontab and every user service file, or None when any of them cannot be read
    (then no project can be called unused)."""
    try:
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if cron.returncode != 0 or not cron.stdout.strip():
        return None
    texts = [cron.stdout]
    for unit in (HOME / ".config" / "systemd" / "user").glob("*.service"):
        try:
            texts.append(unit.read_text())
        except OSError:
            return None
    return texts


def _run(cmd: list, timeout: int = 600) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, env=fixkit.child_env()).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _remove(path: Path, dry: bool) -> int:
    """Bytes freed: measured before and after, so a partial removal is not overstated."""
    n = size_bytes(path)
    if dry:
        return n
    shutil.rmtree(path, ignore_errors=True)
    return n - (size_bytes(path) if path.exists() else 0)


def step1(dry: bool, in_use: list[str]) -> list[str]:
    done = []
    # A VS Code session that starts between the in-use check and the removal loses its
    # server copy; VS Code downloads it again on the next connect. Only old copies go.
    olds = old_vscode_servers(VSCODE_SERVERS, HOME / ".vscode-server", in_use)
    freed = sum(_remove(d, dry) for d in olds)
    if freed > 0:
        done.append(f"{len(olds)} old VS Code server copies ({gb(freed)})")
    cache = HOME / ".npm" / "_cacache"
    freed = _remove(cache, dry) if cache.is_dir() else 0
    if freed > 0:
        done.append(f"the npm download cache ({gb(freed)})")
    if shutil.which("pnpm") and (dry or _run(["pnpm", "store", "prune"])):
        done.append("unused pnpm store files")
    if dry or _run(["sudo", "-n", "apt-get", "clean"]):
        done.append("the apt cache")
    if dry or _run(["sudo", "-n", "docker", "image", "prune", "-f"]):
        done.append("dangling Docker images")
    return done


def idle_node_modules(in_use: list[str], now_epoch: float) -> str | None:
    """A note naming idle projects' node_modules that Rex may remove. Report only: no
    snapshot of cron, services and processes can prove nothing will need them."""
    texts = _service_texts()
    if texts is None:
        return None
    found = stale_node_modules(PROJECTS, referenced_projects(texts), in_use, now_epoch)
    if not found:
        return None
    size = sum(size_bytes(nm) for nm in found)
    names = ", ".join(nm.parent.name for nm in found)
    return (f"node_modules in {len(found)} projects untouched for 30 days ({names}; {gb(size)}; "
            "`npm ci` rebuilds them)")


def left_for_rex(in_use: list[str], now_epoch: float) -> list[str]:
    try:
        note = idle_node_modules(in_use, now_epoch)
    except OSError:
        note = None
    return [*LEFT_FOR_REX, *([note] if note else [])]


def mac_free_gb() -> float | None:
    try:
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", MINI,
                              "df -k /System/Volumes/Data"], capture_output=True, text=True, timeout=30).stdout
        return int(out.strip().splitlines()[-1].split()[3]) * 1024 / 1e9
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def mac_clean(dry: bool) -> bool:
    paths = " ".join(f'"$HOME/Library/Caches/{c}"' for c in MAC_CACHES)
    if dry:
        return True
    try:
        return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", MINI,
                               f"rm -rf {paths}"], capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _reserve(state: dict) -> None:
    """Save the cooldown before acting. On a full disk the save itself can fail: removing
    caches is still safe, and freeing space is the point, so the cleanup goes on (the
    cooldown is then kept only in memory for this pass)."""
    try:
        fixkit.save_state(NAME, state)
    except OSError:
        print("cleanup: could not save state (disk full?); cleaning anyway")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Free disk space before the disk alerts fire.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="run step 1 now, whatever the disk use")
    args = parser.parse_args(argv)
    now_epoch = time.time()
    lock = fixkit.take_lock(NAME)
    if lock is None:
        print("cleanup: another run is still going")
        return 0
    if fixkit.paused(now_epoch) and not args.force:
        print("cleanup: paused for planned work")
        return 0
    state = fixkit.load_state(NAME)
    messages = []
    before = df_pct()
    if before is None:
        print("cleanup: cannot read the server's disk use; skipping the server")
    cooled = now_epoch - float(state.get("last_run", 0)) >= COOLDOWN_S
    if before is not None and (args.force or args.dry_run or (before >= TRIGGER_PCT and cooled)):
        if not args.dry_run:
            state["last_run"] = now_epoch           # reserve before acting
            _reserve(state)
        in_use = in_use_paths()
        done = step1(args.dry_run, in_use)
        after = df_pct() or before
        if not args.dry_run:
            if done:
                messages.append(f"CLEANED: the server disk was {before}% full. I removed "
                                + "; ".join(done) + f". It is now {after}% full.")
        else:
            print(f"server disk {before}% full; would remove:\n- " + "\n- ".join(done or ["nothing"]))
            print("left for Rex: " + "; ".join(left_for_rex(in_use, now_epoch)))
        if not args.dry_run and after >= STILL_FULL_PCT and \
                now_epoch - float(state.get("problem_at", 0)) >= PROBLEM_EVERY_S:
            state["problem_at"] = now_epoch
            messages.append(f"PROBLEM: the server disk is still {after}% full after cleanup. "
                            "The big items left need your call: "
                            + "; ".join(left_for_rex(in_use, now_epoch)) + ".")
    if not args.dry_run:
        fixkit.queue(messages, "cleanup-outbox")    # before any later state save
        messages = []
    free = mac_free_gb()
    mac_cooled = now_epoch - float(state.get("mac_last_run", 0)) >= COOLDOWN_S
    if free is not None and (args.dry_run or (free < MAC_TRIGGER_GB and mac_cooled)):
        if args.dry_run:
            print(f"Mac free {free:.0f} GB; would remove ~/Library/Caches: {', '.join(MAC_CACHES)}")
        else:
            state["mac_last_run"] = now_epoch       # reserve before acting
            _reserve(state)
            if mac_clean(False):
                later = mac_free_gb()
                messages.append(f"CLEANED: the Mac mini had {free:.0f} GB free. I removed app updater "
                                f"leftovers. It now has {later or free:.0f} GB free.")
            else:
                messages.append(f"PROBLEM: the Mac mini has only {free:.0f} GB free, and removing app "
                                "updater leftovers over ssh failed. It needs a look.")
    if args.dry_run:
        return 0
    fixkit.queue(messages, "cleanup-outbox")   # queue, save, flush: a note is never lost
    fixkit.save_state(NAME, state)
    fixkit.flush("cleanup-outbox")
    return 0


if __name__ == "__main__":
    sys.exit(main())
