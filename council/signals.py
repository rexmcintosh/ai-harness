"""Jev shadow signals: three small label-and-score jobs around a council review.

  1. verdict_label    What did the chair decide? One choice over the recommendation text.
  2. seat_agreement   Did two seats raise the same problem? One yes/no per cross-seat pair.
  3. block_sources    Which panel finding does a chair block confirm? One choice per block.

SHADOW MODE. Everything here is display and log only. Nothing it returns may change the
chair's input, the gate count, `review_status`, readiness, `approve` or an exit code. The
house rule for any later step: a Jev signal may inform or make things stricter; it must
never waive a block. Callers use `collect()` and nothing else; it runs AFTER the chair has
answered, never raises, honours a kill switch (COUNCIL_JEV=0) and a time budget, and
refuses repositories outside the owner's data-scope rule.

The question WORDING below was validated in docs/jev-council-offline-test-2026-09-19.md.
This model reads literally, so the wording is the tuning knob: a reworded question is a
different, untested question. `tools/jev_council/experiments.py` imports these builders,
so the wording exists once. Two jobs failed that test and are deliberately NOT here:
checking a finding against a code slice, and a "can it merge exactly as is?" yes/no.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import jev
from .gate import is_candidate, normalize_severity
from .models import MemberResult, Synthesis

CUT = 0.85                # "same problem" at or above this; read off the offline test, re-check on shadow data
MAX_PAIRS = 60
BUDGET_SECONDS = 20       # the whole pass; an outage must not stall a review
DEFAULT_LOG = "~/.local/state/council/jev-shadow.jsonl"
LOG_ENV = "COUNCIL_JEV_LOG"

VERDICTS = {
    "approve": "The chair says to merge now. Anything else it lists is optional, follow-up work, "
               "or explicitly not a blocker.",
    "approve_with_conditions": "The chair approves, but names specific fixes that should be made "
                               "before the merge or before the change is relied on.",
    "request_changes": "The chair does not approve the change as it stands. It asks for rework, or says "
                       "to hold, not merge, or not run it until named problems are fixed.",
}
NONE = "none"
_SEVERITY_RANK = {"critical": 4, "high": 3, "med": 2, "low": 1, "info": 0}


class BudgetSpent(Exception):
    """Raised by collect()'s ask wrapper instead of starting a call after the time budget.
    It stops a loop; it is not counted as an error."""


def clip(text: str, n: int = 320) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ── question builders: (state, questions). Validated wording; do not rephrase. ───────────
def verdict_request(recommendation: str):
    return {"recommendation": recommendation}, {
        "verdict": {"type": "choice", "criteria": VERDICTS,
                    "instructions": "This is the chair's written recommendation at the end of a code review. "
                                    "What is the chair's verdict on merging the change?"},
    }


def duplicate_request(a: str, b: str):
    return {"a": a, "b": b}, {
        "same": {"type": "noul",
                 "instructions": "Two reviewers wrote findings `a` and `b` about the same code change. "
                                 "Do they report the same underlying problem?",
                 "criteria": {"true": "Both point at the same defect or gap, even in different words or "
                                      "citing different lines of the same code.",
                              "false": "They are about different problems, even if they concern the same "
                                       "file or feature."}}}


def link_request(point: str, why: str, findings: dict[str, str]):
    """`findings` is {finding id: finding text}, in panel order."""
    options = {fid: clip(text) for fid, text in findings.items()}
    options[NONE] = "The chair's blocking problem is about something none of the listed findings raised."
    return {"block": {"point": point, "why": clip(why, 600)}}, {
        "source": {"type": "choice", "criteria": options,
                   "instructions": "A review chair confirmed the blocking problem in `block`. Which one of the "
                                   "panel findings in the options reports that same problem? Choose none when "
                                   "no listed finding raised it."}}


# ── finding ids ──────────────────────────────────────────────────────────────────────────
@dataclass
class NumberedFinding:
    fid: str            # F<seat number>.<finding number>, in the order the chair was shown them
    seat: str
    severity: str       # normalized: info | low | med | high | critical
    confidence: int
    text: str


def _severity_label(raw) -> str:
    """The gate's ladder, or "other". A seat can write anything into `severity`, and these
    labels go into the log and the shadow section, which must hold no free text."""
    sev = normalize_severity(str(raw))
    return sev if sev in _SEVERITY_RANK else "other"


def _whole_number(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def numbered_findings(results: list[MemberResult]) -> list[NumberedFinding]:
    """Ids come from panel order, never from prose, so one panel always yields the same ids.
    An errored seat has no findings; it still uses up its seat number."""
    return [NumberedFinding(f"F{n}.{k}", str(r.member), _severity_label(f.severity), _whole_number(f.confidence),
                            str(f.point))
            for n, r in enumerate(results, 1) if not r.error
            for k, f in enumerate(r.findings, 1)]


# ── answer readers: a wrong shape is an error, never a guess ─────────────────────────────
def _probability(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise ValueError(f"not a probability: {value!r}")
    return float(value)


def _choice(answers, name: str, allowed) -> tuple[str, float, dict]:
    answer = answers[name]
    label = answer["choice"]
    if label not in allowed:
        raise ValueError(f"{name}: answer {label!r} was not one of the options")
    probabilities = answer.get("probabilities")
    known = {k: v for k, v in (probabilities if isinstance(probabilities, dict) else {}).items()
             if k in allowed and isinstance(v, (int, float)) and not isinstance(v, bool)}
    return label, _probability(answer["confidence"]), known


def _count(stats, key: str, by: int = 1) -> None:
    if stats is not None:
        stats[key] = stats.get(key, 0) + by


# ── signal 1: the chair's verdict ────────────────────────────────────────────────────────
def verdict_label(recommendation: str, ask) -> dict:
    """{"label", "confidence", "probabilities"}. Raises when the call fails or the answer is
    not one of the three labels; collect() turns that into an error count."""
    state, questions = verdict_request(recommendation)
    label, confidence, probabilities = _choice(ask(state, questions), "verdict", VERDICTS)
    return {"label": label, "confidence": confidence, "probabilities": probabilities}


# ── signal 2: the same problem, raised by two seats ──────────────────────────────────────
def cross_seat_pairs(findings: list[NumberedFinding]):
    return [(a, b) for a, b in itertools.combinations(findings, 2) if a.seat != b.seat]


def _most_severe_first(pairs, max_pairs: int):
    """More pairs than the cap: keep the pairs whose two findings are the most severe. The
    sort is stable, so equally severe pairs stay in panel order."""
    ranked = sorted(pairs, key=lambda p: -(_SEVERITY_RANK.get(p[0].severity, 0) + _SEVERITY_RANK.get(p[1].severity, 0)))
    return ranked[:max_pairs]


def _score_pairs(pairs, ask, *, cut: float, stats) -> list[dict]:
    kept = []
    for a, b in pairs:
        try:
            state, questions = duplicate_request(a.text, b.text)
            p = _probability(ask(state, questions)["same"]["noul"])
        except BudgetSpent:
            break
        except Exception:  # noqa: BLE001 - one bad pair must not lose the others
            _count(stats, "errors")
            continue
        row = {"a": a.fid, "b": b.fid, "p": round(p, 4)}
        if stats is not None:
            stats.setdefault("scores", []).append(row)
        if p >= cut:
            kept.append(row)
    return kept


def seat_agreement(results: list[MemberResult], ask, *, cut: float = CUT, max_pairs: int = MAX_PAIRS,
                   stats: dict | None = None) -> list[dict]:
    """[{"a": "F1.1", "b": "F3.1", "p": 0.94}] for cross-seat pairs Jev scores at or above
    `cut`. Pass `stats` to learn the rest: every score asked (for re-checking the cut later),
    `pairs_total`, `pairs_asked`, `truncated` and `errors`."""
    pairs = cross_seat_pairs([f for f in numbered_findings(results) if f.text.strip()])
    chosen = _most_severe_first(pairs, max_pairs) if len(pairs) > max_pairs else pairs
    if stats is not None:
        stats.update(pairs_total=len(pairs), truncated=len(pairs) > max_pairs)
        stats.setdefault("scores", [])
        stats.setdefault("errors", 0)
    kept = _score_pairs(chosen, ask, cut=cut, stats=stats)
    if stats is not None:
        stats["pairs_asked"] = len(stats["scores"]) + stats["errors"]
    return kept


def clusters(pairs: list[dict], findings: list[NumberedFinding]) -> list[dict]:
    """Merge agreeing pairs into groups (union-find): F1.1~F3.1 and F3.1~F4.1 are one problem
    raised by three seats. Groups and their members come out in panel order."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for pair in pairs:
        parent[find(pair["a"])] = find(pair["b"])
    order = {f.fid: n for n, f in enumerate(findings)}
    seat_of = {f.fid: f.seat for f in findings}
    groups: dict[str, list[str]] = {}
    for fid in sorted(parent, key=lambda x: order.get(x, len(order))):
        groups.setdefault(find(fid), []).append(fid)
    out = []
    for members in groups.values():
        scores = [p["p"] for p in pairs if p["a"] in members]
        out.append({"findings": members, "seats": len({seat_of.get(m, m) for m in members}),
                    "p_min": min(scores), "p_max": max(scores)})
    return out

