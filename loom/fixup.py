# loom/fixup.py
"""One-time triage repairs (2026-08 audit). Idempotent and dry-run by default:

1. self_skipped   — pending transcripts loom generated itself -> committed (never distill).
2. recovered      — sessions settled as 'zero-learning' whose artifact actually parses
                    under the fence-stripping parser and whose learnings never reached
                    the ledger -> reset to distilled so the nightly weave picks them up.
                    (This is the class that silently lost 'Rex has a son named Cai'.)
3. unparseable    — settled sessions whose artifact still cannot be parsed -> artifact
                    moved to quarantine/, session quarantined, so `loom pending` shows it.
4. routes_cleared — cached routes for unsettled learnings targeting people/ dropped, so
                    they re-route with the identity roster; includes remapping away from
                    the deleted people/rex-family-cai.md.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from .discovery import find_pending, is_loom_generated, session_id_for
from .fingerprint import learning_id
from .ledger import WeaveLedger
from .run import Config, LearningsParseError, _parse_learnings
from .state import LoomState

_SETTLED = ("committed", "rejected", "quarantined")


def triage_fixup(cfg: Config, apply: bool = False) -> Dict[str, object]:
    if apply:
        # Same single-writer discipline as run-absorb.sh: never mutate state or
        # ledger while a nightly run holds the lock (and vice versa).
        import fcntl
        lock_path = cfg.loom_dir / ".run.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fh = open(lock_path, "w")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_fh.close()
            return {"apply": True, "error": "another loom run holds .run.lock; retry later"}
        try:
            return _triage_fixup(cfg, apply)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()
    return _triage_fixup(cfg, apply)


def _triage_fixup(cfg: Config, apply: bool) -> Dict[str, object]:
    state = LoomState(cfg.state_path)
    ledger = WeaveLedger(cfg.ledger_path)
    learnings_dir = cfg.loom_dir / "learnings"
    quarantine_dir = cfg.loom_dir / "quarantine"
    out = {"apply": apply, "self_skipped": 0, "self_source_skipped": 0,
           "recovered": [], "unparseable": [], "routes_cleared": 0}

    # 1. Self-generated transcripts still pending.
    for transcript in find_pending(cfg.projects_dir, state):
        if is_loom_generated(transcript):
            out["self_skipped"] += 1
            if apply:
                state.advance(session_id_for(transcript), "committed")

    def _source_is_self(sid: str) -> bool:
        src = next(Path(cfg.projects_dir).glob(f"*/{sid}.jsonl"), None)
        return src is not None and is_loom_generated(src)

    # 2 + 3. Sessions settled as committed with a lost or unreadable artifact.
    # Sessions whose SOURCE transcript is loom-generated are excluded: recovering
    # them would re-inject the meta learnings the self-skip exists to keep out.
    for art in sorted(learnings_dir.glob("*.md")) if learnings_dir.exists() else []:
        sid = art.stem
        if state.state_of(sid) != "committed":
            continue
        if _source_is_self(sid):
            out["self_source_skipped"] += 1
            continue
        try:
            items = _parse_learnings(art.read_text(encoding="utf-8"))
        except LearningsParseError:
            prefix = f"{sid}#"
            if not any(lid.startswith(prefix) for lid, _ in ledger.items()):
                out["unparseable"].append(sid)
                if apply:
                    quarantine_dir.mkdir(parents=True, exist_ok=True)
                    art.rename(quarantine_dir / art.name)
                    state.advance(sid, "quarantined")
            continue
        if not items:
            continue
        lids = [learning_id(sid, i) for i in range(len(items))]
        if all(ledger.status_of(lid) is None for lid in lids):
            # Parseable now, but none of its learnings ever entered the ledger:
            # the old parser read this artifact as empty and settled the session.
            out["recovered"].append(sid)
            if apply:
                state.advance(sid, "distilled")

    # 4. Stale people/ routes planned before the roster existed.
    for lid, e in ledger.items():
        if e.get("status") in _SETTLED:
            continue
        target = e.get("target", "") or ""
        if target.startswith("people/"):
            out["routes_cleared"] += 1
            if apply:
                ledger.clear_route(lid)

    out["recovered_count"] = len(out["recovered"])
    out["unparseable_count"] = len(out["unparseable"])
    return out
