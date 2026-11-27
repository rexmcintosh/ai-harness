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
    blob = _error_blob(data, stderr)
    if not _LIMIT_RE.search(blob):
        return False, None
    m = _RESET_RE.search(blob)
    return True, float(m.group(1)) if m else None


# Claude's own login errors, and nothing looser (report text can say "authenticate").
# An invalid API key is left out: /login does not fix it, so it stays an ordinary failure.
_LOGIN_RE = re.compile(r"Failed to authenticate|OAuth (?:session|token) (?:has )?expired"
                       r"|Not logged in|Please run /login", re.IGNORECASE)


def _error_blob(data: dict | None, stderr: str) -> str:
    if data is not None:
        return str(data.get("result") or "") if data.get("is_error") else ""
    return (stderr or "")[-4000:]


def login_failed(data: dict | None, stdout: str, stderr: str) -> bool:
    """True only for a Claude login error: in the result of a run that reports
    is_error, or on stderr when there was no JSON at all. Never from stdout text."""
    blob = _error_blob(data, stderr)
    return bool(_LOGIN_RE.search(blob)) and not re.search(r"invalid api key", blob, re.IGNORECASE)


def no_model_call(data: dict | None) -> bool:
    """True only when every count in the run's JSON agrees that no model call answered:
    0 ms of API time, 0 input and output tokens, at most 1 turn (a failed login still counts one),
    and, when present, 0 cost and no model usage. Then no tool can have run, so its
    reports are safe to try again. One missing, malformed or contradicting count proves
    nothing."""
    if not isinstance(data, dict):
        return False
    api_ms, turns = data.get("duration_api_ms"), data.get("num_turns")
    usage = data.get("usage")
    out = usage.get("output_tokens") if isinstance(usage, dict) else None
    if not (type(api_ms) is int and api_ms == 0 and type(out) is int and out == 0):
        return False
    if usage.get("input_tokens", 0) != 0 or type(usage.get("input_tokens", 0)) is not int:
        return False
    if not (type(turns) is int and 0 <= turns <= 1):
        return False
    cost = data.get("total_cost_usd", 0)
    if type(cost) not in (int, float) or cost != 0:
        return False
    return data.get("modelUsage", {}) == {}


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