# An "inform the chair" mode would hook in HERE: run seat_agreement() between run_panel() and
# synthesize(), and hand the chair "raised by N seats" as a labelled fact in its digest.
# It is deliberately not built. It waits for shadow data (the log below) that shows the 0.85
# cut holds on reviews it has not seen. Until then the chair never sees a Jev answer.


# ── signal 3: which panel finding a chair block confirms ─────────────────────────────────
def block_sources(blocking_findings, results: list[MemberResult], ask, *, stats: dict | None = None) -> list[dict]:
    """[{"block": 0, "source": "F1.1" | "none", "confidence": 0.99}], one per chair block.
    The options are every panel finding plus `none`. With no panel finding there is nothing
    to choose from: the source is `none`, no call is made, and confidence is None."""
    findings = {f.fid: f.text for f in numbered_findings(results) if f.text.strip()}
    out = []
    for n, block in enumerate(blocking_findings or []):
        if not findings:
            out.append({"block": n, "source": NONE, "confidence": None})
            continue
        try:
            state, questions = link_request(str(block.point), str(block.why), findings)
            source, confidence, _ = _choice(ask(state, questions), "source", questions["source"]["criteria"])
        except BudgetSpent:
            break
        except Exception:  # noqa: BLE001
            _count(stats, "errors")
            continue
        out.append({"block": n, "source": source, "confidence": round(confidence, 4)})
    return out


