from council.review import run_pr_review
from council.models import Panel, Member
from tests.conftest import FakeClient

CODE = ("diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -1 +1 @@\n-old\n+new\n")
# A developer-tooling change -> 'reduced' blast-radius tier.
CODE_TOOLS = ("diff --git a/tools/i18n/translate.mjs b/tools/i18n/translate.mjs\n"
              "--- a/tools/i18n/translate.mjs\n+++ b/tools/i18n/translate.mjs\n@@ -1 +1 @@\n-old\n+new\n")
DOC = ("diff --git a/docs/design.md b/docs/design.md\n--- a/docs/design.md\n+++ b/docs/design.md\n"
       "@@ -1 +1 @@\n-old\n+new\n")


def _panels():
    return {
        "code-review": Panel("code-review", "review code", [Member("Eng", "code1", "be an eng")]),
        "spec-review": Panel("spec-review", "review docs", [Member("Editor", "doc1", "be an editor")]),
    }


def _chair(rec="ok", blocking=()):
    return {"recommendation": rec, "confidence": 8, "consensus": [],
            "disagreements": [], "cross_panel_themes": [],
            "blocking_findings": [{"point": p, "severity": s, "why": w} for (p, s, w) in blocking]}


def test_chair_confirmed_code_finding_blocks(member_json):
    # Panel raises a candidate (high c9) AND the chair confirms it -> blocks.
    client = FakeClient(by_model={
        "code1": member_json(stance="oppose", headline="bug",
                             findings=[("nil deref at app.py:1", "high", 9)]),
        "c": _chair("fix it", blocking=[("nil deref at app.py:1", "high", "verified in file")]),
    })
    body, blocking, unavailable = run_pr_review(CODE, _panels(), client, chair_model="c")
    assert blocking == 1
    assert unavailable is False


def test_unconfirmed_candidate_does_not_block(member_json):
    # The grounding fix (F4/A): a high-confidence candidate the chair does NOT confirm
    # (e.g. "ROOT undefined" refuted by the full file) must NOT block.
    client = FakeClient(by_model={
        "code1": member_json(stance="oppose", headline="bug",
                             findings=[("ROOT used before declaration", "critical", 9)]),
        "c": _chair("looks fine — ROOT is declared at the top of the file", blocking=()),
    })
    _, blocking, unavailable = run_pr_review(CODE, _panels(), client, chair_model="c")
    assert blocking == 0
    assert unavailable is False


def test_dev_tooling_confident_high_blocks_when_the_chair_confirms_it(member_json):
    # Blast-radius (F6) as amended 2026-09: a high c>=8 on a tools/ change is eligible,
    # and a chair that verifies it against the code blocks the merge (baw-pr11).
    client = FakeClient(by_model={
        "code1": member_json(stance="oppose", headline="ownership",
                             findings=[("deletes comments it does not own", "high", 9)]),
        "c": _chair("request changes", blocking=[("deletes comments it does not own", "high", "verified")]),
    })
    _, blocking, unavailable = run_pr_review(CODE_TOOLS, _panels(), client, chair_model="c")
    assert blocking == 1 and unavailable is False


def test_dev_tooling_high_the_chair_refutes_does_not_block(member_json):
    client = FakeClient(by_model={
        "code1": member_json(stance="oppose", headline="node compat",
                             findings=[("breaks on old Node", "high", 9)]),
        "c": _chair("approve: engines pins Node >= 22.12", blocking=()),
    })
    _, blocking, unavailable = run_pr_review(CODE_TOOLS, _panels(), client, chair_model="c")
    assert blocking == 0 and unavailable is False


def test_dev_tooling_low_confidence_high_does_not_block(member_json):
    # A hedged high (c7) is not eligible on any tier, so even a chair that lists it cannot block.
    client = FakeClient(by_model={
        "code1": member_json(stance="oppose", headline="node compat",
                             findings=[("breaks on old Node", "high", 7)]),
        "c": _chair("request changes", blocking=[("breaks on old Node", "high", "x")]),
    })
    _, blocking, _ = run_pr_review(CODE_TOOLS, _panels(), client, chair_model="c")
    assert blocking == 0


