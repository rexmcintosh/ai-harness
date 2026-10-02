"""The label-and-score jobs around the council's reasoning, asked of Jev over SAVED reviews.

Offline only: nothing here is wired into `council review`, the gate or the backlog runner.
Each experiment builds one Request per item, sends it, and keeps the raw answers so a
reader can disagree with a score without re-running anything.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

from council import signals
from council.signals import VERDICTS, clip as _clip   # noqa: F401  (re-exported)

from . import jev
from .reviews import Review, SeatFinding

# The three questions that tested well (verdict, duplicate pair, block link) are wired into
# the council in shadow mode, so their validated wording lives once, in council/signals.py.
# The experiments that did not graduate keep their wording here.

FINDING_KINDS = {
    "concrete_defect": "Names a specific place in the code and a specific wrong behavior that happens "
                       "with the code as written.",
    "hypothetical_risk": "Describes something that could go wrong only under an unusual condition, a race, "
                         "a future change or misuse. Typical words: could, may, might, if.",
    "design_or_taste": "A preference about structure, naming, style or approach. No wrong behavior is claimed.",
    "missing_test_or_doc": "Says a test, a check or documentation is missing or weak.",
    "no_issue": "Says that something is fine, or only describes what the change does.",
}

SEVERITY_LEVELS = [
    "Cosmetic or informational. Nothing behaves wrongly.",
    "Minor. Wrong behavior only in a rare edge case, with little harm.",
    "Moderate. Wrong behavior that users or operators would really hit, with limited harm.",
    "Serious. Data loss, a security hole, or a broken main path.",
]


@dataclass
class Request:
    rid: str
    state: object
    questions: dict


# ── builders ─────────────────────────────────────────────────────────────────────────────
def verdict_request(r: Review) -> Request:
    return Request(r.rid, *signals.verdict_request(r.recommendation))


def finding_request(rid: str, f: SeatFinding) -> Request:
    return Request(f"{rid}#{f.fid}", {"finding": f.text}, {
        "kind": {"type": "choice", "criteria": FINDING_KINDS,
                 "instructions": "A code reviewer wrote this finding. What kind of finding is it, as worded?"},
        "would_break": {"type": "noul",
                        "instructions": "As worded, does this finding claim the code will give a wrong result, "
                                        "crash, lose data or open a security hole in normal use?"},
        "severity": {"type": "score", "criteria": SEVERITY_LEVELS,
                     "instructions": "How serious is the problem this finding describes, taking its claim at face value?"},
    })


def link_request(rid: str, block: dict, findings: list[SeatFinding]) -> Request:
    return Request(rid, *signals.link_request(block.get("point", ""), block.get("why", ""),
                                              {f.fid: f.text for f in findings}))


def cross_seat_pairs(r: Review):
    return [(a, b) for a, b in itertools.combinations(r.findings, 2) if a.seat != b.seat]


def duplicate_request(rid: str, a: SeatFinding, b: SeatFinding) -> Request:
    return Request(f"{rid}#{a.fid}~{b.fid}", *signals.duplicate_request(a.text, b.text))


def router_request(rid: str, head: str, panels: dict[str, str]) -> Request:
    return Request(rid, head, {"panel": {"type": "choice", "criteria": panels,
                                         "instructions": "Which review panel should handle this input?"}})


def tier_request(rid: str, paths: list[str], diff_head: str) -> Request:
    def noul(text):
        return {"type": "noul", "instructions": text}
    return Request(rid, {"changed_paths": paths, "diff": diff_head}, {
        "privileged": noul("Does this change touch code that can deploy, publish, send messages, push to a "
                           "remote, spend money, or change CI permissions?"),
        "security_sensitive": noul("Does this change touch authentication, authorization, secrets, tokens, "
                                   "or the handling of input from outside the team?"),
        "runtime_code": noul("Does this change alter code that runs in production or in an unattended job, "
                             "as opposed to only tests, documentation, comments or developer tooling?"),
    })


def slice_request(rid: str, claim: str, code: str) -> Request:
    return Request(rid, {"claim": claim, "code": code}, {
        "relation": {"type": "choice", "instructions": "How does the code in `code` relate to the reviewer's `claim`?",
                     "criteria": {"supports": "The code shown does what the claim says is wrong.",
                                  "contradicts": "The code shown handles the case, so the claim is wrong.",
                                  "says_nothing": "The code shown is not enough to tell either way."}}})


def build_requests(name: str, reviews: list[Review]) -> list[Request]:
    if name == "verdict":
        return [verdict_request(r) for r in reviews]
    if name == "finding_type":
        return [finding_request(r.rid, f) for r in reviews for f in r.findings]
    if name == "duplicates":
        return [duplicate_request(r.rid, a, b) for r in reviews for a, b in cross_seat_pairs(r)]
    raise ValueError(f"unknown experiment {name!r}")


# ── running and scoring ──────────────────────────────────────────────────────────────────
def run(name: str, reviews: list[Review] | None = None, *, key, transport=jev._http_post,
        dry_run: bool = False, requests: list[Request] | None = None, log=lambda *_: None):
    reqs = requests if requests is not None else build_requests(name, reviews or [])
    if dry_run:
        return {"experiment": name, "dry_run": True, "requests": len(reqs)}
    rows = []
    for n, req in enumerate(reqs, 1):
        try:
            out = jev.ask(req.state, req.questions, key=key, transport=transport)
            rows.append({"id": req.rid, **out})
        except jev.JevError as exc:
            rows.append({"id": req.rid, "error": str(exc)})
        if n % 25 == 0:
            log(f"  {name}: {n}/{len(reqs)}")
    return {"experiment": name, "model": jev.MODEL, "rows": rows,
            "input_tokens": sum(r.get("input_tokens", 0) for r in rows),
            "errors": sum(1 for r in rows if "error" in r)}


_OPENERS = (
    (r"^(do not|don't) (merge|run)|^request|^hold\b|^reject|^block|^rework", "request_changes"),
    (r"^approve (with|after|once|the \w+ (but|in principle))|^approve in principle|^conditional", "approve_with_conditions"),
    (r"^approve|^merge\b|^ship", "approve"),
)


def baseline_verdict(recommendation: str) -> str:
    """Today's cheap alternative: read the opening words. 'Hold for a fix, then merge' and
    other free wording fall through to unknown, which is what the morning report shows now."""
    head = recommendation.strip().lower()
    if head.startswith("hold for"):
        return "unknown"
    for pattern, label in _OPENERS:
        if re.search(pattern, head):
            return label
    return "unknown"


def score(rows: list[dict], field: str, truth: str = "truth") -> dict:
    judged = [r for r in rows if r.get(truth) is not None]
    misses = [r["id"] for r in judged if r.get(field) != r[truth]]
    return {"n": len(judged), "correct": len(judged) - len(misses), "misses": misses}
