"""Thin wrapper around headless `claude -p`. Authenticates via the Max session
(no API key). Default has NO tools; pass allowed_tools for steps that need MCP/file writes.

Prompts are fed via stdin (``-p -``) to avoid exceeding ARG_MAX on large transcripts.
The ``build_argv`` helper exposes the argv for testing; ``run`` always uses stdin."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence


class LLMError(RuntimeError):
    pass


class UsageLimitError(LLMError):
    """Claude rejected the call because the subscription usage/session limit is
    exhausted. The notice appears on stdout, e.g. 'You've hit your session limit'."""


# Printed on stdout with a non-zero exit. A miss just degrades to a generic
# LLMError (retried next run), so keep the set specific to avoid false positives.
_USAGE_LIMIT_RE = re.compile(
    r"hit your session limit|usage limit|limit reached|rate limit", re.IGNORECASE
)

_SETTINGS_PATH = str(Path(__file__).parent / "headless-settings.json")
_EMPTY_MCP_PATH = str(Path(__file__).parent / "headless-mcp.json")


# Cron runs with PATH=/usr/bin:/bin, and the claude CLI moved to ~/.local/bin in the
# 2026-07-25 installer migration — so a bare which() miss must probe known install dirs.
_FALLBACK_BINS = (
    Path.home() / ".local" / "bin" / "claude",
    Path("/usr/local/bin/claude"),
    Path("/usr/bin/claude"),
)


def _claude_bin() -> str:
    found = shutil.which("claude")
    if found:
        return found
    for candidate in _FALLBACK_BINS:
        if candidate.is_file():
            return str(candidate)
    return "claude"


def build_argv(model: str, allowed_tools: Optional[Sequence[str]] = None,
               skip_permissions: bool = False) -> List[str]:
    """Return the argv for a ``claude -p -`` call (prompt fed via stdin)."""
    argv = [_claude_bin(), "-p", "-", "--model", model, "--output-format", "text",
            "--settings", _SETTINGS_PATH,
            "--strict-mcp-config", "--mcp-config", _EMPTY_MCP_PATH]
    if allowed_tools:
        argv += ["--allowedTools", *allowed_tools]
    if skip_permissions:
        argv += ["--dangerously-skip-permissions"]
    return argv


def run(prompt: str, model: str, allowed_tools: Optional[Sequence[str]] = None,
        skip_permissions: bool = False, timeout: int = 600) -> str:
    """Run claude with *prompt* supplied via stdin to stay within ARG_MAX."""
    proc = subprocess.run(
        build_argv(model, allowed_tools, skip_permissions),
        input=prompt,
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        detail = ((proc.stdout or "").strip() + "\n" + (proc.stderr or "").strip()).strip()
        if _USAGE_LIMIT_RE.search(detail):
            raise UsageLimitError(f"claude usage limit: {detail[:500]}")
        raise LLMError(f"claude exited {proc.returncode}: {detail[:500]}")
    return proc.stdout.strip()
