#!/usr/bin/env python3
"""Grade a chair bake-off evidence file against the design's rubric.

`docs/chair-bakeoff-design-2026-09-11.md` §4. Every score is a string match or a
structural check on the recorded JSON, and every match prints the substring it
matched, so a reader can disagree with a score without re-running anything.

Two deliberate departures from the design, both forced by facts it did not check
(see docs/chair-bakeoff-results-2026-09-12.md §"What the design got wrong"):

1. Axis 1 is read off the chair's own `blocking_findings`, not the gate's
   `decide_blocking` count. On `stw-pr11` and `baw-pr11` every changed path is
   developer tooling, so `risk_tier` is "reduced", the tier bar is
   `critical` + c>=8, no panel finding in either is `critical`, and the gate
   returns 0 whatever the chair decides. Grading on the gate count would score
   those two cells identically for every candidate. (The gate as of 2026-09-12;
   see docs/council-gate-policy-2026-09-18.md for the later reduced bar.)
2. Case C's Axis 1 target is the chair's blocking list, not
   `review_status == "changes_requested"`. `REVIEW_SYNTH_OUTPUT` — the gate's
   chair prompt — never asks for `review_status`; only backlog-run's prompt
   does. On this path the field is structurally "unknown" for every model.

Usage:  python3 tools/regress/bakeoff_grade.py <evidence.json>
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

WINDOW = 500  # chars either side, for "resolved it right where it named it"


def _find(pattern, text):
    match = re.search(pattern, text or "", re.I)
    return match.group(0) if match else None


def _near(anchor_pattern, phrase_pattern, text, window=WINDOW):
    """Does `phrase_pattern` occur within `window` chars of `anchor_pattern`?"""
    text = text or ""
    for anchor in re.finditer(anchor_pattern, text, re.I):
        lo = max(0, anchor.start() - window)
        hi = min(len(text), anchor.end() + window)
        hit = re.search(phrase_pattern, text[lo:hi], re.I)
        if hit:
            return anchor.group(0), hit.group(0)
    return None


# --- evidence markers -------------------------------------------------------
# A: the Adversary's high-c9 "no width/height -> CLS" claim, which ground truth
#    says is not a defect (Astro's <Image> infers intrinsic dimensions).
A_ANCHOR = r"width\s*/?\s*(and\s*)?height|\bCLS\b|layout shift"
A_RESOLVE = (r"not a (defect|blocker|bug|real)|non-?blocking|does not block|do not block|"
             r"maintainabil|not block|infers?|automatic|intrinsic|does not gate|"
             r"quality|suggestion|no.{0,12}block")
# B: the engines pin in the package.json the chair was handed refutes the
#    "process.loadEnvFile breaks on Node < 20.6" finding.
B_ANCHOR = r"engines|22\.12"
B_NODE = r"loadEnvFile|node\s*<\s*20|20\.6|node version|node-version|runtime compat"
# C: the two findings the incumbent declined that were real and shipped as fixes.
# The author marker was widened after the first grading pass: `openai-gpt-56-sol`
# run 1 blocks with "Public marker is trusted without comment ownership
# validation ... without checking its author or app identity", which the original
# `author\s*(check|verif|match)` form scored 0. That was a false negative on a
# correct answer, so the pattern now covers the ownership phrasing too. Widening
# can only help candidates; it is applied identically to all four.
C_RETRY = r"retry-?after|\b429\b|\b408\b"
C_AUTHOR = (r"github-actions\[bot\]|authored by|comment author|actor check|"
            r"comment ownership|ownership\s+(check|valid)|owns?\s+the\s+comment|"
            r"does\s+not\s+own|not\s+authored|"
            r"(?:check|verif|valid|confirm)\w*[^.]{0,40}\bauthor\b|"
            r"\bauthor(ship)?\b[^.]{0,40}(?:check|verif|valid|match|identity)")


def axis1(fixture, blocking_text, blocking_count):
    """0-2 on the chair's own blocking list."""
    if fixture in ("aris-pr1", "stw-pr11"):
        if blocking_count == 0:
            return 2, "no false block"
        return 0, f"blocked {blocking_count}: {blocking_text[:200]}"
    # baw-pr11: name both real findings AS BLOCKING.
    retry, author = _find(C_RETRY, blocking_text), _find(C_AUTHOR, blocking_text)
    score = (1 if retry else 0) + (1 if author else 0)
    note = f"retry={retry!r} author={author!r} (of {blocking_count} blocking)"
    return score, note


