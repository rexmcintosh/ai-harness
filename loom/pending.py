# loom/pending.py
"""What is waiting on Rex, in a shape a human can answer.

Two jobs:
  * cluster_blocked() — collapse quarantined learnings into DECISIONS. The same
    fact is routinely re-captured across sessions (one VPS onboarding rule showed
    up 6 times across 4 target articles), and showing six near-identical rows is
    exactly the noise that trains someone to ignore the surface. One fact = one
    decision, with every destination listed.
  * pending_summary() — the whole picture (articles landing + decisions needed)
    for the nightly briefing line and the review page.
"""
from __future__ import annotations

import heapq
import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from stat import S_ISREG
from typing import Dict, List

from .state import state_lock

logger = logging.getLogger(__name__)

# Words too common to carry meaning when comparing two learnings.
_STOP = set("the a an of to in is are and or for on with that this it be as by from "
            "must all other only when new during its into via".split())

# Two learnings whose meaningful words overlap this much are the same fact.
# Calibrated on live data: the 6 VPS-onboarding captures sit at 0.42-1.00 to each
# other, while the unrelated macOS learning never exceeds 0.20 to any of them.
SAME_FACT = 0.40


def _learning_body(text: str) -> str:
    m = re.search(r"learning:\s*>?\s*(.+?)(?:\n\s*(?:route|type|subject):|\Z)", text, re.S)
    return re.sub(r"\s*\n\s*", " ", (m.group(1) if m else text)).strip().strip('"')


def _subject(text: str) -> str:
    m = re.search(r"subject:\s*(.+)", text)
    return m.group(1).strip() if m else "learning"


def _signature(text: str) -> set:
    return {w for w in re.findall(r"[a-z_]{3,}", _learning_body(text).lower())
            if w not in _STOP}


def cluster_blocked(items: List[dict]) -> List[dict]:
    """Group quarantined learnings into distinct decisions, most-repeated first."""
    if not items:
        return []
    sigs = [_signature(i.get("text", "")) for i in items]
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(items)):                       # single-linkage union-find
        for j in range(i + 1, len(items)):
            union = sigs[i] | sigs[j]
            if union and len(sigs[i] & sigs[j]) / len(union) >= SAME_FACT:
                parent[find(i)] = find(j)

    groups: Dict[int, list] = {}
    for idx, item in enumerate(items):
        groups.setdefault(find(idx), []).append(item)

    out = []
    for members in groups.values():
        # Quote the fullest phrasing — the terse re-captures lose context.
        best = max(members, key=lambda m: len(_learning_body(m.get("text", ""))))
        out.append({
            "subject": _subject(best.get("text", "")),
            "body": _learning_body(best.get("text", "")),
            "targets": sorted({m.get("target", "") for m in members if m.get("target")}),
            "ids": sorted(m.get("id", "") for m in members),
            "n": len(members),
        })
    out.sort(key=lambda g: (-g["n"], g["subject"]))
    return out


def _learning_block(learnings_dir: Path, lid: str) -> str:
    """The verbatim text of one learning — what a human needs to judge it."""
    try:
        sid, n = lid.split("#")
        blocks = re.split(r"(?m)^- type:", (Path(learnings_dir) / f"{sid}.md")
                          .read_text(encoding="utf-8"))
        return ("- type:" + blocks[int(n) + 1]).strip()
    except (ValueError, OSError, IndexError):
        return ""


def _quarantined_sessions(loom_dir: Path, limit: int = 10) -> dict:
    """Summarize terminal session quarantines without reading transcript content."""
    loom_dir = Path(loom_dir)
    limit = max(0, int(limit))
    state_path = loom_dir / "state.json"
    degraded = False
    error = None

    with state_lock(state_path):
        try:
            state = json.loads(state_path.read_text() or "{}")
        except FileNotFoundError:
            state = {}
        except OSError as exc:
            error = f"could not read state.json: {exc}"
            logger.warning("%s", error)
            state = {}
            degraded = True
        except ValueError as exc:
            error = f"invalid state.json: {exc}"
            logger.warning("%s", error)
            state = {}
            degraded = True
        if not isinstance(state, dict):
            error = "invalid state.json: top-level value is not an object"
            logger.warning("%s", error)
            state = {}
            degraded = True

        quarantined = {}
        reasons = Counter()
        for sid, entry in state.items():
            if (not isinstance(sid, str) or not isinstance(entry, dict)
                    or entry.get("state") != "quarantined"):
                continue

            quarantined[sid] = entry
            detector = entry.get("quarantine_detector") or entry.get("detector") or "unknown"
            if not isinstance(detector, str) or not detector.strip():
                detector = "unknown"
            reasons[detector.strip()] += 1

        # Snapshot and stat the quarantine directory while holding the same read
        # lock as state.json. Only an exact <session_id>.jsonl name is canonical.
        transcript_stats = {}
        quarantine_dir = loom_dir / "quarantine"
        try:
            transcripts = list(quarantine_dir.iterdir())
            for transcript in transcripts:
                try:
                    stat = transcript.stat()
                except OSError:
                    continue
                if not S_ISREG(stat.st_mode):
                    continue

                name = transcript.name
                if not name.endswith(".jsonl"):
                    continue
                sid = name[:-len(".jsonl")]
                if sid in quarantined:
                    transcript_stats[sid] = stat
        except OSError:
            pass

    def quarantine_datetime(entry):
        value = entry.get("quarantined_at")
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)

    def session_summary(sid):
        stat = transcript_stats.get(sid)
        quarantined_at = quarantine_datetime(quarantined[sid])
        observed_at = quarantined_at or (
            datetime.fromtimestamp(stat.st_mtime, timezone.utc) if stat else None
        )
        return {
            "id": sid,
            "quarantined_on": observed_at.date().isoformat() if observed_at else None,
            "size_bytes": stat.st_size if stat else None,
            "_recency": observed_at.timestamp() if observed_at else 0,
        }

    recent_ranked = heapq.nlargest(
        limit,
        (session_summary(sid) for sid in quarantined),
        key=lambda item: (item["_recency"], item["id"]),
    )
    recent = [{k: v for k, v in item.items() if k != "_recency"}
              for item in recent_ranked]
    histogram = dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0])))
    payload = {
        "count": len(quarantined),
        "reasons": histogram,
        "recent": recent,
        "action": "Run `loom resolve <id>` to review and resolve one.",
        "degraded": degraded,
    }
    if error:
        payload["error"] = error
    return payload


