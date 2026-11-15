"""One JSON record per dispatcher run in <state>/runs/, and the counts read from them.

Record keys: run_id, started_at (epoch), ended_at, status (ok | failed | limit |
skipped), reason, mode, entries, session_id, rc, timed_out, result, note_status.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import secrets
from pathlib import Path

COUNTED = ("ok", "failed")


def new_run_id(now: float) -> str:
    stamp = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def _path(state: Path, run_id: str) -> Path:
    return Path(state) / "runs" / f"{run_id}.json"


def write_run(state: Path, record: dict) -> Path:
    path = _path(state, record["run_id"])
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1)
    return path


def update_run(state: Path, run_id: str, **fields) -> dict:
    path = _path(state, run_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(fields)
    tmp = path.with_name(f".tmp-{path.name}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return record


def _all_runs(state: Path) -> list[dict]:
    out = []
    for p in (Path(state) / "runs").glob("*.json"):
        if p.name.startswith("."):
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            float(rec["started_at"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        out.append(rec)
    out.sort(key=lambda r: (float(r["started_at"]), r.get("run_id", "")), reverse=True)
    return out


def recent_runs(state: Path, now: float, hours: float) -> list[dict]:
    cutoff = now - hours * 3600
    return [r for r in _all_runs(state) if float(r["started_at"]) >= cutoff]


def _merges(record: dict) -> list[dict]:
    result = record.get("result") or {}
    merges = result.get("merges") if isinstance(result, dict) else None
    return [m for m in merges or [] if isinstance(m, dict)]


def counts(state: Path, now: float) -> dict:
    day = recent_runs(state, now, 24)
    counted = [r for r in day if r.get("status") in COUNTED]
    return {
        "runs_day": len(counted),
        "runs_hour": sum(1 for r in counted if float(r["started_at"]) >= now - 3600),
        "merges_day": sum(1 for r in day for m in _merges(r) if not m.get("reverted")),
    }


def fail_streak(state: Path) -> int:
    streak = 0
    for r in _all_runs(state):
        status = r.get("status")
        if status == "failed":
            streak += 1
        elif status == "ok":
            break
    return streak
