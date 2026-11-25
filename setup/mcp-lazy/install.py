#!/usr/bin/python3 -I
"""Install mcp-lazy as the Playwright browser helper for Claude Code and Codex.

    install.py            install or repair, then check
    install.py --check    check only; exit 1 if anything differs
    install.py --undo     put the original helper entries back, then show them

What it changes:
  * ~/.local/bin/mcp-lazy            a copy of mcp_lazy.py
  * ~/.claude.json  mcpServers.playwright      (through `claude mcp add-json`)
  * ~/.codex/config.toml  [mcp_servers.playwright]  command and args only

Before it replaces anything it saves the old copy in
~/.local/state/mcp-lazy/backups/. Sessions that are already running keep their
old helper until they restart.
"""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "mcp_lazy.py"
HOME = Path.home()
TARGET = HOME / ".local/bin/mcp-lazy"
STATE = HOME / ".local/state/mcp-lazy"
CLAUDE_JSON = HOME / ".claude.json"
CODEX_TOML = HOME / ".codex/config.toml"
NAME = "playwright"
# 0.0.84 is pinned because its Chromium build (1252) is the one installed in
# ~/.cache/ms-playwright. 0.0.86 expects build 1255, which is not installed.
REAL = ["npx", "-y", "@playwright/mcp@0.0.84", "--browser", "chromium"]
ORIGINAL = ["npx", "-y", "@playwright/mcp@latest", "--browser", "chromium"]


def wanted(undo=False):
    if undo:
        return ORIGINAL[0], ORIGINAL[1:]
    return str(TARGET), ["--name", NAME, "--", *REAL]


def stamp():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def backup(name, data):
    folder = STATE / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.{stamp()}"
    path.write_bytes(data)
    return path


def atomic_write(path, data, mode):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --- the stand-in file ------------------------------------------------------

def install_file():
    data = SOURCE.read_bytes()
    if TARGET.exists() and TARGET.read_bytes() == data:
        return False
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    if TARGET.exists():
        print(f"backup: {backup('mcp-lazy', TARGET.read_bytes())}")
    atomic_write(TARGET, data, 0o755)
    return True


# --- Claude ------------------------------------------------------------------

def claude_entry():
    try:
        return json.loads(CLAUDE_JSON.read_text()).get("mcpServers", {}).get(NAME)
    except (OSError, ValueError):
        return None


def claude_wanted(undo=False):
    command, args = wanted(undo)
    return {"type": "stdio", "command": command, "args": args, "env": {}}


def claude_ok(entry, undo=False):
    want = claude_wanted(undo)
    return (isinstance(entry, dict) and entry.get("command") == want["command"]
            and entry.get("args") == want["args"] and not entry.get("env")
            and entry.get("type", "stdio") == "stdio")


def set_claude(undo=False):
    current = claude_entry()
    if claude_ok(current, undo):
        return False
    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("claude CLI not found on PATH")
    if current is not None:
        print(f"backup: {backup('claude-playwright.json', json.dumps(current, indent=2).encode())}")
        subprocess.run([claude, "mcp", "remove", "-s", "user", NAME], check=True,
                       stdout=subprocess.DEVNULL)
    subprocess.run([claude, "mcp", "add-json", "-s", "user", NAME, json.dumps(claude_wanted(undo))],
                   check=True, stdout=subprocess.DEVNULL)
    if not claude_ok(claude_entry(), undo):
        raise SystemExit("~/.claude.json does not show the new entry after `claude mcp add-json`")
    return True


# --- Codex -------------------------------------------------------------------

TABLE = re.compile(r"^\[mcp_servers\.playwright\][ \t]*(#.*)?$", re.M)
NEXT_TABLE = re.compile(r"^[ \t]*\[", re.M)


def toml_string(value):
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def edit_codex(text, command, args):
    """Return text with [mcp_servers.playwright] command and args replaced.

    Only those two keys change. Everything else, including comments and the
    managed Claude-mirror block markers, stays byte for byte the same.
    """
    command_line = f"command = {toml_string(command)}"
    args_line = f"args = [{', '.join(toml_string(a) for a in args)}]"
    found = TABLE.search(text)
    if not found:
        addition = f"\n[mcp_servers.playwright]\n{command_line}\n{args_line}\nstartup_timeout_sec = 30\n"
        new = text.rstrip("\n") + "\n" + addition
    else:
        start = found.end()
        following = NEXT_TABLE.search(text, start + 1)
        end = following.start() if following else len(text)
        body = text[start:end]
        lines = body.split("\n")
        seen = set()
        for i, line in enumerate(lines):
            key = line.split("=", 1)[0].strip() if "=" in line else None
            if key == "command":
                lines[i] = command_line
                seen.add(key)
            elif key == "args":
                if not line.rstrip().endswith("]"):
                    raise ValueError("args in [mcp_servers.playwright] spans several lines; edit it by hand")
                lines[i] = args_line
                seen.add(key)
        insert = [l for k, l in (("command", command_line), ("args", args_line)) if k not in seen]
        if insert:
            lines[1:1] = insert
        new = text[:start] + "\n".join(lines) + text[end:]
    check_codex_edit(text, new, command, args)
    return new