def test_chair_and_panel_receive_file_context(member_json):
    # S1/E1: the file context must reach BOTH the panel and the chair so grounding works.
    client = FakeClient(by_model={
        "code1": member_json(stance="approve", headline="ok"),
        "c": _chair(),
    })
    run_pr_review(CODE, _panels(), client, chair_model="c",
                  file_context="FULL FILE: const ROOT = '/repo'")
    member_call = next(c for c in client.calls if c["model"] == "code1")
    chair_call = next(c for c in client.calls if c["model"] == "c")
    assert "const ROOT" in member_call["user"]
    assert "const ROOT" in chair_call["user"]          # chair can now ground (was 'Code review' before)
    assert "src/app.py" in chair_call["user"]           # chair sees the diff, not just a label


def test_docs_only_pr_never_blocks(member_json):
    client = FakeClient(by_model={
        "doc1": member_json(stance="oppose", headline="bad",
                            findings=[("fatal gap", "critical", 9)]),
        "c": _chair("clarify", blocking=[("fatal gap", "critical", "x")]),
    })
    body, blocking, unavailable = run_pr_review(DOC, _panels(), client, chair_model="c")
    assert "Docs review (advisory)" in body
    assert "Code review (gate)" not in body
    assert blocking == 0          # docs never block, even a confirmed critical
    assert unavailable is False


def test_mixed_pr_gates_on_code_only(member_json):
    client = FakeClient(by_model={
        "code1": member_json(stance="oppose", headline="bug",
                             findings=[("nil deref at app.py:1", "high", 9)]),
        "doc1": member_json(stance="concerns", headline="vague",
                            findings=[("undefined term", "critical", 9)]),
        "c": _chair("address both", blocking=[("nil deref at app.py:1", "high", "real")]),
    })
    body, blocking, unavailable = run_pr_review(CODE + DOC, _panels(), client, chair_model="c")
    assert "Code review (gate)" in body and "Docs review (advisory)" in body
    assert blocking == 1          # only the code finding counts


def test_code_panel_outage_fails_closed(member_json):
    client = FakeClient(by_model={"c": _chair()}, raises_for={"code1"})
    _, blocking, unavailable = run_pr_review(CODE, _panels(), client, chair_model="c")
    assert unavailable is True     # whole (1-member) code panel errored


def test_chair_outage_fails_closed(member_json):
    # If the arbiter is down we cannot ground -> fail closed (the gate's safety property).
    client = FakeClient(by_model={
        "code1": member_json(stance="oppose", headline="bug",
                             findings=[("x", "critical", 9)])},
        raises_for={"c"})
    _, blocking, unavailable = run_pr_review(CODE, _panels(), client, chair_model="c")
    assert unavailable is True


def test_doc_panel_outage_does_not_block(member_json):
    client = FakeClient(by_model={
        "code1": member_json(stance="approve", headline="lgtm"),
        "c": _chair()},
        raises_for={"doc1"})
    _, blocking, unavailable = run_pr_review(CODE + DOC, _panels(), client, chair_model="c")
    assert unavailable is False    # doc-side failure must not fail the gate


def test_empty_diff_is_noop():
    body, blocking, unavailable = run_pr_review("", _panels(), FakeClient(), chair_model="c")
    assert blocking == 0 and unavailable is False


def test_run_pr_review_forwards_task_type_review(member_json):
    # council/review.py's PR-review path must thread task_type="review" down to
    # every complete() call (both panels + both chairs) for accurate usage logging.
    client = FakeClient(by_model={
        "code1": member_json(stance="approve", headline="ok"),
        "doc1": member_json(stance="approve", headline="ok"),
        "c": _chair(),
    })
    run_pr_review(CODE + DOC, _panels(), client, chair_model="c")
    assert client.calls  # sanity: calls actually happened
    assert all(c["task_type"] == "review" for c in client.calls)


def test_junk_but_valid_json_from_every_seat_fails_closed(member_json):
    client = FakeClient(by_model={"code1": '{": ": ", "}', "c": _chair("approve")})
    _, blocking, unavailable = run_pr_review(CODE, _panels(), client, chair_model="c")
    assert blocking == 0 and unavailable is True


def test_junk_but_valid_json_from_the_chair_fails_closed(member_json):
    client = FakeClient(by_model={
        "code1": member_json(stance="oppose", headline="bug", findings=[("nil deref", "high", 9)]),
        "c": '{": ": ", "}'})
    _, blocking, unavailable = run_pr_review(CODE, _panels(), client, chair_model="c")
    assert unavailable is True

