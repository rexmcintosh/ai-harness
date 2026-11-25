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


def test_edit_changes_only_command_and_args():
    command, args = install.wanted()
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
    command, args = install.wanted()
    once = install.edit_codex(CONFIG, command, args)
    assert install.edit_codex(once, command, args) == once
    assert install.edit_codex(once, *install.wanted(undo=True)) == CONFIG


def test_missing_table_is_added():
    text = 'model = "gpt"\n'
    new = install.edit_codex(text, *install.wanted())
    assert tomllib.loads(new)["mcp_servers"]["playwright"]["startup_timeout_sec"] == 30
    assert new.startswith(text)


def test_multiline_args_are_refused():
    text = CONFIG.replace('args = ["-y", "@playwright/mcp@latest", "--browser", "chromium"]',
                          'args = [\n  "-y",\n  "x",\n]')
    with pytest.raises(ValueError):
        install.edit_codex(text, *install.wanted())


def test_other_change_is_refused():
    with pytest.raises(ValueError):
        install.check_codex_edit(CONFIG, CONFIG.replace('model = "gpt"', 'model = "other"'),
                                 "npx", ["-y", "@playwright/mcp@latest", "--browser", "chromium"])


def test_claude_entry_check():
    want = install.claude_wanted()
    assert install.claude_ok(dict(want))
    assert not install.claude_ok(dict(want, args=want["args"][:-1]))
    assert not install.claude_ok(dict(want, env={"A": "1"}))
    assert not install.claude_ok(None)
    assert install.claude_ok({"type": "stdio", "command": "npx", "args": install.ORIGINAL[1:]}, undo=True)
