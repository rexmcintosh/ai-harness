"""Offline Jev-on-council experiment harness: parsing saved reviews, building requests,
scoring. No test may reach TypeSafe: every call goes through an injected transport."""
from __future__ import annotations

import json

import pytest

from tools.jev_council import experiments as ex
from tools.jev_council import jev
from tools.jev_council.reviews import in_scope, parse_review

REVIEW = """<!-- reviewed commit: abc -->
[panel: code-review · rigor: daily]

## Council

**Question:** Review this:

--- /tmp/x.diff

### Recommendation (confidence 7/10)

Approve with follow-up fixes. Two robustness gaps should be addressed before merge.

**Consensus:**
- parser is fragile
- dry-run lacks error handling

**Cross-panel themes:**
- robustness

---

<details><summary>Raw panel</summary>

#### Eng Manager · openai-gpt-53-codex — concerns

_Mostly in place, two edge paths._

- `med` (c8) Review-note extraction can over-capture (backlogrun/cli.py:568-575).
- `low` (c9) Tests miss the trailing-text case (tests/test_backlogrun.py:700-718).

<sub>lower-confidence:</sub>
- `low` (c7) Dry-run race after planning (backlogrun/cli.py:1236). _(tentative)_

- _(suggestion)_ Bound each note to a paragraph.

#### Security Officer · deepseek-v4-pro — na

__


#### Adversary · grok-4-3 — concerns

_parser splits on embedded markers_

- `med` (c8) review_notes (cli.py:569) splits on every marker inside a note body

</details>
"""


def test_parse_review_reads_the_verdict_seats_and_findings():
    r = parse_review(REVIEW, "rid")
    assert r.panel == "code-review" and r.rec_confidence == 7
    assert r.recommendation.startswith("Approve with follow-up fixes.")
    assert r.consensus == ["parser is fragile", "dry-run lacks error handling"]
    assert [(s.name, s.stance) for s in r.seats] == [
        ("Eng Manager", "concerns"), ("Security Officer", "na"), ("Adversary", "concerns")]
    assert [(f.fid, f.seat, f.severity, f.confidence, f.tentative) for f in r.findings] == [
        ("F1.1", "Eng Manager", "med", 8, False), ("F1.2", "Eng Manager", "low", 9, False),
        ("F1.3", "Eng Manager", "low", 7, True), ("F3.1", "Adversary", "med", 8, False)]
    assert r.findings[0].text.startswith("Review-note extraction can over-capture")
    assert "suggestion" not in " ".join(f.text for f in r.findings)


def test_reviews_of_out_of_scope_repos_are_never_loaded():
    # Owner data rule (2026-09-19): council text, code and diffs may go to TypeSafe for council
    # work. Student, customer, mail, tax and finance repos stay out, and so do unpublished
    # manuscripts (the romance repos): those categories need their own decision.
    assert in_scope("2026-07-27-time-standards-season-refresh", "swimtrack-website")
    assert in_scope("gate2", "ai-harness")
    for rid, repo in (("2026-08-16-math-content-polish", "sat-prep"), ("x", "tax-advisor"),
                      ("2026-07-23-freestyle-reader-magnet", "romance-empire"), ("y", "finance-tracker"),
                      ("2026-09-02-sat-prep-vocab", "anything"), ("bebop-briefing-fix", "ai-harness")):
        assert not in_scope(rid, repo)
    assert not in_scope("an-id-the-backlog-does-not-know", None)      # unknown repo: fail closed


def test_load_reviews_takes_the_repo_from_the_backlog_map_and_skips_the_rest(tmp_path):
    from tools.jev_council.reviews import load_reviews
    for name in ("2026-09-11T03Z-2026-07-27-time-standards.md", "2026-09-10T03Z-2026-08-16-math-polish.md",
                 "2026-09-09T03Z-2026-01-01-unknown.md"):
        (tmp_path / name).write_text(REVIEW)
    repo_of = {"2026-07-27-time-standards": "swimtrack-website", "2026-08-16-math-polish": "sat-prep"}
    loaded = load_reviews(tmp_path, repo_of=repo_of)
    assert [r.rid for r in loaded] == ["2026-09-11T03Z-2026-07-27-time-standards"]
    assert [r.repo for r in loaded] == ["swimtrack-website"]
    assert len(load_reviews(tmp_path, default_repo="ai-harness")) == 3


def test_ask_sends_pinned_model_redacted_state_and_returns_answers():
    sent = {}

    def transport(req, key, timeout):
        sent.update(req=req, key=key)
        return {"model": jev.MODEL, "answers": {"q": {"type": "noul", "noul": 0.9}},
                "usage": {"input_tokens": 12}}
    token = "A" * 48
    out = jev.ask({"text": f"mail me at rex@example.com with {token}"},
                  {"q": {"type": "noul", "instructions": "?"}}, key="k", transport=transport)
    assert sent["req"]["model"] == jev.MODEL == "jev-1.13.0"
    assert "rex@example.com" not in json.dumps(sent["req"]) and token not in json.dumps(sent["req"])
    assert out["answers"]["q"]["noul"] == 0.9 and out["input_tokens"] == 12