# ── collect(): the only entry point callers use ──────────────────────────────────────────
@dataclass
class JevSignals:
    """What one shadow pass learned. Ids, labels and numbers only: no review text, so the
    whole object can be logged and carried into records."""
    repo: str
    model: str = jev.MODEL
    item: str = ""                                   # backlog item id, when the caller has one
    verdict: dict | None = None                      # {"label", "confidence", "probabilities"}
    seat_agreement: list[dict] = field(default_factory=list)     # pairs at or above the cut
    clusters: list[dict] = field(default_factory=list)
    pair_scores: list[dict] = field(default_factory=list)        # every pair asked, for re-checking the cut
    pairs_total: int = 0
    pairs_asked: int = 0
    truncated: bool = False                          # more pairs than the cap; the most severe were asked
    block_sources: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)           # id, seat, severity, confidence, eligible
    seats_answered: int = 0
    tier: str | None = None
    cut: float = CUT
    calls: int = 0
    seconds: float = 0.0
    errors: int = 0
    budget_exhausted: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def log_path_for(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    if environ.get(LOG_ENV):
        return Path(environ[LOG_ENV])
    home = environ.get("HOME")
    return (Path(home) if home else Path.home()) / DEFAULT_LOG[2:]


def _append_log(record: dict, path) -> None:
    """One JSON line. A log that cannot be written is ignored: the log serves the review,
    never the other way round."""
    try:
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _budgeted(ask, counter: dict, *, deadline: float, clock):
    """Count every call, and refuse to START one once the time budget is spent."""
    def wrapped(state, questions):
        if clock() >= deadline:
            counter["budget_exhausted"] = True
            raise BudgetSpent()
        counter["calls"] += 1
        return ask(state, questions)
    return wrapped


def _default_ask(environ):
    key = jev.load_key(environ=environ)
    return lambda state, questions: jev.ask(state, questions, key=key)


def collect(context_repo: str | None, results: list[MemberResult], synthesis: Synthesis, *,
            environ=None, ask=None, budget_seconds: float = BUDGET_SECONDS, log_path=None,
            name: str = "", tier: str | None = None, clock=time.monotonic) -> JevSignals | None:
    """Run the three signals for one finished review and append one line to the shadow log.

    Call it AFTER synthesize(), so the chair cannot be influenced. Returns None, and sends
    nothing, when the kill switch is off, there is no key, or the repository (or `name`, a
    backlog item id) is outside the data-scope rule; an unknown repository is refused.
    Never raises: a failed call or a wrongly shaped answer costs that one signal and adds to
    `errors`. `tier` is the gate's blast-radius tier when the input was a diff; it only
    labels a linked finding as eligible or not. The result is for display and the log."""
    sig = None
    try:
        environ = os.environ if environ is None else environ
        if not jev.shadow_enabled(environ) or not jev.in_scope(name, context_repo):
            return None
        findings = numbered_findings(results)
        sig = JevSignals(repo=str(context_repo), item=str(name or ""), tier=tier,
                         seats_answered=sum(1 for r in results if not r.error))
        eligible = {f.fid: (is_candidate(f.severity, f.confidence, tier=tier) if tier else None) for f in findings}
        sig.findings = [{"id": f.fid, "seat": f.seat, "severity": f.severity, "confidence": f.confidence,
                         "eligible": eligible[f.fid]} for f in findings]
        started = clock()
        counter = {"calls": 0, "budget_exhausted": False}
        budgeted = _budgeted(ask or _default_ask(environ), counter, deadline=started + budget_seconds, clock=clock)

        # Cheapest and most useful first: the time budget cuts from the end.
        try:
            if not synthesis.error and str(synthesis.recommendation or "").strip():
                sig.verdict = verdict_label(str(synthesis.recommendation), budgeted)
        except BudgetSpent:
            pass
        except Exception:  # noqa: BLE001
            sig.errors += 1
        try:
            stats: dict = {}
            links = block_sources(synthesis.blocking_findings, results, budgeted, stats=stats)
            sig.block_sources = [{**link, "eligible": eligible.get(link["source"])} for link in links]
            sig.errors += stats.get("errors", 0)
        except Exception:  # noqa: BLE001
            sig.errors += 1
        try:
            stats = {}
            sig.seat_agreement = seat_agreement(results, budgeted, stats=stats)
            sig.clusters = clusters(sig.seat_agreement, findings)
            sig.pair_scores = stats.get("scores", [])
            sig.pairs_total, sig.pairs_asked = stats.get("pairs_total", 0), stats.get("pairs_asked", 0)
            sig.truncated = bool(stats.get("truncated"))
            sig.errors += stats.get("errors", 0)
        except Exception:  # noqa: BLE001
            sig.errors += 1
        sig.calls, sig.budget_exhausted = counter["calls"], counter["budget_exhausted"]
        sig.seconds = round(clock() - started, 3)
    except Exception:  # noqa: BLE001 - shadow mode must never break the review it rides on
        if sig is not None:
            sig.errors += 1
    if sig is not None:
        _log_review(sig, synthesis, log_path or log_path_for(environ))
    return sig


def _log_review(sig: JevSignals, synthesis, path) -> None:
    """The comparison record: Jev's three answers next to the chair's own `review_status`
    and the gate-relevant facts. Ids, labels, numbers and one hash. NO review text."""
    try:
        recommendation = str(synthesis.recommendation or "")
        record = {"ts": _utc_now(), "kind": "review", **sig.to_dict(),
                  "chair_review_status": str(synthesis.review_status),
                  "chair_blocks": len(synthesis.blocking_findings or []),
                  "chair_error": bool(synthesis.error),
                  "recommendation_sha256": hashlib.sha256(recommendation.encode()).hexdigest()}
    except Exception:  # noqa: BLE001
        record = {"ts": _utc_now(), "kind": "review", **sig.to_dict()}
    _append_log(record, path)
