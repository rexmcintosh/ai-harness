"""Read MeetTrack database rows for a run: GET only, at most 1000 rows.

    python3 -m alertwatch.dbread <table> [postgrest filters] [--select cols] [--limit N]

A diagnose run has no general shell, so this is how it reads the database. It
sets MEETTRACK_DB_READ_ONLY=1 before splash_poller's db.py loads, so even a bug
here cannot write. Prints one JSON object per row.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_TABLE_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_COLS_RE = re.compile(r"[A-Za-z0-9_,.*()!:]+")


def _default_select():
    os.environ["MEETTRACK_DB_READ_ONLY"] = "1"
    sys.path.insert(0, str(Path(os.environ.get("ALERT_WATCH_PROJECTS", "/home/dev/projects")) / "splash_poller"))
    import db
    return db.select


def main(argv: list[str] | None = None, *, select=None) -> int:
    ap = argparse.ArgumentParser(prog="alertwatch.dbread")
    ap.add_argument("table")
    ap.add_argument("filters", nargs="?", default="")
    ap.add_argument("--select", default="*")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args(argv)
    if not _TABLE_RE.fullmatch(args.table):
        print(f"dbread: bad table name {args.table!r}", file=sys.stderr)
        return 2
    if not _COLS_RE.fullmatch(args.select):
        print(f"dbread: bad --select {args.select!r}", file=sys.stderr)
        return 2
    if not 1 <= args.limit <= 1000:
        print("dbread: --limit must be 1 to 1000 (PostgREST pages at 1000)", file=sys.stderr)
        return 2
    if re.search(r"(^|&)(limit|offset)=", args.filters):
        print("dbread: use --limit, not limit= in the filters", file=sys.stderr)
        return 2
    filters = "&".join(p for p in (args.filters, f"limit={args.limit}") if p)
    rows = (select or _default_select())(args.table, filters, args.select)
    for row in rows:
        print(json.dumps(row, ensure_ascii=False, default=str))
    if len(rows) == args.limit:
        print(f"dbread: {len(rows)} rows; there may be more (raise --limit or narrow the filter)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
