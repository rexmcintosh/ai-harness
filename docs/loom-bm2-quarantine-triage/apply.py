#!/usr/bin/env python3
"""Operator step for backlog item 2026-08-25-loom-triage-two-malformed-bm2-artifacts.

Loom's runtime data (loom/state.json, loom/learnings/, loom/quarantine/) is gitignored
and lives only in the main checkout, ~/projects/ai-harness (loom/cli.py hardcodes that
root). None of it can ride a branch commit, so this script applies the triage directly:

    python3 docs/loom-bm2-quarantine-triage/apply.py            # from any checkout of this branch

What it does, in this order, and only after every preflight check passed:
  1. Writes the two salvage artifacts (facts found only in the malformed bm2-* drafts)
     into loom/learnings/ under new ids and marks those ids 'distilled', so the next
     backfill weaves them. An id already at 'distilled' or later is left alone.
  2. Marks the two bm2-* session ids 'committed' (their quarantine is settled).
  3. Copies each malformed bm2-* quarantine file to the backup directory, verifies the
     copy, and only then deletes it from loom/quarantine/.

Preflight (any failure: one line on stderr, exit 1, nothing written):
  - loom/state.json exists under the target repo and already knows both bm2-* ids. This
    is the guard against a wrong or renamed path: an earlier draft pointed at the repo's
    previous name and would have built a fresh loom/ tree there and reported success.
  - both salvage sources parse with loom's own `_parse_learnings`;
  - an artifact already in loom/learnings/ under a salvage id has identical content;
  - no loom absorb/backfill/promote process is running, and loom/.run.lock is free. The
    lock is held for the whole apply.

Safe to re-run: a second run changes nothing.

It does NOT touch the original session c2b2fe92-aca1-4140-9f0f-105efcbec759's own
quarantine entry. That entry predates this item and belongs to the quarantine review
queue described in docs/loom-quarantine-disposition.md.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))          # this checkout's loom package

from loom.phantom import loom_running             # noqa: E402
from loom.run import _STAGE_ORDER, LearningsParseError, _parse_learnings   # noqa: E402
from loom.state import LoomState                  # noqa: E402

DEFAULT_REPO = Path.home() / "projects" / "ai-harness"
DEFAULT_BACKUP = Path.home() / ".local" / "state" / "loom" / "quarantine-resolved"
BM2_IDS = ("bm2-363556c5-bf8f-4ada-95ee-79e435d75d34", "bm2-c2b2fe92-aca1-4140-9f0f-105efcbec759")


class Refusal(Exception):
    """A preflight check failed. Nothing has been written."""


def _salvage_text(bm2_id: str) -> str:
    source = HERE / f"salvage-{bm2_id}.md"
    if not source.exists():
        raise Refusal(f"salvage source is missing: {source}")
    text = source.read_text(encoding="utf-8")
    try:
        if not _parse_learnings(text):
            raise Refusal(f"salvage source holds no learnings: {source}")
    except LearningsParseError as exc:
        raise Refusal(f"salvage source does not parse ({exc}): {source}") from exc
    return text


def _preflight(repo: Path) -> dict[str, str]:
    """Every check that can fail, before the first write. Returns {salvage id: text}."""
    state_path = repo / "loom" / "state.json"
    if not state_path.is_file():
        raise Refusal(f"no loom state.json under {repo}; point --repo at the live checkout")
    known = json.loads(state_path.read_text() or "{}")
    missing = [sid for sid in BM2_IDS if sid not in known]
    if missing:
        raise Refusal(f"{state_path} does not know {', '.join(missing)}; wrong data directory?")
    texts = {}
    for bm2_id in BM2_IDS:
        text = _salvage_text(bm2_id)
        existing = repo / "loom" / "learnings" / f"{bm2_id}-salvage.md"
        if existing.exists() and existing.read_text(encoding="utf-8") != text:
            raise Refusal(f"{existing} already exists and differs from the salvage source")
        texts[f"{bm2_id}-salvage"] = text
    return texts


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _settle(repo: Path, backup_dir: Path, texts: dict[str, str], log: Callable[[str], None]) -> None:
    loom = repo / "loom"
    state = LoomState(loom / "state.json")             # loom's own locked, atomic writer
    for new_id, text in texts.items():
        artifact = loom / "learnings" / f"{new_id}.md"
        if not artifact.exists():
            artifact.parent.mkdir(exist_ok=True)
            _write_atomic(artifact, text)
            log(f"wrote {artifact}")
        if _STAGE_ORDER[state.state_of(new_id)] < _STAGE_ORDER["distilled"]:
            state.advance(new_id, "distilled")
            log(f"state[{new_id}] -> distilled")
    for bm2_id in BM2_IDS:
        if state.state_of(bm2_id) != "committed":
            state.advance(bm2_id, "committed")
            log(f"state[{bm2_id}] -> committed (quarantine settled)")
        stale = loom / "quarantine" / f"{bm2_id}.md"
        if stale.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            kept = backup_dir / stale.name
            shutil.copy2(stale, kept)
            if kept.read_bytes() != stale.read_bytes():
                raise RuntimeError(f"backup of {stale} does not match; nothing deleted")
            stale.unlink()
            log(f"moved {stale} -> {kept}")


def apply(repo: Path = DEFAULT_REPO, *, backup_dir: Path = DEFAULT_BACKUP,
          running_check: Callable[[], bool] = loom_running, log: Callable[[str], None] = print) -> int:
    repo, backup_dir = Path(repo), Path(backup_dir)
    try:
        texts = _preflight(repo)
        if running_check():
            raise Refusal("a loom absorb/backfill/promote is running; retry later")
        with open(repo / "loom" / ".run.lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise Refusal("another loom run holds loom/.run.lock; retry later") from None
            try:
                _settle(repo, backup_dir, texts, log)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except Refusal as why:
        print(f"refusing: {why}", file=sys.stderr)
        return 1
    log("done")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO, help="checkout that holds the live loom/ data")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP)
    args = parser.parse_args(argv)
    return apply(args.repo, backup_dir=args.backup_dir)


if __name__ == "__main__":
    raise SystemExit(main())
