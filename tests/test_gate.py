"""Gate decision logic: severity normalization, blast-radius tier, candidate
selection, and chair-arbitrated/grounded blocking. See docs/council-audit-2026-06-25.md
(F1, F2, F4, F6) for the failure modes these fix."""
from council.gate import (
    normalize_severity, risk_tier, is_candidate, candidate_findings, decide_blocking)
from council.models import MemberResult, Finding, Synthesis, ConfirmedBlock


# ── severity normalization (F6) ──────────────────────────────────────────────
def test_normalize_severity_is_case_and_alias_insensitive():
    assert normalize_severity("CRITICAL") == "critical"
    assert normalize_severity(" High ") == "high"
    assert normalize_severity("blocker") == "critical"   # alias
    assert normalize_severity("medium") == "med"
    assert normalize_severity("") == "info"              # empty -> lowest


# ── blast-radius tier (F6) ───────────────────────────────────────────────────
def test_risk_tier_reduced_for_dev_tooling_only():
    assert risk_tier(["tools/i18n/translate.mjs"]) == "reduced"
    assert risk_tier(["scripts/build.sh", "eslint.config.mjs"]) == "reduced"


def test_risk_tier_full_for_production_source():
    assert risk_tier(["src/pages/index.astro"]) == "full"
    # any production-source path pulls the whole change up to full
    assert risk_tier(["tools/x.mjs", "src/app.py"]) == "full"


def test_risk_tier_full_when_unknown():
    assert risk_tier([]) == "full"        # no paths -> safe default


def test_risk_tier_full_for_privileged_tooling_but_not_low_risk_fixture():
    assert risk_tier([".github/workflows/venice-review.yml"]) == "full"
    assert risk_tier(["scripts/deploy-preview.sh"]) == "full"
    assert risk_tier(["bin/tg-send"]) == "full"
    assert risk_tier(["tools/i18n/translate.mjs"]) == "reduced"


# ── candidate bar, tier-aware + normalized (F6) ──────────────────────────────
def test_full_tier_candidate_bar():
    assert is_candidate("critical", 1, tier="full") is True        # critical any conf
    assert is_candidate("high", 8, tier="full") is True
    assert is_candidate("high", 7, tier="full") is False           # high needs c>=8
    assert is_candidate("med", 10, tier="full") is False           # med never


def test_reduced_tier_candidate_bar():
    # Owner policy 2026-09 (gate decision option b): a confident high is ELIGIBLE on dev
    # tooling too; the chair still has to confirm it. Everything else is unchanged.
    assert is_candidate("critical", 8, tier="reduced") is True
    assert is_candidate("high", 9, tier="reduced") is True
    assert is_candidate("high", 8, tier="reduced") is True
    assert is_candidate("high", 7, tier="reduced") is False        # a hedged high stays advisory
    assert is_candidate("critical", 5, tier="reduced") is False    # reduced critical floor kept at 8
    assert is_candidate("med", 10, tier="reduced") is False


def test_candidate_findings_skips_errored_members():
    results = [
        MemberResult("A", "m", "oppose", "h", findings=[Finding("p", "high", 9)]),
        MemberResult("B", "m", "na", "h", error="boom", findings=[Finding("x", "critical", 9)]),
    ]
    cands = candidate_findings(results, tier="full")
    assert [m for m, _ in cands] == ["A"]   # errored member's findings ignored


# ── decide_blocking: chair arbitration + grounding (F1, F2, F4) ──────────────
def _results_with(sev="high", conf=9):
    return [MemberResult("Adversary", "grok", "oppose", "h", findings=[Finding("p", sev, conf)])]


def _syn(blocks=()):
    return Synthesis(recommendation="r", confidence=8,
                     blocking_findings=[ConfirmedBlock(p, s, w) for (p, s, w) in blocks])


def test_grounding_drops_a_candidate_the_chair_refuted():
    # Candidate exists (high c9) but the chair, given context, confirms nothing
    # (e.g. "ROOT undefined" refuted by the full file) -> no block.
    assert decide_blocking(_results_with(), _syn(blocks=()), tier="full") == 0


def test_chair_confirmed_finding_blocks():
    assert decide_blocking(_results_with(), _syn(blocks=[("p", "high", "real bug")]), tier="full") == 1


def test_chair_cannot_block_without_a_candidate():
    # No seat finding clears the bar -> chair cannot manufacture a block (F2 inverse:
    # the chair arbitrates panel findings, it does not originate gates).
    weak = [MemberResult("Eng", "m", "concerns", "h", findings=[Finding("nit", "med", 10)])]
    assert decide_blocking(weak, _syn(blocks=[("nit", "med", "i say so")]), tier="full") == 0


def test_single_lens_high_does_not_auto_block(member_json=None):
    # The Adversary alone raising a high c8 must NOT block unless the chair confirms (F1/D).
    assert decide_blocking(_results_with("high", 8), _syn(blocks=()), tier="full") == 0


def test_reduced_tier_confident_high_blocks_once_the_chair_confirms_it():
    # baw-pr11: a real high c9 (comment ownership) on a tooling-only change, confirmed by
    # the chair 3/3, used to return 0 because no panel finding was `critical`.
    assert decide_blocking(_results_with("high", 9), _syn(blocks=[("p", "high", "verified")]), tier="reduced") == 1


def test_reduced_tier_high_the_chair_refuted_does_not_block():
    # stw-pr11 (the Node-compat false alarm): eligible now, but grounding still drops it.
    assert decide_blocking(_results_with("high", 9), _syn(blocks=()), tier="reduced") == 0


def test_reduced_tier_low_confidence_high_never_blocks_even_if_chair_lists_it():
    assert decide_blocking(_results_with("high", 7), _syn(blocks=[("p", "high", "x")]), tier="reduced") == 0


def test_reduced_tier_low_confidence_critical_still_cannot_block():
    assert decide_blocking(_results_with("critical", 7), _syn(blocks=[("p", "critical", "x")]), tier="reduced") == 0


# ── KNOWN GAP, pinned on purpose: confirmed blocks are not tied to panel findings ────────
# docs/council-gate-policy-2026-09-18.md ("Separate gap"). Until the provenance branch
# lands, the gate asks only "is there ANY eligible panel finding?" and then counts every
# block the chair lists. These two tests document that behaviour so it cannot drift
# silently; the provenance change is expected to flip both to the stricter count.
def test_known_gap_an_unrelated_chair_block_counts_once_any_reduced_tier_high_is_eligible():
    panel = _results_with("high", 9)                       # eligible: "p"
    syn = _syn(blocks=[("something the panel never raised", "high", "chair's own idea")])
    assert decide_blocking(panel, syn, tier="reduced") == 1


def test_known_gap_a_multi_high_panel_counts_whatever_the_chair_lists():
    panel = [MemberResult("Adversary", "grok", "oppose", "h",
                          findings=[Finding("real defect", "high", 9), Finding("hedged worry", "high", 7)])]
    # The chair confirms the INELIGIBLE c7 finding and an extra one; both count today
    # because the eligible c9 finding opens the gate for the whole list.
    syn = _syn(blocks=[("hedged worry", "high", "x"), ("unrelated", "high", "y")])
    assert decide_blocking(panel, syn, tier="reduced") == 2
    assert decide_blocking(panel, syn, tier="full") == 2   # the same gap already exists on the full tier

