#!/usr/bin/env python3
"""Install the owner policy and helper locally; default is a read-only plan."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

START = "<!-- owner-venice-delegate:start -->"
END = "<!-- owner-venice-delegate:end -->"
SKILL_BLOCK = """## Owner's Venice routes

Before selecting a route, read `~/.claude/delegate/venice.md` in full.
That owner policy overrides this skill's provider matrices, subscription
tie-breakers, opposite-provider review rules, and CLI-only dispatch mechanics.
Use the existing profile routes for work outside the Venice policy and fallbacks.
"""
GLOBAL_BLOCK = """## Venice helper and second-opinion routing (2026-09-13)

For delegation or independent review, read `~/.claude/delegate/venice.md`.
It is Rex's standing policy and overrides conflicting Delegate plugin routing.
Use `VENICE_SECOND_OPINION_KEY` for work reviews and second opinions, and
`VENICE_CODE_HELPER_KEY` for suitable bounded coding helpers. Use
`venice-delegate council-review --diff` for the existing local Council review
workflow. The wrapper loads only the review credential. Open-PR CI keeps its
existing configured credentials; this local change does not rotate CI secrets.
Routine, task-scoped Venice helper and review calls are authorized. No secrets
or unrelated private data may be sent. Merge and deployment rules still apply.
"""


def block(text, body):
    value = START + "\n" + body.rstrip() + "\n" + END
    if text.count(START) != text.count(END) or text.count(START) > 1:
        raise ValueError("Repair the incomplete or duplicate owner-Venice marker block")
    if START in text:
        before, tail = text.split(START, 1)
        _, after = tail.split(END, 1)
        return before + value + after
    # Place skill policy before route selection, after its frontmatter.
    if text.startswith("---\n"):
        offset = text.index("\n---\n", 4) + 5
        return text[:offset] + "\n" + value + "\n" + text[offset:]
    return text.rstrip() + "\n\n" + value + "\n"


def plan(home, source, python):
    def check_target(path):
        if not path.is_absolute() or not path.resolve().is_relative_to(home.resolve()):
            raise ValueError(f"Install target must be inside {home}: {path}")
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError(f"Install target contains a symlink; use its real owned path: {path}")

    changes = []
    policies = [(home / ".claude/CLAUDE.md", GLOBAL_BLOCK),
                (home / ".codex/skills/delegate/SKILL.md", SKILL_BLOCK)]
    installed = home / ".claude/plugins/installed_plugins.json"
    if installed.exists():
        try:
            for name, entries in json.loads(installed.read_text()).get("plugins", {}).items():
                if name.startswith("delegate@"):
                    for entry in entries:
                        path = Path(entry["installPath"])
                        path.relative_to(home)
                        policies.append((path / "skills/delegate/SKILL.md", SKILL_BLOCK))
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            raise ValueError(f"Repair delegate metadata in {installed}: {type(error).__name__}") from None
    for path, body in policies:
        check_target(path)
        try:
            original = path.read_text()  # Do not silently skip an active surface.
            changes.append((path, block(original, body).encode(), path.stat().st_mode & 0o777))
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot update {path}: {error}") from None
    policies = changes
    changes = []
    changes.extend([
        (home / ".claude/delegate/venice.md", (source / "venice.md").read_bytes(), 0o600),
        (home / ".local/lib/venice-delegate/venice_delegate.py", (source / "venice_delegate.py").read_bytes(), 0o600),
        (home / ".local/bin/venice-delegate",
         ("#!/bin/sh\nexec " + shlex.quote(str(python)) + " " +
          shlex.quote(str(home / ".local/lib/venice-delegate/venice_delegate.py")) + ' "$@"\n').encode(), 0o755),
    ])
    changes.extend(policies)  # Install dependencies before activating their policy.
    for path, _, _ in changes:
        check_target(path)
    return [(p, data, mode) for p, data, mode in changes
            if not p.exists() or p.read_bytes() != data or p.stat().st_mode & 0o777 != mode]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    home = Path.home()
    python = home / ".local/share/pipx/venvs/council/bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        parser.error("The existing Council Python environment is required")
    try:
        subprocess.run([str(python), "-V"], check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        parser.error(f"Council Python cannot start: {python}")
    if args.apply:
        lock_path = home / ".local/state/venice-delegate/install.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = lock_path.open("a")  # Held until process exit, including planning.
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another Venice install is running; rerun after it finishes")
    try:
        changes = plan(home, Path(__file__).parent, python)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    backup = home / ".local/state/venice-delegate/backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if args.apply and changes:
        backup.mkdir(parents=True, mode=0o700)
        manifest = []
        for path, data, mode in changes:
            old = path.read_bytes() if path.exists() else None
            manifest.append({"path": str(path), "existed": old is not None,
                             "old_mode": path.stat().st_mode & 0o777 if old is not None else None,
                             "old_sha256": hashlib.sha256(old).hexdigest() if old is not None else None,
                             "new_sha256": hashlib.sha256(data).hexdigest()})
            if old is not None:
                saved = backup / path.relative_to(home)
                saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                saved.write_bytes(old)
                saved.chmod(0o600)
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for path, data, mode in changes:
        print(f"{'Install' if args.apply else 'Would install'}: {path}")
        if not args.apply:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".venice-install-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, mode)
            os.replace(temp, path)
        finally:
            Path(temp).unlink(missing_ok=True)
    if not changes:
        print("No changes")
    if args.apply and changes:
        print(f"Backups: {backup}")


if __name__ == "__main__":
    main()
