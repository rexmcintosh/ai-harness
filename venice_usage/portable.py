"""Move usage rows between ledgers that never share a filesystem.

The council merge gate runs on a GitHub Actions runner. `VeniceClient._log_usage()`
fires there exactly as it does on the VPS, but it writes to the runner's own
`~/.local/state/venice-usage/ledger.db`, which is destroyed with the job — so the
CI key's spend is logged successfully into nothing.

This module is the bridge. The job exports its rows to a JSONL file that the
workflow keeps as a build artifact; the VPS downloads the artifacts and ingests
them back into the real ledger. No new service, no new secret.

Format — one JSON object per line, first line a header:

    {"kind": "venice-usage-export", "version": 1, "origin": {...},
     "exported_at": "...", "rows": 4}
    {"ts": ..., "project": ..., ..., "ext_id": "gh:owner/repo:1001:1#1"}

`ext_id` is what makes ingest idempotent: `<origin id>#<row id in the exporting
ledger>`. It doubles as provenance — `ext_id LIKE 'gh:%'` is exactly the CI rows,
and it names the repo, the run and the attempt that produced them.
"""
from __future__ import annotations
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .ledger import connect, default_db

KIND = "venice-usage-export"
VERSION = 1
ROW_FIELDS = ("ts", "project", "task_type", "model", "tokens_in", "tokens_out",
              "usd", "source", "ext_id")
SUFFIX = ".jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_rows(db_path, since, until):
    """Rows from an existing ledger. A ledger that was never created is not an
    error: a gate that failed before its first Venice call legitimately has no
    rows, and the artifact must still be written so the run is accounted for."""
    db = Path(db_path) if db_path else default_db()
    if not db.exists():
        return []
    where, params = [], []
    if since:
        where.append("ts >= ?"); params.append(since)
    if until:
        where.append("ts <= ?"); params.append(until)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = ("SELECT id, ts, project, task_type, model, tokens_in, tokens_out, usd, source"
           f" FROM usage{clause} ORDER BY id")
    with closing(connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def export_jsonl(path, *, origin: dict, since=None, until=None, db_path=None) -> int:
    """Write every ledger row to `path` as JSONL, stamped with `origin`.

    `origin["id"]` must uniquely name the producing run — it is half the
    idempotency key. Returns the number of rows written."""
    origin = dict(origin or {})
    origin_id = str(origin.get("id") or "").strip()
    if not origin_id:
        raise ValueError("export_jsonl needs a non-empty origin['id']")
    rows = _read_rows(db_path, since, until)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": KIND, "version": VERSION, "origin": origin,
                             "exported_at": _now(), "rows": len(rows)},
                            sort_keys=True) + "\n")
        for r in rows:
            out = {k: r[k] for k in ROW_FIELDS if k in r}
            out["ext_id"] = f"{origin_id}#{r['id']}"
            fh.write(json.dumps(out, sort_keys=True) + "\n")
    tmp.replace(path)
    return len(rows)


def _files(target) -> list[Path]:
    target = Path(target)
    if target.is_dir():
        return sorted(p for p in target.rglob("*" + SUFFIX) if p.is_file())
    return [target]


def _ingest_one(path: Path, db_path, totals: dict) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    head = None
    for i, line in enumerate(lines):
        if line.strip():
            try:
                head = json.loads(line)
            except ValueError as e:
                raise ValueError(f"{path}: not a venice-usage export (bad header)") from e
            lines = lines[i + 1:]
            break
    if not isinstance(head, dict) or head.get("kind") != KIND:
        raise ValueError(f"{path}: not a venice-usage export")
    if head.get("version") != VERSION:
        raise ValueError(f"{path}: unsupported export version {head.get('version')!r}")
    totals["files"] += 1

    from .ledger import append
    for line in lines:
        if not line.strip():
            continue
        totals["rows"] += 1
        try:
            row = json.loads(line)
            rowid = append(project=row["project"], task_type=row["task_type"],
                           model=row["model"], tokens_in=row.get("tokens_in") or 0,
                           tokens_out=row.get("tokens_out") or 0,
                           usd=row.get("usd"), source=row.get("source"),
                           ts=row["ts"], ext_id=row.get("ext_id"), db_path=db_path)
        except (ValueError, KeyError, TypeError):
            # One unreadable line must not cost us the rest of the artifact.
            totals["rows"] -= 1
            totals["malformed"] += 1
            continue
        totals["inserted" if rowid else "skipped"] += 1


def ingest_jsonl(target, *, db_path=None) -> dict:
    """Merge one export file, or every `*.jsonl` under a directory, into the
    ledger. Idempotent by `ext_id`. Returns per-run counts."""
    totals = {"files": 0, "rows": 0, "inserted": 0, "skipped": 0, "malformed": 0}
    for path in _files(target):
        _ingest_one(path, db_path, totals)
    return totals


def github_origin() -> dict | None:
    """Origin metadata for a GitHub Actions run, or None when not on a runner.

    `id` is repo + run + ATTEMPT. The attempt matters: re-running a failed
    workflow really does spend a second panel's worth of Venice calls, and
    deduping those away would under-report the bill."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not repo or not run_id:
        return None
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT") or "1"
    return {
        "id": f"gh:{repo}:{run_id}:{attempt}",
        "host": "github-actions",
        "repo": repo,
        "run_id": run_id,
        "run_attempt": attempt,
        "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
        "event": os.environ.get("GITHUB_EVENT_NAME", ""),
        "sha": os.environ.get("GITHUB_SHA", ""),
        "pr": os.environ.get("PR_NUMBER", ""),
    }
