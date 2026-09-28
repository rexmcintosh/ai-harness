#!/usr/bin/env python3
# bebop/findings_line.py
"""One line for the morning briefing: new code-review findings on main.

The diem drain runs a council review of every repo's new commits on main each night. Those
reviews were saved and read by nobody, including the ~20 a week that asked for changes.
`diem.findings` now records each "request changes" verdict in the drain's state dir with
status `new`; `diem findings --ack <id>` marks one seen. This line is the count of `new`.

Same rules as backlog_line.py next to it:

  * **Code, not the model.** `run-briefing.sh` appends the finished string after the agent
    has answered, so the model cannot drop or reword it.
  * **Silence when there is nothing new.** An empty string means no line at all.
  * **Fail open.** A missing or broken file costs the line, never the briefing. Nothing
    here raises past `main()`, and it always exits 0.

Reads one JSON file and nothing else. Standard library only, so it runs under any python3.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

FINDINGS_ENV = "BEBOP_FINDINGS_FILE"    # tests point this at their own file
DEFAULT_FINDINGS = (".local", "state", "diem", "findings.json")

TAIL = "Look: diem findings"


def findings_path() -> Path:
    """The findings file: $BEBOP_FINDINGS_FILE, else the diem drain's default state dir."""
    override = os.environ.get(FINDINGS_ENV)
    return Path(override).expanduser() if override else Path.home().joinpath(*DEFAULT_FINDINGS)


def briefing_line(records) -> str:
    """The line, or "" when nothing is new. Pure: no file, no env."""
    if not isinstance(records, list):
        return ""
    n = sum(1 for r in records
            if isinstance(r, dict) and r.get("id") and r.get("status") == "new")
    if not n:
        return ""                       # the silence is the point
    noun = "finding" if n == 1 else "findings"
    return f"{n} new code-review {noun} on main. {TAIL}"


def line_from_file(path=None) -> str:
    """The line for one findings file. Returns "" for anything it cannot make sense of."""
    try:
        text = Path(path or findings_path()).read_text(encoding="utf-8")
        return briefing_line(json.loads(text))
    except Exception:                   # noqa: BLE001 - a broken file never costs the briefing
        return ""


def main(argv=None) -> int:
    try:
        sys.stdout.write(line_from_file())
    except Exception:                   # noqa: BLE001 - belt and braces; stdout IS the briefing
        return 0
    return 0                            # never non-zero: the caller must not see a failure


if __name__ == "__main__":
    raise SystemExit(main())
