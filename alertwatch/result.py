"""Read a run's `claude -p --output-format json` output."""
from __future__ import annotations

import json
import re

from backlogrun.cli import USAGE_LIMIT_RE

LIST_KEYS = ("entries", "actions", "merges", "pending_checks", "needs_rex")
NOTE_MAX = 1500
_BLOCK_RE = re.compile(r"^```json[ \t]*\n(.*?)\n```[ \t]*$", re.MULTILINE | re.DOTALL)
_RESET_RE = re.compile(r"limit reached\|(\d{9,11})")
# backlog-run's pattern misses "You've hit your limit · resets 3pm" (a real Claude
# message, see tests/test_council_backup_client.py), so alert-watch adds it.
_MORE_LIMIT_RE = re.compile(r"hit your (?:\w+ )?limit", re.IGNORECASE)


def parse_result(text: str) -> dict | None:
    """The last fenced ```json block of the run's final text, checked. Anything a
    report quoted earlier in the output is ignored."""
    blocks = _BLOCK_RE.findall(text or "")
    if not blocks:
        return None
    try:
        obj = json.loads(blocks[-1])
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    note = obj.get("note")
    if not isinstance(note, str) or not note.strip():
        return None
    for key in LIST_KEYS:
        obj.setdefault(key, [])
        if not isinstance(obj[key], list):
            return None
    obj["note"] = note[:NOTE_MAX]
    return obj


def usage_limit(data: dict | None, stdout: str, stderr: str) -> tuple[bool, float | None]:
    text = str((data or {}).get("result") or "")
    blob = "\n".join((text, (stdout or "")[-4000:], (stderr or "")[-4000:]))
    if not (USAGE_LIMIT_RE.search(blob) or _MORE_LIMIT_RE.search(blob)):
        return False, None
    m = _RESET_RE.search(blob)
    return True, float(m.group(1)) if m else None


def started(data: dict | None) -> bool:
    try:
        return int((data or {}).get("num_turns") or 0) > 0
    except (TypeError, ValueError):
        return False
