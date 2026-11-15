"""The alert-watch inbox: one JSON file per captured message.

bin/tg-send writes `inbox/<id>.json` (via a `.tmp-` file and a rename). The
dispatcher claims every entry by renaming it into `batch/<run_id>/` (an atomic
rename, as in fixit/queue.py, so two claimers never share an entry), then moves
the batch to `done/` or `failed/`, or puts entries back in `inbox/`.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

SUBDIRS = ("inbox", "batch", "done", "failed", "runs")
_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
_TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _mkdir700(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def ensure_dirs(state: Path) -> None:
    _mkdir700(Path(state))
    for sub in SUBDIRS:
        _mkdir700(Path(state) / sub)


def _fsync_dir(folder: Path) -> None:
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


def _write_json(path: Path, obj: dict) -> None:
    tmp = path.with_name(f".tmp-{path.name}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _problem(entry) -> str | None:
    if not isinstance(entry, dict):
        return "not a JSON object"
    if entry.get("version") != 1:
        return f"unknown version {entry.get('version')!r}"
    if not isinstance(entry.get("text"), str):
        return "no text"
    eid = entry.get("id")
    if not isinstance(eid, str) or not _ID_RE.fullmatch(eid) or ".." in eid:
        return "bad id"
    return None


def _visible_json(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*.json") if not p.name.startswith("."))


UNREADABLE_AGE_S = 3600
# The chat id of the dispatcher's own check entries (add_entry). tg-send always
# records the real chat, so only an alert-watch-check entry may carry it.
INTERNAL_CHAT = "alert-watch-internal"
CHECK_SOURCE = "alert-watch-check"


def _eligibility(path: Path, watch_chats) -> str:
    """watched | other | unknown. Only a proven watched destination is watched: a chat
    in watch_chats, or the dispatcher's own check entry. A missing or empty chat, or a
    file that cannot be read or parsed, is unknown, and unknown is never claimed."""
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        chat = str(entry.get("chat_id") or "")
    except (OSError, ValueError, AttributeError):
        return "unknown"
    if chat in watch_chats:
        return "watched"
    if chat == INTERNAL_CHAT and entry.get("source") == CHECK_SOURCE:
        return "watched"
    return "other" if chat else "unknown"


def purge_other_chats(state: Path, watch_chats) -> int:
    """Delete inbox messages sent to a chat alert-watch does not watch (tg-send skips
    them; this is the second guard). One that cannot be deleted stays in the inbox and
    is never claimed."""
    removed = 0
    for p in _visible_json(Path(state) / "inbox"):
        if _eligibility(p, watch_chats) == "other":
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def stale_unknown(state: Path, watch_chats, now: float) -> list[Path]:
    """Captures whose chat has stayed unproven (unreadable, or no chat) for an hour.
    The caller queues a names-only note before it deletes them."""
    out = []
    for p in _visible_json(Path(state) / "inbox"):
        try:
            old = p.stat().st_mtime < now - UNREADABLE_AGE_S
        except OSError:
            continue
        if old and _eligibility(p, watch_chats) == "unknown":
            out.append(p)
    return out


def claim_all(state: Path, run_id: str, limit: int | None = None,
              watch_chats=None) -> tuple[list[dict], list[tuple[str, str]]]:
    """Claim inbox entries (the oldest first, at most `limit` valid ones) into
    batch/<run_id>/. Returns (valid entries, [(file name, reason)]); broken files go
    to failed/<run_id>/ with a .reason file."""
    state = Path(state)
    batch = state / "batch" / run_id
    entries: list[dict] = []
    broken: list[tuple[str, str]] = []
    for src in _visible_json(state / "inbox"):
        if limit is not None and len(entries) >= limit:
            break
        if watch_chats is not None and _eligibility(src, watch_chats) != "watched":
            continue  # never claimed: another chat's, or its chat cannot be proven yet
        batch.mkdir(parents=True, exist_ok=True)
        dst = batch / src.name
        try:
            os.rename(src, dst)
        except OSError:
            continue  # another claimer took it
        try:
            entry = json.loads(dst.read_text(encoding="utf-8"))
            reason = _problem(entry)
        except (OSError, ValueError) as e:
            entry, reason = None, f"unreadable: {e.__class__.__name__}"
        if reason:
            broken.append((src.name, reason))  # moved by move_broken, after its note is queued
            continue
        entry["_file"] = src.name
        entries.append(entry)
    if batch.exists() and not any(batch.iterdir()):
        batch.rmdir()
    return entries, broken


def move_broken(state: Path, run_id: str, broken: list[tuple[str, str]]) -> None:
    state = Path(state)
    failed = state / "failed" / run_id
    for name, reason in broken:
        src = state / "batch" / run_id / name
        if src.exists():
            failed.mkdir(parents=True, exist_ok=True)
            os.rename(src, failed / name)
            (failed / f"{name}.reason").write_text(reason + "\n", encoding="utf-8")
    batch = state / "batch" / run_id
    if batch.exists() and not any(batch.iterdir()):
        batch.rmdir()


def finish(state: Path, run_id: str, to: str) -> None:
    if to not in ("done", "failed"):
        raise ValueError(to)
    src = Path(state) / "batch" / run_id
    if not src.exists():
        return
    dst = Path(state) / to / run_id
    if dst.exists():  # broken entries of this run may already be there
        for p in src.iterdir():
            os.rename(p, dst / p.name)
        src.rmdir()
    else:
        os.rename(src, dst)


def file_away(state: Path, run_id: str, entries: list[dict], to: str) -> None:
    """Move these entries' files out of batch/<run_id>/ into <to>/<run_id>/ (for the
    entries the rules dropped or ignored, so a later release() cannot resend them)."""
    if to not in ("done", "failed"):
        raise ValueError(to)
    src_dir = Path(state) / "batch" / run_id
    dst_dir = Path(state) / to / run_id
    for entry in entries:
        src = src_dir / (entry.get("_file") or f"{entry['id']}.json")
        if src.exists():
            dst_dir.mkdir(parents=True, exist_ok=True)
            os.rename(src, dst_dir / src.name)


def _clean(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if not k.startswith("_")}


def release(state: Path, run_id: str, entries: list[dict], *, count_attempt: bool,
            max_attempts: int = 2) -> list[str]:
    """Put a failed run's entries back in the inbox. With count_attempt, each gets
    attempts + 1, and one that reaches max_attempts goes to failed/<run_id>/ instead.
    Returns the ids that went to failed/."""
    state = Path(state)
    failed_ids = []
    for entry in entries:
        out = _clean(entry)
        if count_attempt:
            out["attempts"] = int(out.get("attempts") or 0) + 1
        name = entry.get("_file") or f"{out['id']}.json"
        if count_attempt and out["attempts"] >= max_attempts:
            folder = state / "failed" / run_id
            folder.mkdir(parents=True, exist_ok=True)
            _write_json(folder / name, out)
            failed_ids.append(out["id"])
        else:
            _write_json(state / "inbox" / name, out)
    # The returned and failed copies are on disk for good before the batch goes.
    _fsync_dir(state / "inbox")
    _fsync_dir(state / "failed" / run_id)
    shutil.rmtree(state / "batch" / run_id, ignore_errors=True)
    return failed_ids


def batch_entries(state: Path, run_id: str) -> list[dict]:
    out = []
    for p in _visible_json(Path(state) / "batch" / run_id):
        try:
            entry = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not _problem(entry):
            entry["_file"] = p.name
            out.append(entry)
    return out


def stale_batches(state: Path) -> list[str]:
    folder = Path(state) / "batch"
    return sorted(p.name for p in folder.iterdir() if p.is_dir()) if folder.exists() else []


def recover_stale(state: Path) -> list[str]:
    """Batches left by a dispatcher that died mid-run. Its run may have acted, so they
    are never replayed: they go to failed/. Call only while holding the dispatch lock."""
    state = Path(state)
    stale = []
    for batch in sorted(p for p in (state / "batch").iterdir() if p.is_dir()):
        finish(state, batch.name, "failed")
        stale.append(batch.name)
    return stale


def seen(state: Path, eid: str) -> bool:
    """An entry with this id is in the inbox, a batch, done/ or failed/."""
    state = Path(state)
    name = f"{eid}.json"
    return (state / "inbox" / name).exists() or any(
        True for sub in ("batch", "done", "failed") for _ in (state / sub).glob(f"*/{name}"))


def clean_tmp(state: Path, now: float, age_s: int = 3600) -> int:
    """Remove half-written .tmp- capture files that a killed tg-send left behind."""
    removed = 0
    for p in (Path(state) / "inbox").glob(".tmp-*"):
        try:
            if p.stat().st_mtime < now - age_s:
                p.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def add_entry(state: Path, text: str, source: str, eid: str | None = None,
              chat_id: str = INTERNAL_CHAT) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    eid = eid or now.strftime("%Y%m%dT%H%M%S.%fZ") + "-" + secrets.token_hex(4)
    entry = {"version": 1, "id": eid, "captured_at": now.strftime(_TS_FMT), "chat_id": chat_id,
             "text": text, "source": source, "cwd": "", "attempts": 0}
    _write_json(Path(state) / "inbox" / f"{eid}.json", entry)
    _fsync_dir(Path(state) / "inbox")  # on disk before the caller records it as enqueued
    return eid


def _captured_epoch(path: Path) -> float | None:
    try:
        stamp = json.loads(path.read_text(encoding="utf-8"))["captured_at"]
        return dt.datetime.strptime(stamp, _TS_FMT).replace(tzinfo=dt.timezone.utc).timestamp()
    except (OSError, ValueError, KeyError, TypeError):
        try:
            return path.stat().st_mtime
        except OSError:
            return None  # claimed by the dispatcher meanwhile


def oldest_age_s(state: Path, now: float) -> float | None:
    stamps = [t for t in (_captured_epoch(p) for p in _visible_json(Path(state) / "inbox")) if t is not None]
    return now - min(stamps) if stamps else None


def prune(state: Path, now: float, days: int = 30) -> int:
    cutoff = now - days * 86400
    removed = 0
    for sub in ("done", "failed"):
        folder = Path(state) / sub
        if not folder.exists():
            continue
        for d in folder.iterdir():
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    return removed
