"""Offline replay of the 2026-09-12 chair bake-off through the gate. No network.

The bake-off saved 36 paid chair answers (4 chair models x 3 fixtures x 3 repeats) beside
the panels they judged. Re-running `decide_blocking` over those saved answers pins what
the reduced-tier candidate bar does to REAL reviews: the one known-real finding
(`baw-pr11`, comment ownership, high c9, tooling-only diff) must be able to block once a
chair confirms it, and no fixture whose expected block count is zero may start blocking.
"""
import json
from pathlib import Path

from council.gate import candidate_findings, decide_blocking
from council.models import ConfirmedBlock, Finding, MemberResult, Synthesis

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = json.loads((ROOT / "docs/evidence/chair-bakeoff-2026-09-12.json").read_text())


def _panel(fixture: str) -> list[MemberResult]:
    seats = json.loads((ROOT / "tools/regress/panels" / f"{fixture}.json").read_text())
    return [MemberResult(s["member"], s["model"], s["stance"], s["headline"], error=s.get("error"),
                         findings=[Finding(f["point"], f["severity"], int(f["confidence"]))
                                   for f in s.get("findings", [])])
            for s in seats]


def _replayed(run: dict) -> int:
    syn = Synthesis(recommendation=run["recommendation"], confidence=run["confidence"],
                    blocking_findings=[ConfirmedBlock(b["point"], b.get("severity", ""), b.get("why", ""))
                                       for b in run["blocking_findings"]])
    return decide_blocking(_panel(run["fixture"]), syn, tier=run["tier"])


def test_the_confirmed_real_high_on_a_tooling_only_diff_blocks():
    sol = [r for r in EVIDENCE["runs"]
           if r["fixture"] == "baw-pr11" and r["chair_model"] == "openai-gpt-56-sol"]
    assert len(sol) == 3 and all(r["tier"] == "reduced" and r["blocking"] == 0 for r in sol)  # as recorded
    assert all(len(r["blocking_findings"]) == 1 for r in sol)       # the chair confirmed it 3/3
    assert [_replayed(r) for r in sol] == [1, 1, 1]


def test_no_saved_review_of_a_clean_fixture_starts_blocking():
    clean = [r for r in EVIDENCE["runs"] if r["expected_blocking"] == 0]
    assert {r["fixture"] for r in clean} == {"aris-pr1", "stw-pr11"} and len(clean) == 24
    # stw-pr11 is the Node-compat false alarm the reduced tier was created for: its two
    # highs (c9, c8) are eligible now, and every saved chair still refuted them.
    assert len(candidate_findings(_panel("stw-pr11"), tier="reduced")) == 2
    assert [r for r in clean if _replayed(r)] == []


def test_a_chair_that_confirms_nothing_still_passes_the_real_case():
    # Eligibility is not a block: the incumbent chair confirmed nothing on baw-pr11 in the
    # bake-off, so this policy alone does not change its outcome (chair choice is separate).
    incumbent = [r for r in EVIDENCE["runs"]
                 if r["fixture"] == "baw-pr11" and r["chair_model"] == "claude-opus-4-8"]
    assert len(incumbent) == 3 and [_replayed(r) for r in incumbent] == [0, 0, 0]