def check_codex_edit(old, new, command, args):
    """Refuse an edit that changes anything except the helper's command and args."""
    before = tomllib.loads(old)
    after = tomllib.loads(new)
    entry = after.get("mcp_servers", {}).get(NAME, {})
    if entry.get("command") != command or entry.get("args") != list(args):
        raise ValueError("the edited Codex config does not hold the wanted entry")
    new_table = NAME not in before.get("mcp_servers", {})

    def strip(parsed):
        servers = parsed.get("mcp_servers", {})
        table = servers.get(NAME)
        if isinstance(table, dict):
            table.pop("command", None)
            table.pop("args", None)
            if new_table:
                table.pop("startup_timeout_sec", None)
            if not table:
                servers.pop(NAME)
        if "mcp_servers" in parsed and not parsed["mcp_servers"]:
            parsed.pop("mcp_servers")
        return parsed

    if strip(before) != strip(after):
        raise ValueError("the Codex edit would change other settings; nothing written")


def codex_entry():
    try:
        return tomllib.loads(CODEX_TOML.read_text()).get("mcp_servers", {}).get(NAME)
    except (OSError, ValueError):
        return None


def codex_ok(entry, undo=False):
    command, args = wanted(undo)
    return isinstance(entry, dict) and entry.get("command") == command and entry.get("args") == args


def set_codex(undo=False):
    if not CODEX_TOML.is_file():
        print(f"info no {CODEX_TOML}; Codex skipped")
        return False
    if codex_ok(codex_entry(), undo):
        return False
    original = CODEX_TOML.read_bytes()
    new = edit_codex(original.decode(), *wanted(undo)).encode()
    print(f"backup: {backup('codex-config.toml', original)}")
    if CODEX_TOML.read_bytes() != original:
        raise SystemExit("~/.codex/config.toml changed while editing; run again")
    atomic_write(CODEX_TOML, new, CODEX_TOML.stat().st_mode & 0o777)
    return True


# --- report ------------------------------------------------------------------

def running():
    lazy = real = 0
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if any(a.endswith(b"mcp-lazy") or a.endswith(b"mcp_lazy.py") for a in argv[:3]):
            lazy += 1
        elif len(argv) > 1 and argv[1].endswith(b"/playwright-mcp"):
            real += 1  # the node process of one real helper
    return lazy, real


def report(undo=False):
    problems = []
    if not undo:
        same = TARGET.exists() and TARGET.read_bytes() == SOURCE.read_bytes()
        print(f"{'ok  ' if same else 'FAIL'} {TARGET} {'matches' if same else 'differs from'} {SOURCE.name}")
        if not same:
            problems.append("file")
        if not os.access(TARGET, os.X_OK):
            print(f"FAIL {TARGET} is not executable")
            problems.append("mode")
    for label, entry, ok in (("claude", claude_entry(), claude_ok), ("codex ", codex_entry(), codex_ok)):
        if label == "codex " and not CODEX_TOML.is_file():
            print(f"info codex: no {CODEX_TOML}; skipped")
            continue
        good = ok(entry, undo)
        shown = " ".join([entry.get("command", "?"), *entry.get("args", [])]) if isinstance(entry, dict) else "missing"
        print(f"{'ok  ' if good else 'FAIL'} {label} playwright: {shown}")
        if not good:
            problems.append(label.strip())
    lazy, real = running()
    print(f"info stand-ins running: {lazy}; real browser helpers running: {real}")
    print("info sessions that were running before the change keep their old helper until they restart")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--undo", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Linux":
        raise SystemExit("mcp-lazy needs Linux (/proc)")
    if os.geteuid() == 0:
        raise SystemExit("run as the user who runs Claude and Codex, not root")
    if not args.check:
        if not args.undo and install_file():
            print(f"installed {TARGET}")
        if set_claude(args.undo):
            print("changed ~/.claude.json playwright entry")
        if set_codex(args.undo):
            print("changed ~/.codex/config.toml playwright entry")
    problems = report(args.undo)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
