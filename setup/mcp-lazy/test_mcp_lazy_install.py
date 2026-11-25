"""Tests for the mcp-lazy installer's config edits."""
import importlib.util
import tomllib
from pathlib import Path

import pytest

# Load by path under a unique name: setup/delegate-venice also has an install.py.
_spec = importlib.util.spec_from_file_location(
    "mcp_lazy_install", Path(__file__).resolve().parent / "install.py")
install = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install)

CONFIG = """model = "gpt"
# BEGIN managed Claude mirror
[mcp_servers.context7]
url = "https://mcp.context7.com/mcp"
startup_timeout_sec = 20

[mcp_servers.playwright]
command = "npx"
args = ["-y", "@playwright/mcp@latest", "--browser", "chromium"]
startup_timeout_sec = 30

[[skills.config]]
path = "/x/SKILL.md"
enabled = false
# END managed Claude mirror
"""


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    """Keep every test away from the real ~/.local/state/mcp-lazy."""
    monkeypatch.setattr(install, "STATE", tmp_path / "state")
    monkeypatch.setattr(install, "BASELINE", tmp_path / "state" / "baseline.json")
    return tmp_path / "state"


def test_edit_changes_only_command_and_args():
    command, args = install.wanted("codex")
    new = install.edit_codex(CONFIG, command, args)
    parsed = tomllib.loads(new)
    assert parsed["mcp_servers"]["playwright"] == {
        "command": command, "args": args, "startup_timeout_sec": 30}
    # Every other line is unchanged, byte for byte.
    old_lines, new_lines = CONFIG.splitlines(), new.splitlines()
    changed = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert len(old_lines) == len(new_lines)
    assert [old_lines[i].split(" ")[0] for i in changed] == ["command", "args"]


def test_edit_is_idempotent_and_undo_restores():
    command, args = install.wanted("codex")
    once = install.edit_codex(CONFIG, command, args)
    assert install.edit_codex(once, command, args) == once
    assert install.edit_codex(once, *install.wanted("codex", undo=True)) == CONFIG


def test_missing_table_is_added():
    text = 'model = "gpt"\n'
    new = install.edit_codex(text, *install.wanted("codex"))
    assert tomllib.loads(new)["mcp_servers"]["playwright"]["startup_timeout_sec"] == 30
    assert new.startswith(text)


def test_multiline_args_are_refused():
    text = CONFIG.replace('args = ["-y", "@playwright/mcp@latest", "--browser", "chromium"]',
                          'args = [\n  "-y",\n  "x",\n]')
    with pytest.raises(ValueError):
        install.edit_codex(text, *install.wanted("codex"))


def test_other_change_is_refused():
    with pytest.raises(ValueError):
        install.check_codex_edit(CONFIG, CONFIG.replace('model = "gpt"', 'model = "other"'),
                                 "npx", ["-y", "@playwright/mcp@latest", "--browser", "chromium"])


def test_claude_entry_keeps_every_other_field(state):
    current = {"type": "stdio", "command": "npx", "args": ["old"], "env": {"A": "1"}, "extra": 5}
    new = install.claude_new_entry(current)
    assert new == dict(current, command=str(install.TARGET), args=install.wanted("claude")[1])
    assert install.claude_ok(new)
    assert not install.claude_ok(dict(new, args=new["args"][:-1]))
    assert not install.claude_ok(None)


def test_undo_restores_the_recorded_original(state):
    custom = {"type": "stdio", "command": "/opt/pw", "args": ["--x"], "env": {"A": "1"}}
    install.record_baseline("claude", custom)
    installed = install.claude_new_entry(custom)
    # Recording the stand-in itself must not replace the real original.
    install.record_baseline("claude", installed)
    undone = install.claude_new_entry(installed, undo=True)
    assert undone == custom
    assert install.claude_ok(undone, undo=True)
    # Without a record, undo falls back to the packaged default.
    assert install.wanted("codex", undo=True) == (install.ORIGINAL[0], install.ORIGINAL[1:])


def test_backups_are_private(state):
    path = install.backup("x.json", b"secret")
    assert path.read_bytes() == b"secret"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert state.stat().st_mode & 0o777 == 0o700