def test_ask_refuses_an_answer_from_a_different_model_version():
    def transport(req, key, timeout):
        return {"model": "jev-2.0.0", "answers": {}}
    with pytest.raises(jev.JevError, match="model"):
        jev.ask("s", {"q": {"type": "noul", "instructions": "?"}}, key="k", transport=transport)


def test_dry_run_counts_requests_and_never_calls_the_transport():
    def boom(*a, **k):
        raise AssertionError("network call in a dry run")
    r = parse_review(REVIEW, "rid")
    plan = ex.run("verdict", [r], key=None, transport=boom, dry_run=True)
    assert plan == {"experiment": "verdict", "dry_run": True, "requests": 1}


def test_verdict_request_asks_one_choice_over_the_recommendation_only():
    r = parse_review(REVIEW, "rid")
    (req,) = ex.build_requests("verdict", [r])
    assert req.state == {"recommendation": r.recommendation}
    assert set(req.questions["verdict"]["criteria"]) == {"approve", "approve_with_conditions", "request_changes"}


def test_link_request_offers_every_eligible_finding_and_a_none_option():
    r = parse_review(REVIEW, "rid")
    block = {"point": "note extraction over-captures", "why": "slices to end of prompt"}
    req = ex.link_request("rid#0", block, r.findings)
    options = req.questions["source"]["criteria"]
    assert list(options) == ["F1.1", "F1.2", "F1.3", "F3.1", "none"]
    assert options["F3.1"].startswith("review_notes (cli.py:569)")


def test_duplicate_pairs_are_cross_seat_only():
    r = parse_review(REVIEW, "rid")
    pairs = [(a.fid, b.fid) for a, b in ex.cross_seat_pairs(r)]
    assert ("F1.1", "F3.1") in pairs and ("F1.1", "F1.2") not in pairs and len(pairs) == 3


def test_leading_phrase_baseline_reads_the_common_openers():
    assert ex.baseline_verdict("Approve and merge. Looks good.") == "approve"
    assert ex.baseline_verdict("Approve with follow-up fixes. Two gaps.") == "approve_with_conditions"
    assert ex.baseline_verdict("Request rework before running on live data.") == "request_changes"
    assert ex.baseline_verdict("Hold for a small content fix, then merge.") == "unknown"


def test_score_counts_agreement_and_lists_misses():
    rows = [{"id": "a", "truth": "approve", "jev": "approve"},
            {"id": "b", "truth": "request_changes", "jev": "approve_with_conditions"}]
    s = ex.score(rows, "jev")
    assert s["n"] == 2 and s["correct"] == 1 and s["misses"] == ["b"]


def test_council_text_is_sent_whole_with_only_secrets_and_addresses_removed():
    # The watchdog's log redaction clips lines at 300 chars and strips @handles; a chair
    # recommendation is one long line and code has decorators, so council text gets its own.
    long_line = "Approve. " + ("word " * 300) + "END"
    text = f"{long_line}\n@pytest.fixture\nsee https://x.test/page?token=abc123 or mail a.b@example.org"
    out = jev.redact_text(text)
    assert out.splitlines()[0].endswith("END") and "@pytest.fixture" in out
    assert "a.b@example.org" not in out and "token=abc123" not in out
    assert "sk-" + "a" * 45 not in jev.redact_text("key sk-" + "a" * 45)


def test_fixture_link_cases_cover_the_true_source_and_both_kinds_of_none():
    from tools.jev_council import fixtures_run as fx
    cases = fx.link_requests()
    assert len(cases) == 16 and {truth for _, truth in cases} == {"F1.1", "none"}
    by_id = {req.rid: (req, truth) for req, truth in cases}
    own, _ = by_id["block0:own-panel"]
    hard, hard_truth = by_id["block0:own-panel-without-source"]
    assert "F1.1" in own.questions["source"]["criteria"]
    assert "F1.1" not in hard.questions["source"]["criteria"] and hard_truth == "none"
    assert all("none" in req.questions["source"]["criteria"] for req, _ in cases)


def test_fixture_run_dry_run_makes_no_call(capsys):
    from tools.jev_council import fixtures_run as fx
    assert fx.main(["--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out) == {"link": 16, "kinds": 19, "slices": 6}


def test_a_real_run_without_an_output_folder_is_refused(monkeypatch, capsys):
    # Raw answers quote private review text, so they must never default into the repo.
    from tools.jev_council import run as runner
    monkeypatch.setattr(runner, "dataset", lambda: [parse_review(REVIEW, "rid")])
    monkeypatch.setattr(runner.jev, "load_key", lambda: "k")
    monkeypatch.setattr(runner.ex, "run", lambda *a, **k: {"rows": [], "errors": 0, "input_tokens": 0})
    assert runner.main(["verdict"]) == 2 and "--out" in capsys.readouterr().err
