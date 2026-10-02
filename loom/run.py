# loom/run.py
"""Loom orchestrator. v0 distill (gate -> spool -> distill -> learnings artifact)
plus v1 weave (route -> group/cap -> weave -> commit on loom-shadow). Shadow mode
keeps v0 behavior; live mode runs the weave."""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import yaml

from .backends import get_backend
from .coverage import is_learning_covered
from .discovery import find_pending, is_loom_generated, session_id_for
from .fingerprint import learning_id
from .gate import scan_clean
from .gitio import ShadowRepo
from .indexer import rebuild_backlinks, upsert_index_entry
from .ledger import WeaveLedger
from .route import confirm_route, normalize_target
from .spool import spool_copy
from .state import LoomState
from .transcript import extract_text
from .weave import weave_target
from . import llm  # noqa: F401  (kept for monkeypatch compatibility in tests)

_PROMPTS = Path(__file__).parent / "prompts"
_STAGE_ORDER = {"pending": 0, "distilled": 1, "weaved": 2, "committed": 3, "quarantined": 9}


@dataclass
class Config:
    projects_dir: Path
    loom_dir: Path
    state_path: Path
    wiki_worktree: Optional[Path] = None  # loom-shadow worktree; weave commits land here
    wiki_master: Optional[Path] = None    # master worktree; promote merges/commits here
    claude_dir: Optional[Path] = None  # claude_dir: used by promote (CLI), not by absorb/weave
    ledger_path: Optional[Path] = None


def _distill_prompt(text: str, roster: str = "") -> str:
    return (_PROMPTS / "distill.md").read_text(encoding="utf-8") \
        .replace("{{ROSTER}}", roster or "(none)") \
        .replace("{{TRANSCRIPT}}", text)


def _roster_text(cfg: "Config") -> str:
    """The identity roster (`_roster.md` at the wiki root): who the recurring people
    are and their canonical articles. Human-edited (Obsidian); absent is valid."""
    for root in (cfg.wiki_master, cfg.wiki_worktree):
        if root:
            p = Path(root) / "_roster.md"
            if p.exists():
                return p.read_text(encoding="utf-8").strip()
    return ""


class LearningsParseError(ValueError):
    """Nonempty distill output that yields no usable learnings (fenced beyond
    repair, prose-wrapped, invalid YAML). Must be surfaced, never treated as a
    zero-learning session: that silently discarded real facts for weeks — the
    2026-08 case lost 'Rex has a son named Cai' behind a ```yaml fence."""


