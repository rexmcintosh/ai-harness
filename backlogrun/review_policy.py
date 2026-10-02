"""How much review one worked branch gets, and which review findings send it back.

Pure apart from the optional price lookup. Three rules (backlog item
2026-09-27-backlog-run-review-policy):

1. Size the review to the risk. A branch whose every changed path is prose or data is
   `docs`; anything else, or anything unsure, is `code`. Code keeps the full council
   `code-review` panel. Docs get a light review: a read-only Claude session at night
   (22:00-07:00 UTC, when the owner's Claude plan is idle), otherwise one council seat.
2. Severity gate. Every round is a full review, but only a blocking or serious finding
   sends the item back. Minor points become follow-ups on the item.
3. Round limit. After ROUND_LIMIT review rounds the owner decides; `rework` refuses
   without --force.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath

ROUND_LIMIT = 3

# Night window in UTC hours: [NIGHT_START, 24) and [0, NIGHT_END).
NIGHT_START, NIGHT_END = 22, 7

REVIEWER_CODE = "council code-review"
REVIEWER_CLAUDE_DOCS = "Claude review (docs)"
REVIEWER_COUNCIL_DOCS = "council spec-review (one seat, docs)"

# Prose and data. Anything else is code, including files with no extension.
_DOC_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".adoc", ".csv", ".tsv"}
# YAML is data only inside a docs directory; elsewhere it is often config that runs.
_DOC_DATA_SUFFIXES = {".yaml", ".yml"}
# Prose that an agent or scheduler executes as instructions is code, whatever its suffix.
_EXECUTED_NAMES = {"claude.md", "agents.md", "gemini.md", "skill.md"}
_EXECUTED_NAME_PARTS = ("cron",)

SERIOUS_SEVERITIES = {"high", "critical"}
VERIFIED_DEFECTS = {"correctness", "security"}


def path_is_doc(path: str) -> bool:
    """True only for a path that is clearly prose or data and never executes."""
    if not isinstance(path, str) or not path.strip():
        return False
    p = PurePosixPath(path.strip())
    parts = [part.lower() for part in p.parts]
    name = parts[-1]
    # Dot directories (.github, .claude, .husky, ...) hold workflows, hooks and commands.
    if any(part.startswith(".") for part in parts):
        return False
    # Crontab text is code wherever it sits, so anything under a cron-named folder is too.
    if name in _EXECUTED_NAMES or any(word in part for part in parts for word in _EXECUTED_NAME_PARTS):
        return False
    suffix = p.suffix.lower()
    if suffix in _DOC_SUFFIXES:
        return True
    if suffix in _DOC_DATA_SUFFIXES:
        return "docs" in parts[:-1] or "doc" in parts[:-1]
    return False


def classify_paths(paths: list[str]) -> str:
    """'docs' when every changed path is prose/data, else 'code'. No paths is 'code'."""
    paths = [p for p in paths if isinstance(p, str) and p.strip()]
    if paths and all(path_is_doc(p) for p in paths):
        return "docs"
    return "code"


def is_night(now: datetime) -> bool:
    """22:00-07:00 UTC. `now` must be timezone-aware UTC."""
    return now.hour >= NIGHT_START or now.hour < NIGHT_END


def choose_reviewer(kind: str, now: datetime) -> tuple[str, str]:
    """(reviewer label, why) for a diff class at a UTC moment."""
    if kind != "docs":
        return REVIEWER_CODE, "the diff changes code or config (or its type is unsure)"
    if is_night(now):
        return REVIEWER_CLAUDE_DOCS, (f"docs-only diff at {now:%H:%M} UTC, inside the "
                                     f"{NIGHT_START:02d}:00-{NIGHT_END:02d}:00 UTC Claude window")
    return REVIEWER_COUNCIL_DOCS, (f"docs-only diff at {now:%H:%M} UTC, outside the "
                                   f"{NIGHT_START:02d}:00-{NIGHT_END:02d}:00 UTC Claude window")


def cheapest_seat(members: list, price=None):
    """The panel member whose model costs least per input+output token. Members without a
    known price rank last; ties keep panel order. `price(model)` -> {'input','output'} | None."""
    if not members:
        raise ValueError("panel has no members")
    if price is None:
        try:
            from venice_usage.pricing import price_row as price
        except Exception:  # noqa: BLE001 - no price table: keep panel order
            return members[0]

    def cost(member):
        row = price(member.model)
        try:
            return (0, float(row["input"]) + float(row["output"]))
        except (TypeError, KeyError, ValueError):
            return (1, 0.0)
    return min(members, key=cost)


# The chair or reviewer is asked for this next to required_changes. Kept here so the
# council and Claude paths ask for the same thing.
CLASSIFY_INSTRUCTION = (
    "Also return required_change_classes: a JSON list with exactly one object per entry of "
    "required_changes, in the same order, each {\"severity\": \"critical\"|\"high\"|\"medium\"|"
    "\"low\", \"verified_defect\": \"correctness\"|\"security\"|\"none\"}. Use verified_defect "
    "correctness or security only for a defect you verified against the supplied diff, not a "
    "possibility. Minor points (style, docs wording, nice-to-have tests) are medium or low.")


def split_findings(*, blocking: list[tuple[str, str]], required_changes: list[str] | None,
                   classes: object) -> tuple[list[str], list[str]]:
    """(serious, minor) from a chair's structured verdict.

    `blocking` holds the chair's (text, severity) blocking findings; every one is serious.
    A required change is serious when its class is high/critical or a verified correctness
    or security defect. When `classes` is missing or does not line up one-to-one with the
    required changes, every required change is serious: fail closed.
    """
    serious = [text for text, _severity in blocking]
    minor: list[str] = []
    changes = list(required_changes or [])
    aligned = (isinstance(classes, list) and len(classes) == len(changes)
               and all(isinstance(c, dict) for c in classes))
    for n, change in enumerate(changes):
        if not aligned:
            serious.append(change)
            continue
        cls = classes[n]
        severity = str(cls.get("severity") or "").strip().lower()
        defect = str(cls.get("verified_defect") or "").strip().lower()
        if severity not in SERIOUS_SEVERITIES | {"medium", "low"}:
            serious.append(change)        # unknown severity: fail closed
        elif severity in SERIOUS_SEVERITIES or defect in VERIFIED_DEFECTS:
            serious.append(change)
        else:
            minor.append(change)
    serious = list(dict.fromkeys(serious))
    minor = [m for m in dict.fromkeys(minor) if m not in serious]
    return serious, minor
