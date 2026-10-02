# loom/weave.py
"""Weave one target file's bundle of learnings. The SCRIPT does all I/O and all
fingerprinting; the model only returns prose. Flow: dedup -> model -> stamp
fingerprints -> scoped shape-lints + all-routes sentinel -> bisect-on-fail ->
commit (trailer + empty-diff skip). Returns {'committed': [...], 'quarantined': [...]}.

A guard failure QUARANTINES rather than discards: the weave must not reach
loom-shadow unreviewed, but the learning is kept and surfaced for a human
decision. Quarantine is settled, so a deterministic guard (e.g. the sentinel's
priv-write pattern matching legitimate `authorized_keys` documentation) cannot
re-trip every run and burn DIEM forever."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from . import sentinel
from .fingerprint import markers_in, with_markers, strip_markers
from .weave_lint import is_trailing_append, is_excessive_rewrite

_PROMPTS = Path(__file__).parent / "prompts"
_SHAPE_LINTED_DIRS = {"people", "places", "companies", "projects", "eras",
                      "transitions", "philosophies", "patterns", "relationships",
                      "memory"}   # facts; NOT decisions/ (append) or MEMORY.md (index)


def _shape_linted(directory: str, target: str) -> bool:
    if Path(target).name == "MEMORY.md":
        return False
    return directory in _SHAPE_LINTED_DIRS


def _weave_prompt(target: str, article: str, bundle: List[dict], roster: str = "") -> str:
    learnings = "\n".join(f"- ({b['type']}) {b['subject']}: {b['learning']}" for b in bundle)
    return (_PROMPTS / "weave.md").read_text() \
        .replace("{{LEARNINGS}}", learnings) \
        .replace("{{ROSTER}}", roster or "(none)") \
        .replace("{{TARGET}}", target) \
        .replace("{{ARTICLE}}", article or "(new article — none yet)")


def _passes_guards(directory: str, target: str, before: str, after: str) -> bool:
    if not sentinel.is_clean(after):                 # all routes
        return False
    if _shape_linted(directory, target):
        if is_trailing_append(before, after):
            return False
        if is_excessive_rewrite(before, after):
            return False
    return True


def _try_bundle(backend, before: str, directory: str, target: str, bundle: List[dict],
                retry: bool = True, roster: str = ""):
    """Return revised text if it passes guards, else None."""
    prompt = _weave_prompt(target, before, bundle, roster)
    sys = "You are a careful wiki writer. Output only the full revised article."
    after = backend.complete("weave", sys, prompt)
    if _passes_guards(directory, target, before, after):
        return after
    if retry:
        stronger = sys + " Integrate; do not restructure, append event-logs, or include shell/command text."
        after = backend.complete("weave", stronger, prompt)
        if _passes_guards(directory, target, before, after):
            return after
    return None


def weave_target(backend, repo, ledger, target: str, directory: str,
                 bundle: List[dict], today: str, roster: str = "") -> Dict[str, List[str]]:
    # `today` is accepted for caller-signature parity (run.py); index date-stamping lives in run.py.
    result = {"committed": [], "quarantined": []}
    before = repo.read(target) or ""
    present = markers_in(before) | repo.committed_ids()

    # Dedup: drop learnings already woven into this target / committed anywhere.
    fresh = [b for b in bundle if b["id"] not in present]
    for b in bundle:
        if b["id"] in present and ledger.status_of(b["id"]) not in ("rejected", "quarantined"):
            ledger.mark(b["id"], "committed")
            result["committed"].append(b["id"])
    if not fresh:
        return result

    committed, quarantined = _weave_recursive(backend, repo, ledger, target, directory, before, fresh,
                                              roster=roster)
    result["committed"].extend(committed)
    result["quarantined"].extend(quarantined)
    return result


def _weave_recursive(backend, repo, ledger, target, directory, before, bundle, roster=""):
    """Weave a bundle; on guard failure bisect down to the offender(s)."""
    after = _try_bundle(backend, before, directory, target, bundle, roster=roster)
    if after is not None:
        ids = [b["id"] for b in bundle]
        authoritative = markers_in(before) | set(ids)
        stamped = with_markers(strip_markers(after), authoritative)
        sha = repo.commit_file(target, stamped, ids, f"weave: {target}")
        for b in bundle:
            ledger.mark(b["id"], "committed", commit_sha=sha)
        return ids, []
    if len(bundle) == 1:
        ledger.quarantine(bundle[0]["id"], "weave failed guards after retry")
        return [], [bundle[0]["id"]]
    mid = len(bundle) // 2
    # Re-read `before` fresh each half: the first half may have committed.
    c1, r1 = _weave_recursive(backend, repo, ledger, target, directory,
                              repo.read(target) or "", bundle[:mid], roster=roster)
    c2, r2 = _weave_recursive(backend, repo, ledger, target, directory,
                              repo.read(target) or "", bundle[mid:], roster=roster)
    return c1 + c2, r1 + r2
