"""Redaction before anything leaves the machine. Two shapes: prose/code, and log tails.

Redaction lowers risk; it does not change the data scope. A source that is out of scope
(jev/scope.py) is never sent, redacted or not.
"""
from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_CUSTOMER_ROW = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+[\"']?\s*\|")   # "email | name | ..." anywhere in the line
_MAIL_LINE = re.compile(r"^\s*(mailed|would mail|skipped)\b")
_URL_QUERY = re.compile(r"(https?://[^\s?\"']+)\?[^\s\"']+")
_TOKENISH = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")
_HANDLE = re.compile(r"(?<![\w.])@[A-Za-z0-9_.]{3,}")
_JSON_DATA_LIST = re.compile(r'"(?:articles|\w+_items)"\s*:\s*\[')
MAX_LOG_LINE = 300


def strip_json_data_lists(line: str) -> str:
    """Cut each JSON per-item data list out (`"articles": [...]`, `"x_items": [[...], ...]`):
    names and notes about items. Brackets are matched by depth (the lists nest) and quoted
    strings skipped. An unclosed list (a truncated line) is cut to the end."""
    out, pos = [], 0
    for m in _JSON_DATA_LIST.finditer(line):
        if m.start() < pos:
            continue
        depth, i, quoted = 1, m.end(), False
        while i < len(line) and depth:
            ch = line[i]
            if quoted:
                if ch == "\\":
                    i += 1
                elif ch == '"':
                    quoted = False
            elif ch == '"':
                quoted = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            i += 1
        out.append(line[pos:m.start()])
        pos = i
    out.append(line[pos:])
    return "".join(out)


def redact_text(text: str) -> str:
    """Prose and code, whole: only addresses, URL query strings and long token-shaped strings
    go. Lines are not clipped and @names stay (a recommendation is one long line; code has
    decorators)."""
    text = _EMAIL.sub("<email>", text)
    text = _URL_QUERY.sub(r"\1?<query removed>", text)
    return _TOKENISH.sub("<token>", text)


def redact_state(state):
    """redact_text over every string VALUE in a nested state. Keys are ids: left alone."""
    if isinstance(state, str):
        return redact_text(state)
    if isinstance(state, dict):
        return {k: redact_state(v) for k, v in state.items()}
    if isinstance(state, list):
        return [redact_state(v) for v in state]
    return state


def _redact_log_line(line: str) -> str:
    if _CUSTOMER_ROW.search(line):
        return "  <customer row removed>"
    if _MAIL_LINE.search(line):
        return re.sub(r":.*$", ": <subject removed>", _EMAIL.sub("<email>", line))[:MAX_LOG_LINE]
    line = strip_json_data_lists(line)      # wiki article names, per-item notes
    line = redact_text(line)
    line = _HANDLE.sub("@<handle>", line)
    return line[:MAX_LOG_LINE]


def redact_log(text: str) -> str:
    """Log tails: also drops customer rows and mail subjects whole, cuts JSON item lists,
    removes @handles, clips long lines. Runs of removed rows collapse to one line."""
    out: list[str] = []
    for line in (_redact_log_line(ln) for ln in text.splitlines()):
        if line.startswith("  <customer row") and out and out[-1] == line:
            continue
        out.append(line)
    return "\n".join(out)
