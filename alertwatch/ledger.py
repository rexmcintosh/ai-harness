"""One JSON record per dispatcher run in <state>/runs/, and the counts read from them.

Record keys: run_id, started_at (epoch), ended_at, status (ok | failed | limit |
skipped), reason, mode, entries, session_id, rc, timed_out, result, note_status.
"""
from __future__ import annotations

import datetime as dt
import fcntl
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


def _write_tmp(path: Path, record: dict) -> Path:
    tmp = path.with_name(f".tmp-{path.name}-{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    return tmp


def write_run(state: Path, record: dict) -> Path:
    """Create a run record, whole or not at all: written to a temp file, then linked
    to its name (which fails if a record already has that name)."""
    path = _path(state, record["run_id"])
    tmp = _write_tmp(path, record)
    try:
        os.link(tmp, path)
    finally:
        tmp.unlink()
    fsync_dir(path.parent)
    return path


def replace_run(state: Path, record: dict) -> Path:
    """Write a run record over whatever is there (used to repair an unreadable one)."""
    path = _path(state, record["run_id"])
    os.replace(_write_tmp(path, record), path)
    fsync_dir(path.parent)
    return path


def update_run(state: Path, run_id: str, **fields) -> dict:
    path = _path(state, run_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(fields)
    os.replace(_write_tmp(path, record), path)  # contents fsynced before the rename
    fsync_dir(path.parent)
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


def fail_streak(state: Path, since: float = 0.0) -> int:
    """Failed runs in a row, newest first, started after `since` (the last self-pause:
    resuming means those failures were seen, so they must not pause it again)."""
    streak = 0
    for r in _all_runs(state):
        if float(r["started_at"]) <= since:
            break
        status = r.get("status")
        if status == "failed":
            streak += 1
        elif status == "ok":
            break
    return streak


# --- the actions ledger: every merge, revert and restart a helper made ---------
# <state>/actions.jsonl, appended under a lock by trusted helper code (merge.py,
# restart.py), never by the model. It is the authority for the merge cap and for
# "which writers did alert-watch restart".


def fsync_dir(folder: Path) -> None:
    """Make a new or renamed file's directory entry durable."""
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def append_action(state: Path, action: dict) -> None:
    path = Path(state) / "actions.jsonl"
    new = not path.exists()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(action, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    if new:
        fsync_dir(Path(state))


def actions(state: Path) -> list[dict]:
    path = Path(state) / "actions.jsonl"
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _outcomes(acts: list[dict]) -> set[str]:
    return {str(a.get("intent")) for a in acts
            if a.get("kind") in ("merge", "revert", "merge_abandoned", "revert_abandoned") and a.get("intent")}


def merged(state: Path) -> list[dict]:
    """Every alert-watch merge: completed ones, and intents with no outcome (a crash
    after the push may have left one; callers check origin for those)."""
    acts = actions(state)
    done = _outcomes(acts)
    return [a for a in acts if a.get("kind") == "merge"
            or (a.get("kind") == "merge_intent" and a.get("id") not in done)]


def reverted(state: Path) -> set[str]:
    """to_sha of every merge that was reverted, or whose revert may have been pushed."""
    acts = actions(state)
    abandoned = {str(a.get("intent")) for a in acts if a.get("kind") == "revert_abandoned"}
    return {str(a.get("reverts")) for a in acts
            if a.get("kind") == "revert" or (a.get("kind") == "revert_intent" and a.get("id") not in abandoned)}


def merges_in_window(state: Path, now: float, hours: float = 24) -> int:
    """Merges (not reverts) the merge helper made in the window: the merge cap. An
    intent with no outcome counts, so a crash cannot hide a merge from the cap."""
    cutoff = now - hours * 3600
    return sum(1 for a in merged(state) if float(a.get("at") or 0) >= cutoff)
