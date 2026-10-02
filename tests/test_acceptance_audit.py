"""Acceptance criteria from docs/council-audit-2026-06-25.md, replayed against the
real PR #9 / #11 scenarios. The FakeClient scripts each seat as it actually answered;
the chair's `blocking_findings` is its grounded verdict given the file_context the shim
now supplies. These assert the gate's CONTRACT: false positives stop blocking, true
positives still gate."""
from council.review import run_pr_review
from council.models import Panel, Member
from tests.conftest import FakeClient


def _panels():
    return {
        "code-review": Panel("code-review", "code", [
            Member("Eng Manager", "eng", "be an eng"),
            Member("Security Officer", "sec", "be a cso"),
            Member("Adversary", "adv", "be an adversary")]),
        "spec-review": Panel("spec-review", "docs", [Member("Editor", "doc", "edit")]),
    }


def _m(stance, headline, findings=(), suggestions=()):
    return {"stance": stance, "headline": headline,
            "findings": [{"point": p, "severity": s, "confidence": c} for (p, s, c) in findings],
            "suggestions": list(suggestions)}


def _chair(rec, blocking=()):
    return {"recommendation": rec, "confidence": 8, "consensus": [], "disagreements": [],
            "cross_panel_themes": [],
            "blocking_findings": [{"point": p, "severity": s, "why": w} for (p, s, w) in blocking]}


# AC1 + AC3 — PR #11 (dev tooling): the Node-compat block was moot under engines>=22.12.0.
# The Adversary's confident highs are eligible on the reduced tier (policy 2026-09), but the
# grounded chair confirms nothing -> the change that was blocked 4x still PASSES.
def test_pr11_dev_tooling_no_longer_blocks():
    diff = ("diff --git a/tools/i18n/translate.mjs b/tools/i18n/translate.mjs\n"
            "--- a/tools/i18n/translate.mjs\n+++ b/tools/i18n/translate.mjs\n"
            "@@ -68,6 +68,12 @@\n+function loadDotEnv(path){process.loadEnvFile(path);}\n")
    files = ("=== package.json ===\n{\"engines\":{\"node\":\">=22.12.0\"}}\n"
             "=== tools/i18n/translate.mjs ===\nconst ROOT = '/repo';\n")
    client = FakeClient(by_model={
        "eng": _m("concerns", "compat trap", [("loadEnvFile unguarded", "med", 7)]),
        "sec": _m("approve", "safe"),
        "adv": _m("concerns", "node break", [("process.loadEnvFile TypeError on old Node", "high", 9),
                                             ("runs before arg parse", "high", 8)]),
        "c": _chair("Approve — engines pins Node >=22.12.0, loadEnvFile is always present"),
    })
    _, blocking, unavailable = run_pr_review(diff, _panels(), client, chair_model="c",
                                             file_context=files)
    assert blocking == 0 and unavailable is False


# AC1 + AC4 — a full-tier ROOT-style false positive (single-lens critical) is dropped by
# grounding because the file context shows ROOT is declared.
def test_full_tier_false_critical_is_grounded_away():
    diff = ("diff --git a/src/app.mjs b/src/app.mjs\n--- a/src/app.mjs\n+++ b/src/app.mjs\n"
            "@@ -80,1 +80,2 @@\n+  loadDotEnv(join(ROOT, '.env'));\n")
    files = "=== src/app.mjs ===\nconst ROOT = dirname(...);   // declared at top\n"
    client = FakeClient(by_model={
        "eng": _m("approve", "ok"),
        "sec": _m("approve", "no risk"),
        "adv": _m("oppose", "ref error", [("ROOT used before declaration", "critical", 9)]),
        "c": _chair("Approve — ROOT is declared at module top in the provided file"),
    })
    _, blocking, _ = run_pr_review(diff, _panels(), client, chair_model="c", file_context=files)
    assert blocking == 0


# AC2 — PR #9 (production source): the View-Transitions breakage is a real bug two lenses
# raised; the chair confirms it -> it MUST still block. No regression on true positives.
def test_pr9_view_transitions_true_positive_still_blocks():
    diff = ("diff --git a/src/layouts/Layout.astro b/src/layouts/Layout.astro\n"
            "--- a/src/layouts/Layout.astro\n+++ b/src/layouts/Layout.astro\n"
            "@@ -45,0 +46,8 @@\n+  document.querySelectorAll('.reveal').forEach(o=>io.observe(o));\n")
    client = FakeClient(by_model={
        "eng": _m("concerns", "init once", [("reveal captured once", "low", 9)]),
        "sec": _m("approve", "no sec impact"),
        "adv": _m("concerns", "breaks on swap",
                  [("one-shot observe; .reveal added later stays hidden", "high", 9),
                   ("no re-scan on astro:after-swap", "high", 8)]),
        "c": _chair("Request changes — verified one-shot observer breaks under View Transitions",
                    blocking=[("one-shot reveal observer breaks under Astro View Transitions",
                               "high", "two lenses; site uses view transitions")]),
    })
    _, blocking, unavailable = run_pr_review(diff, _panels(), client, chair_model="c")
    assert blocking == 1 and unavailable is False
