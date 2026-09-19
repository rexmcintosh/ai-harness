from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Member:
    name: str
    model: str
    system: str
    # Optional per-seat output ceiling. A seat whose model reasons long (today
    # openai-gpt-53-codex, 17,142 completion tokens at its ledger maximum against
    # grok-4-3's 2,187) can be given its own headroom without raising the panel's.
    max_completion_tokens: int | None = None


@dataclass
class Panel:
    name: str
    description: str
    members: list[Member]
    default_rigor: str = "daily"  # "daily" | "deep"
    max_completion_tokens: int | None = None
    chair_max_completion_tokens: int | None = None
    # {"daily": {...}, "deep": {...}} — per-rigor overrides of the two above.
    rigor: dict = field(default_factory=dict)


@dataclass
class Finding:
    point: str
    severity: str  # info | low | med | high | critical
    confidence: int  # 1..10


@dataclass
class MemberResult:
    member: str
    model: str
    stance: str  # approve | concerns | oppose | na
    headline: str
    findings: list[Finding] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class Disagreement:
    topic: str
    type: str  # mechanical | taste | user-challenge
    positions: str
    resolution: str = ""
    what_we_might_miss: str = ""
    if_wrong_cost: str = ""


@dataclass
class SweepFinding:
    point: str
    severity: str
    confidence: int
    locations: list[str] = field(default_factory=list)  # chunk labels it appeared in
    sources: list[str] = field(default_factory=list)  # panel members that raised it


@dataclass
class SweepReport:
    findings: list["SweepFinding"] = field(default_factory=list)
    chunks_scanned: int = 0
    dropped: int = 0  # files beyond max_chunks (surfaced, never silent)
    summary: str = ""
    error: str | None = None


@dataclass
class CandidateVote:
    member: str
    model: str
    pick: str  # label of the preferred candidate
    ranking: list[str] = field(default_factory=list)  # labels best -> worst
    rationale: str = ""
    error: str | None = None


@dataclass
class ComparisonResult:
    winner: str
    rationale: str = ""
    ranking: list[str] = field(default_factory=list)  # chair's overall order
    grafts: list[str] = field(default_factory=list)  # best ideas from runners-up
    confidence: int = 5
    votes: list["CandidateVote"] = field(default_factory=list)
    error: str | None = None


@dataclass
class ConfirmedBlock:
    """A finding the chair has verified against context and judged worth blocking
    the merge. The gate counts these — not raw panelist findings (see audit F1/F2/F4)."""
    point: str
    severity: str = ""
    why: str = ""


@dataclass
class Synthesis:
    recommendation: str
    confidence: int
    consensus: list[str] = field(default_factory=list)
    disagreements: list[Disagreement] = field(default_factory=list)
    cross_panel_themes: list[str] = field(default_factory=list)
    blocking_findings: list[ConfirmedBlock] = field(default_factory=list)
    error: str | None = None
    # Optional explicit verdict used by backlog-run; prose never implies readiness.
    review_status: str = "unknown"
    required_changes: list[str] | None = None
    raw_response: str = ""