def pending_summary(*, wiki_root, ledger_path, learnings_dir, loom_dir,
                    today=None, promote_target=None, quarantined_limit=10) -> dict:
    """Everything waiting on a human, in one payload. Shared by the briefing line
    and the review page so the two can never disagree about what's pending.

    `held` / `would_promote` describe the NEXT promote run (02:00 UTC), so the
    hold comparison uses next_promote_date(), not the calling day — a hold set
    at 07:00 targets tomorrow's run and must show as held all day. `today` is
    kept for caller compatibility but no longer drives the hold check;
    `promote_target` lets tests inject the target date."""
    from .autopromote import auto_promote_check, is_held, next_promote_date  # local: avoid import cycle
    from .ledger import WeaveLedger
    from .promote import _git

    del today                                                  # see docstring
    target = promote_target or next_promote_date()
    wiki_root = Path(wiki_root)
    check = auto_promote_check(wiki_root=wiki_root, loom_dir=loom_dir, today=target)

    added = set()
    try:
        for line in _git(wiki_root, "diff", "--name-status",
                         "master..loom-shadow").stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[0] == "A":
                added.add(parts[1])
    except Exception:                                          # noqa: BLE001
        pass

    articles = [{"file": f, "slug": Path(f).stem,
                 "dir": (f.split("/")[0] if "/" in f else "root"),
                 "new": f in added}
                for f in check["articles"]]

    try:
        led = WeaveLedger(ledger_path)
        blocked = [{"id": lid, "target": led.entry(lid).get("target", ""),
                    "reason": reason, "text": _learning_block(learnings_dir, lid)}
                   for lid, reason in led.quarantined()]
    except Exception:                                          # noqa: BLE001
        blocked = []

    return {
        "commits": check["commits"],
        "articles": articles,
        "new": sum(1 for a in articles if a["new"]),
        "updated": sum(1 for a in articles if not a["new"]),
        "decisions": cluster_blocked(blocked),
        "held": is_held(loom_dir, target),
        "staged_claude": check["staged"],
        "would_promote": check["go"],
        "quarantined_sessions": _quarantined_sessions(
            Path(loom_dir), limit=quarantined_limit),
    }


def briefing_line(payload: dict, url: str = "") -> str:
    """The single loom line for the 07:00 briefing — or "" to stay silent.

    Composed in code, never by the briefing model: these are counts a person acts
    on, and a paraphrase ("a bunch of articles") would be worse than useless.
    Returns at most two short lines to respect the briefing's phone-glanceable
    six-line budget.
    """
    promo = payload.get("promoted") or {}
    landed = promo.get("articles") or []
    decisions = payload.get("decisions") or []
    staged = payload.get("staged_claude") or []
    quarantined = payload.get("quarantined_sessions") or {}
    quarantined_count = quarantined.get("count", 0)
    quarantine_degraded = quarantined.get("degraded", False)
    held = payload.get("held")

    n = len(landed)
    noun = "article" if n == 1 else "articles"

    if held:
        head = f"held — {n} {noun} waiting · reply GO to promote tonight"
    elif staged:
        head = f"{n} {noun} waiting — a memory/skill change needs you first"
    elif promo.get("promoted") and n:
        head = f"{n} {noun} landed in your wiki"
    elif decisions or quarantined_count or quarantine_degraded:
        head = "nothing landed"
    else:
        return ""                      # nothing happened and nothing is asked

    parts = [f"🧵 {head}"]
    if decisions:
        names = " · ".join(d.get("subject", "?") for d in decisions[:3])
        more = f" +{len(decisions) - 3}" if len(decisions) > 3 else ""
        parts.append(f"   {len(decisions)} need your call: {names}{more}")
    if quarantine_degraded:
        parts.append(
            f"   quarantine summary degraded: {quarantined.get('error', 'unknown error')}")
    elif quarantined_count:
        noun = "session" if quarantined_count == 1 else "sessions"
        parts.append(f"   {quarantined_count} quarantined {noun} need review")
    if url:
        parts.append(f"   {url}")
    return "\n".join(parts)
