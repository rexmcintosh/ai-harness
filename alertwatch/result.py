"""Read a run's `claude -p --output-format json` output."""
from __future__ import annotations

import json
import re

LIST_KEYS = ("entries", "actions", "merges", "pending_checks", "needs_rex")
NOTE_MAX = 1500
_BLOCK_RE = re.compile(r"^```json[ \t]*\n(.*?)\n```[ \t]*$", re.MULTILINE | re.DOTALL)
_RESET_RE = re.compile(r"limit reached\|(\d{9,11})")
# Claude's own limit messages ("Claude AI usage limit reached|<epoch>", "You've hit
# your limit \u00b7 resets 3pm", "You've hit your session limit"), and nothing looser:
# echoed report text such as "hit your API limit" must not stop the watcher.
_LIMIT_RE = re.compile(r"(?:Claude AI )?usage limit reached"
                       r"|you(?:'|\u2019)?ve hit your (?:session |usage |weekly |opus |sonnet )?limit",
                       re.IGNORECASE)


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
    """True only for a Claude limit error: in the result of a run that reports
    is_error, or on stderr when there was no JSON at all. Never from stdout text."""
    if data is not None:
        blob = str(data.get("result") or "") if data.get("is_error") else ""
    else:
        blob = (stderr or "")[-4000:]
    if not _LIMIT_RE.search(blob):
        return False, None
    m = _RESET_RE.search(blob)
    return True, float(m.group(1)) if m else None


def started(data: dict | None) -> bool:
    try:
        return int((data or {}).get("num_turns") or 0) > 0
    except (TypeError, ValueError):
        return False


def proved_idle(data: dict | None) -> bool:
    """True only when the run's JSON says, with a real integer, that it took zero
    turns. Missing or malformed counts prove nothing, so they are never replayed."""
    if not isinstance(data, dict):
        return False
    turns = data.get("num_turns")
    return type(turns) is int and turns == 0
