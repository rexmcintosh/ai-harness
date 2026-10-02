"""Merge-gate decision: which panel findings actually block a PR.

The v0.2.0 gate was a single-lens OR over raw panelist findings — any one seat's
`critical`/`high`-c>=8 failed the PR, the chair had no say, and nothing was
verified against the code (audit F1/F2/F4/F6). This module replaces that with:

  1. severity normalization        — "High"/"blocker" no longer slip the gate (F6)
  2. blast-radius tier             — dev tooling gates only on confident (c>=8) criticals/highs (F6)
  3. tier-aware candidate bar      — the panel must find something serious enough
  4. chair-arbitrated grounding    — only findings the chair confirms (given full
                                     file context) count; it can drop false
                                     positives but cannot manufacture a block (F1/F2/F4)
"""
from __future__ import annotations

import re

from .models import MemberResult, Synthesis

# Synonyms a model might emit, mapped to the canonical ladder info<low<med<high<critical.
_SEV_ALIASES = {
    "blocker": "critical", "crit": "critical", "critical": "critical", "fatal": "critical",
    "severe": "high", "major": "high", "high": "high",
    "moderate": "med", "medium": "med", "med": "med",
    "minor": "low", "low": "low",
    "informational": "info", "nit": "info", "info": "info",
}

# Path segments that mark a change as developer-tooling rather than production source.
_DEV_SEGMENTS = {"tools", "scripts", "test", "tests", "__tests__", ".github", "ci", "setup"}
# Segments that force the FULL gate regardless of where they live.
_HIGH_RISK_SEGMENTS = {"auth", "authz", "login", "session", "payment", "payments",
                       "billing", "security", "crypto", "secrets"}
_PRIVILEGED_TOOL_ACTIONS = {"deploy", "publish", "release", "send", "push", "upload", "provision"}


def _is_privileged_tool_path(path: str) -> bool:
    """Tooling that can change CI authority or write outside the checkout uses the full gate."""
    segs = [s.lower() for s in (path or "").split("/") if s]
    if len(segs) >= 2 and segs[:2] == [".github", "workflows"]:
        return True
    words = set(re.findall(r"[a-z0-9]+", segs[-1] if segs else ""))
    return bool(words & _PRIVILEGED_TOOL_ACTIONS)


def normalize_severity(s: str) -> str:
    """Lowercase/trim and map synonyms; empty -> 'info' (lowest). Anything
    unrecognized passes through lowercased so it simply won't match the gate."""
    key = (s or "").strip().lower()
    if not key:
        return "info"
    return _SEV_ALIASES.get(key, key)


def _is_dev_path(path: str) -> bool:
    segs = [s.lower() for s in (path or "").split("/") if s]
    if not segs:
        return False
    name = segs[-1]
    if any(s in _DEV_SEGMENTS for s in segs[:-1]) or segs[0] in _DEV_SEGMENTS:
        return True
    if ".config." in name or name.endswith((".config.js", ".config.mjs", ".config.ts")):
        return True
    if name.startswith("."):          # dotfile config: .eslintrc, .prettierrc, ...
        return True
    return False


def risk_tier(paths) -> str:
    """'full' for production source / high-risk areas; 'reduced' only when EVERY
    changed path is developer tooling. Unknown (no paths) -> 'full' (safe default)."""
    paths = [p for p in (paths or []) if p]
    if not paths:
        return "full"
    if any(seg in _HIGH_RISK_SEGMENTS
           for p in paths for seg in p.lower().split("/")):
        return "full"
    if any(_is_privileged_tool_path(p) for p in paths):
        return "full"
    return "reduced" if all(_is_dev_path(p) for p in paths) else "full"


def is_candidate(severity: str, confidence: int, *, tier: str) -> bool:
    """Does this finding clear the bar to be *eligible* to block, before grounding?
    full:    critical (any confidence) or high with confidence >= 8.
    reduced: critical or high, each with confidence >= 8. Dev tooling still never gates on
             a hedged finding, but a confident high is eligible (owner policy 2026-09,
             option b): the chair's grounding, not the tier, drops robustness false alarms."""
    sev = normalize_severity(severity)
    if tier == "reduced":
        return sev in ("critical", "high") and confidence >= 8
    return sev == "critical" or (sev == "high" and confidence >= 8)


def candidate_findings(results: list[MemberResult], *, tier: str):
    """[(member, finding)] for findings clearing the tier bar. Errored members are skipped."""
    return [(r.member, f)
            for r in results if not r.error
            for f in r.findings if is_candidate(f.severity, f.confidence, tier=tier)]


def decide_blocking(results: list[MemberResult], syn: Synthesis, *, tier: str) -> int:
    """The gate count. A finding blocks only if BOTH hold:
      - the panel surfaced at least one candidate at this tier (the chair arbitrates
        panel findings; it cannot originate a block), and
      - the chair confirmed findings as blocking (grounded against context — it can
        drop a false positive like "ROOT undefined" to zero).
    Returns the number of chair-confirmed blocking findings, or 0 if there are no
    candidates. Fail-closed on chair/panel outage is handled by the caller."""
    if not candidate_findings(results, tier=tier):
        return 0
    return len(syn.blocking_findings)