def axis2(fixture, raw, axis1_score):
    """0-2 on whether the synthesis cites the context that settles the case."""
    if fixture == "aris-pr1":
        if axis1_score == 0:
            return 0, "wrong verdict"
        hit = _near(A_ANCHOR, A_RESOLVE, raw)
        if hit:
            return 2, f"named {hit[0]!r}, resolved {hit[1]!r}"
        if _find(A_ANCHOR, raw):
            return 1, "named the CLS claim but never resolved it as a non-defect"
        return 1, "right verdict, never named the width/height claim"
    if fixture == "stw-pr11":
        if axis1_score == 0:
            return 0, "wrong verdict"
        hit = _near(B_ANCHOR, B_NODE, raw)
        if hit:
            return 2, f"named {hit[0]!r} against {hit[1]!r}"
        pin = _find(B_ANCHOR, raw)
        if pin:
            return 2, f"named the pin {pin!r}"
        return 1, "right verdict by luck: never named the engines pin"
    retry, author = _find(C_RETRY, raw), _find(C_AUTHOR, raw)
    score = (1 if retry else 0) + (1 if author else 0)
    return score, f"retry={retry!r} author={author!r}"


def axis3(cells, panel_text):
    """0-2 once per candidate: parse discipline, block shape, no invention."""
    strict = all(c.get("jsonparse_branch") == "strict" for c in cells)
    shapes_ok, invented = True, []
    for cell in cells:
        for block in cell.get("blocking_findings") or []:
            if not (block.get("point") or "").strip() or not isinstance(block.get("severity"), str) \
                    or not isinstance(block.get("why"), str) or not block.get("severity").strip():
                shapes_ok = False
            words = {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_.\[\]/-]{3,}",
                                           block.get("point", "").lower())}
            if words and not (words & panel_text):
                invented.append(block.get("point", "")[:120])
    criteria = [strict, shapes_ok, not invented]
    score = 2 if all(criteria) else (1 if sum(criteria) == 2 else 0)
    return score, {"strict_json_all_cells": strict, "blocking_shape_ok": shapes_ok,
                   "possibly_invented": invented}


def main(path):
    evidence = json.loads(Path(path).read_text())
    runs = evidence["runs"]
    panel_dir = Path(__file__).resolve().parent / "panels"
    panel_words = {}
    for fixture_id in {r["fixture"] for r in runs}:
        blob = (panel_dir / f"{fixture_id}.json").read_text().lower()
        panel_words[fixture_id] = set(re.findall(r"[a-z_][a-z0-9_.\[\]/-]{3,}", blob))

    by_model = defaultdict(list)
    for run in runs:
        by_model[run["chair_model"]].append(run)

    scored = {}
    for model, cells in by_model.items():
        per_fixture = defaultdict(list)
        for cell in cells:
            if cell.get("error") or cell.get("chair_error"):
                per_fixture[cell["fixture"]].append(
                    {"repeat": cell["repeat"], "a1": 0, "a2": 0,
                     "note": f"ERROR {cell.get('error') or cell.get('chair_error')}"})
                continue
            blocks = cell.get("blocking_findings") or []
            blocking_text = " | ".join(
                f"{b.get('point','')} {b.get('severity','')} {b.get('why','')}" for b in blocks)
            blocking_text += " " + " ".join(cell.get("required_changes") or [])
            a1, n1 = axis1(cell["fixture"], blocking_text, len(blocks))
            a2, n2 = axis2(cell["fixture"], cell.get("raw_response", ""), a1)
            per_fixture[cell["fixture"]].append(
                {"repeat": cell["repeat"], "a1": a1, "a2": a2, "a1_note": n1, "a2_note": n2,
                 "gate_blocking": cell.get("blocking"), "branch": cell.get("jsonparse_branch"),
                 "seconds": cell.get("seconds"), "review_status": cell.get("review_status")})
        # Axis 3 is per candidate, over every cell it ran.
        merged_words = set().union(*(panel_words[f] for f in per_fixture)) if per_fixture else set()
        a3, a3_detail = axis3([c for c in cells if not c.get("error")], merged_words)
        totals = {f: {"a1_mean": statistics.mean(x["a1"] for x in v),
                      "a1_min": min(x["a1"] for x in v),
                      "a2_mean": statistics.mean(x["a2"] for x in v),
                      "a2_min": min(x["a2"] for x in v)}
                  for f, v in per_fixture.items()}
        rubric_total = sum(t["a1_mean"] + t["a2_mean"] for t in totals.values()) + a3
        scored[model] = {"per_fixture": dict(per_fixture), "summary": totals,
                         "axis3": a3, "axis3_detail": a3_detail,
                         "total_of_14": round(rubric_total, 2)}
    print(json.dumps(scored, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