_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n(.*?)\n?\s*```\s*$", re.S)

# A learning deferred this many times by weave exceptions is dead-lettered
# (quarantined for human review) instead of re-queued: four poisoned learnings
# each reached 261 deferrals by 2026-08-16, hogging the nightly target slots.
_DEAD_LETTER_DEFERRALS = 5

# Life-first weave order: targets in these directories claim max_targets slots
# before any ops target. By 2026-08 only 6.9% of woven learnings landed in these
# dirs — floods of tooling gotchas kept deferring the rare person/place facts
# past the nightly caps, which is backwards for a personal wiki.
_SOFT_DIRS = {"people", "relationships", "places", "companies", "philosophies"}


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.match(text)
    return m.group(1) if m else text


def _parse_learnings(artifact_text: str) -> List[dict]:
    """Parse a distill artifact. Genuinely empty output (blank, `[]`, `null`) is a
    legitimate zero-learning result and returns []. Anything else that yields no
    usable learning raises LearningsParseError."""
    text = _strip_fences(artifact_text.strip())
    if not text.strip():
        return []
    try:
        data = yaml.safe_load(text)
    except Exception as exc:
        raise LearningsParseError(f"invalid YAML: {exc}") from exc
    if data is None or data == []:
        return []
    items = [d for d in data if isinstance(d, dict) and d.get("learning")] \
        if isinstance(data, list) else []
    if not items:
        raise LearningsParseError("nonempty distill output has no usable learnings")
    return items


def _index_listing(wiki: Path) -> str:
    idx = wiki / "_index.md"
    return idx.read_text(encoding="utf-8") if idx.exists() else ""


def absorb(cfg: Config, shadow: bool = True, backend: str = "claude",
           max_targets: int = 10, today: str = "", deadline_seconds=None,
           max_per_target: int = 4, distill: bool = True,
           weave_reserve: float = 0.4, max_distill: Optional[int] = None) -> Dict[str, object]:
    state = LoomState(cfg.state_path)
    learnings_dir = cfg.loom_dir / "learnings"
    spool_dir = cfg.loom_dir / "spool"
    quarantine_dir = cfg.loom_dir / "quarantine"
    # "quarantined" = SESSIONS whose transcript failed the secret gate.
    # "quarantined_learnings" = individual learnings whose weave failed a guard.
    summary = {"distilled": 0, "quarantined": 0, "failed": 0, "self_skipped": 0,
               "committed": 0, "skipped_covered": 0, "deferred": 0,
               "quarantined_learnings": 0, "deadline_hit": False,
               "distill_deadline_hit": False, "limit_hit": False}

    start = time.monotonic()
    def _past(limit) -> bool:
        return limit is not None and (time.monotonic() - start) > limit

    def _expired() -> bool:
        return _past(deadline_seconds)

    # Distill gets only part of the budget when a weave follows it. Between 2026-07-11
    # and 07-23 every nightly `absorb --live` reported deadline_hit=True, committed=0 and
    # a deferred count growing 176 -> 1152: distilling ~220 sessions ate the entire 3600s,
    # so _weave_all began already expired and deferred every target. Thirteen nights woven
    # nothing, silently — the run still exited 0. In shadow mode no weave follows, so
    # reserving anything there would only waste budget.
    distill_deadline = deadline_seconds
    if deadline_seconds is not None and not shadow and weave_reserve:
        distill_deadline = deadline_seconds * (1.0 - weave_reserve)

    # ---------- Stage 1: distill (v0) ----------
    # `distill=False` (used by `backfill`) weaves the already-distilled backlog only — it never
    # tries to distill new pending sessions (the nightly `absorb` on the Claude backend does that).
    be = get_backend(backend)
    roster = _roster_text(cfg)
    if distill:
        for transcript in find_pending(cfg.projects_dir, state):
            if max_distill is not None and summary["distilled"] >= max_distill:
                # Per-run cap for drain jobs: the diem harness hard-kills at its
                # deadline, so each backfill chunk must be bounded, not open-ended.
                break
            if _past(distill_deadline):
                summary["deadline_hit"] = True
                summary["distill_deadline_hit"] = True
                break
            sid = session_id_for(transcript)
            if _STAGE_ORDER[state.state_of(sid)] >= _STAGE_ORDER["distilled"]:
                continue
            if is_loom_generated(transcript):
                # Loom's own `claude -p` calls persist transcripts under the same
                # projects dir. Distilling them feeds the pipeline its own output —
                # that loop is how the wiki filled with loom-about-loom articles.
                state.advance(sid, "committed")
                summary["self_skipped"] += 1
                continue
            if not scan_clean(transcript):
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(transcript, quarantine_dir / transcript.name)
                state.advance(sid, "quarantined")
                summary["quarantined"] += 1
                continue
            spool_copy(transcript, spool_dir)
            try:
                text = extract_text(transcript)
                learnings, parse_ok = "", False
                for attempt in (1, 2):
                    learnings = be.complete("distill", "Extract durable learnings.",
                                            _distill_prompt(text, roster))
                    try:
                        _parse_learnings(learnings)
                        parse_ok = True
                        break
                    except LearningsParseError:
                        logging.warning("distill output unparseable for %s (attempt %d)",
                                        transcript.name, attempt)
            except llm.UsageLimitError:
                summary["limit_hit"] = True
                break
            except Exception:
                logging.exception("distill failed for %s", transcript)
                summary["failed"] += 1
                continue
            learnings_dir.mkdir(parents=True, exist_ok=True)
            artifact = learnings_dir / f"{sid}.md"
            tmp_artifact = learnings_dir / f"{sid}.tmp"
            tmp_artifact.write_text(learnings + "\n", encoding="utf-8")
            if not scan_clean(tmp_artifact):
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(tmp_artifact), str(quarantine_dir / f"{sid}.md"))
                state.advance(sid, "quarantined")
                summary["quarantined"] += 1
                continue
            if not parse_ok:
                # Never settle malformed output as a zero-learning session.
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(tmp_artifact), str(quarantine_dir / f"{sid}.md"))
                state.advance(sid, "quarantined")
                summary["quarantined"] += 1
                logging.error("distill output unparseable after retry; quarantined %s", sid)
                continue
            tmp_artifact.rename(artifact)
            state.advance(sid, "distilled")
            summary["distilled"] += 1

    if summary["limit_hit"]:
        return summary
    if shadow:
        return summary

    # ---------- Stage 2: weave (v1) ----------
    try:
        _weave_all(cfg, state, backend, max_targets, max_per_target, today, summary, _expired)
    except llm.UsageLimitError:
        summary["limit_hit"] = True
    return summary


def _weave_all(cfg, state, backend_name, max_targets, max_per_target, today, summary,
               expired: Callable[[], bool]):
    summary.setdefault("skipped_covered", 0)
    repo = ShadowRepo(cfg.wiki_worktree, base="master")
    ledger = WeaveLedger(cfg.ledger_path)
    ledger.reconcile_from_git(repo.committed_ids())          # git is authoritative
    be = get_backend(backend_name)
    roster = _roster_text(cfg)
    index_listing = _index_listing(cfg.wiki_worktree)
    learnings_dir = cfg.loom_dir / "learnings"

    sessions = [sid for sid in _sessions_at_least(state, "distilled")
                if _STAGE_ORDER[state.state_of(sid)] < _STAGE_ORDER["committed"]]
    sessions.sort(key=lambda s: (learnings_dir / f"{s}.md").stat().st_mtime
                  if (learnings_dir / f"{s}.md").exists() else 0)

    buckets: Dict[str, List[dict]] = {}
    dirs: Dict[str, str] = {}
    session_learnings: Dict[str, List[str]] = {}
    for sid in sessions:
        if expired():
            # Routing burns model calls too; without this check a long routing
            # phase could run past the deadline before the first weave.
            summary["deadline_hit"] = True
            break
        art = learnings_dir / f"{sid}.md"
        if not art.exists():
            state.advance(sid, "committed")                 # zero-learning session
            continue
        try:
            items = _parse_learnings(art.read_text(encoding="utf-8"))
        except LearningsParseError:
            # Legacy malformed artifact (pre-validation era). Surface it — the old
            # behavior settled these as zero-learning sessions and lost the facts.
            quarantine_dir = cfg.loom_dir / "quarantine"
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(art), str(quarantine_dir / art.name))
            state.advance(sid, "quarantined")
            summary["quarantined"] += 1
            logging.error("unparseable learnings artifact quarantined: %s", art.name)
            continue
        if not items:
            state.advance(sid, "committed")
            continue
        ids_here = []
        for idx, learning in enumerate(items):
            lid = learning_id(sid, idx)
            ids_here.append(lid)
            if ledger.status_of(lid) in ("committed", "rejected", "quarantined"):
                continue
            cached = ledger.entry(lid)
            # Routed in a prior run — reuse without a model call, but re-validate
            # first: entries planned before this guard existed (or poisoned by a
            # bad run) must not bypass normalize_target on the reuse path.
            cached_target = normalize_target(cached.get("target"), cfg.wiki_worktree)
            if cached_target:
                route = {"target": cached_target, "action": cached.get("action", "update")}
            else:
                if cached.get("target"):
                    logging.warning("route: refused stale cached target %r for %s",
                                    cached["target"], lid)
                route = confirm_route(be, learning, index_listing,
                                      wiki_root=cfg.wiki_worktree, roster=roster)
                if not route:
                    ledger.defer(lid, "unroutable")
                    continue
                ledger.plan(lid, route["target"], route["action"])
            entry = dict(learning)
            entry.update(id=lid, target=route["target"], action=route["action"],
                         directory=route["target"].split("/", 1)[0])
            buckets.setdefault(route["target"], []).append(entry)
            dirs[route["target"]] = entry["directory"]
        session_learnings[sid] = ids_here

    targets = list(buckets.keys())
    # Soft dirs first, then a deterministic daily rotation within each class.
    # Without the rotation the same mtime-ordered head owned every one of the
    # max_targets slots night after night while the tail starved.
    targets.sort(key=lambda t: (0 if dirs[t] in _SOFT_DIRS else 1,
                                hashlib.sha1(f"{today}:{t}".encode()).hexdigest()))
    for target in targets[:max_targets]:
        # Cap learnings woven into ONE target per run: keeps each weave a small, reviewable
        # diff and stops a popular pre-existing article from triggering a bisect/cost storm.
        # The overflow is deferred and drains over subsequent runs.
        weave_now = buckets[target][:max_per_target]
        for entry in buckets[target][max_per_target:]:
            ledger.defer(entry["id"], "per-target cap")
            summary["deferred"] += 1
        if expired():
            summary["deadline_hit"] = True
            for entry in weave_now:
                ledger.defer(entry["id"], "run deadline")
                summary["deferred"] += 1
            continue

        fresh = []
        article = None
        coverage_deadline_hit = False
        for position, entry in enumerate(weave_now):
            if entry["action"] == "update":
                if expired():
                    summary["deadline_hit"] = True
                    for pending in fresh + weave_now[position:]:
                        ledger.defer(pending["id"], "run deadline")
                        summary["deferred"] += 1
                    coverage_deadline_hit = True
                    break
                if article is None:
                    article = repo.read(target) or ""
                if is_learning_covered(be, entry["learning"], article):
                    ledger.mark(entry["id"], "committed", reason="already-covered")
                    summary["skipped_covered"] += 1
                    continue
            fresh.append(entry)
        if coverage_deadline_hit:
            continue
        weave_now = fresh
        if not weave_now:
            continue
        if expired():
            summary["deadline_hit"] = True
            for entry in weave_now:
                ledger.defer(entry["id"], "run deadline")
                summary["deferred"] += 1
            continue

        try:
            res = weave_target(be, repo, ledger, target, dirs[target], weave_now,
                               today=today, roster=roster)
        except llm.UsageLimitError:
            raise
        except Exception:
            logging.exception("weave_target failed for %s", target)
            for entry in weave_now:
                if ledger.entry(entry["id"]).get("deferrals", 0) >= _DEAD_LETTER_DEFERRALS:
                    # Dead-letter: a learning that keeps blowing up must stop
                    # re-occupying a nightly slot; surface it for a human instead.
                    ledger.quarantine(entry["id"], "dead-letter: repeated weave exceptions")
                    summary["quarantined_learnings"] += 1
                else:
                    ledger.defer(entry["id"], "weave exception")
                    summary["deferred"] += 1
            continue
        summary["committed"] += len(res["committed"])
        summary["quarantined_learnings"] += len(res["quarantined"])
        if res["committed"]:
            slug = Path(target).stem
            committed_set = set(res["committed"])
            first = next((b for b in weave_now if b["id"] in committed_set), weave_now[0])
            upsert_index_entry(cfg.wiki_worktree, slug, dirs[target], first["learning"], today=today)
    for target in targets[max_targets:]:
        for entry in buckets[target]:
            ledger.defer(entry["id"], "per-run cap")
            summary["deferred"] += 1

    rebuild_backlinks(cfg.wiki_worktree)
    repo.commit_paths(["_index.md", "_backlinks.json"], "index: rebuild _index/_backlinks")

    for sid, ids in session_learnings.items():
        statuses = [ledger.status_of(i) for i in ids]
        if all(s in ("committed", "rejected", "quarantined") for s in statuses):
            state.advance(sid, "committed")
        # 'weaved' is reachable only if a future change separates write from commit; today commit_file is atomic so learnings go planned->committed directly.
        elif all(s in ("committed", "rejected", "quarantined", "woven") for s in statuses):
            state.advance(sid, "weaved")
        # else stays distilled

    # Surface both: legacy permanent rejects (pre-quarantine) and the new
    # quarantined learnings awaiting review.
    summary["quarantined_items"] = ledger.quarantined() + ledger.rejected()
    summary["shadow_commits"] = repo.commits_since()
    oldest = repo.oldest_unpromoted_epoch()
    summary["oldest_age_days"] = int((time.time() - oldest) / 86400) if oldest else 0


def _sessions_at_least(state: LoomState, floor: str) -> List[str]:
    return [sid for sid, e in state._data.items()
            if _STAGE_ORDER.get(e.get("state", "pending"), 0) >= _STAGE_ORDER[floor]]
