"""`council compare` — the selection half of "waste tokens, save time".

Generate N candidate solutions however you like (different models, parallel agent
runs, K shots of the same model), then have the panel rank them and the chair pick
a winner + say what to graft from the runners-up. The council owns *selection*;
candidate *generation* is the caller's job (see scripts/parallel-attempts.sh for a
self-contained example).
"""
from __future__ import annotations

import concurrent.futures

from .models import Member, Panel, CandidateVote, ComparisonResult
from .prompts import COMPARE_OUTPUT, COMPARE_SYNTH
from .jsonparse import loads_lenient


def _as_int(value, default: int = 5) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_candidates(candidates) -> str:
    return "\n\n".join(f"=== CANDIDATE {label} ===\n{text}" for label, text in candidates)


def _ask_voter(member: Member, context: str, client,
               max_completion_tokens=None) -> CandidateVote:
    try:
        raw = client.complete(member.model, member.system + "\n\n" + COMPARE_OUTPUT, context,
                              json_mode=member.json_mode,
                              max_completion_tokens=max_completion_tokens)
        d = loads_lenient(raw)
        if not isinstance(d, dict) or not str(d.get("pick") or "").strip():
            raise ValueError(f"no usable answer in the reply ({len(raw or '')} chars)")
        return CandidateVote(
            member=member.name, model=member.model,
            pick=str(d.get("pick", "")),
            ranking=[str(x) for x in d.get("ranking", [])],
            rationale=str(d.get("rationale", "")),
        )
    except Exception as e:  # noqa: BLE001
        return CandidateVote(member=member.name, model=member.model, pick="",
                             error=f"{type(e).__name__}: {e}")


def _synthesize_comparison(task, candidates, votes, client, *, chair_model,
                           max_completion_tokens=None) -> ComparisonResult:
    labels = [label for label, _ in candidates]
    digest = []
    for v in votes:
        if v.error:
            digest.append(f"### {v.member} ({v.model}) — errored, no vote")
            continue
        digest.append(f"### {v.member} ({v.model}) — pick: {v.pick}, ranking: {v.ranking}")
        digest.append(f"rationale: {v.rationale}")
    user = (f"TASK:\n{task}\n\nCANDIDATE LABELS: {labels}\n\n"
            f"PANELIST RANKINGS (independent, blind to each other):\n" + "\n".join(digest))
    try:
        d = loads_lenient(client.complete(chair_model, COMPARE_SYNTH, user,
                                          max_completion_tokens=max_completion_tokens))
        return ComparisonResult(
            winner=str(d.get("winner", "")),
            rationale=str(d.get("rationale", "")),
            ranking=[str(x) for x in d.get("ranking", [])],
            grafts=[str(x) for x in d.get("grafts", [])],
            confidence=_as_int(d.get("confidence", 5)),
            votes=votes,
        )
    except Exception as e:  # noqa: BLE001
        return ComparisonResult(winner="", confidence=0, votes=votes,
                                error=f"{type(e).__name__}: {e}")


def run_compare(task: str, candidates, panel: Panel, client, *,
                chair_model: str, max_workers=None, budget=None) -> ComparisonResult:
    """Rank `candidates` (a list of (label, text)) against `task` with `panel`,
    then synthesize a winner. Raises ValueError on fewer than two candidates —
    there is nothing to compare."""
    candidates = list(candidates)
    if len(candidates) < 2:
        raise ValueError("compare needs at least two candidates")
    context = f"TASK:\n{task}\n\n{_format_candidates(candidates)}"

    workers = max_workers or min(8, max(1, len(panel.members)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_ask_voter, m, context, client,
                               budget.member(m.name) if budget else None): m
                   for m in panel.members}
        votes = [f.result() for f in concurrent.futures.as_completed(futures)]
    order = [m.name for m in panel.members]
    votes.sort(key=lambda v: order.index(v.member))

    return _synthesize_comparison(task, candidates, votes, client, chair_model=chair_model,
                                  max_completion_tokens=budget.chair if budget else None)
