"""backlog-run review policy: review sized to risk, severity gate, 3-round limit.

Backlog item 2026-09-27-backlog-run-review-policy.
"""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backlogrun import cli as br
from backlogrun import review_policy as rp
from backlogrun.readiness import evaluate
from tests.test_backlog_readiness import SHA, evidence, record  # noqa: F401 - fixtures
from tests.test_backlogrun import (  # noqa: F401 - fixtures
    REAL_COUNCIL_REVIEW, _git_identity, git, item, load_items, world, worked_branch)


# The autouse fixture tripwires the module's reviewers; keep the real ones for direct tests.
CLAUDE_DOCS_REVIEW = br.claude_docs_review
COUNCIL_DOCS_REVIEW = br.council_docs_review


def utc(hour, minute=0):
    return datetime(2026, 9, 28, hour, minute, tzinfo=timezone.utc)


# ----------------------------------------------------------------------------- classification


@pytest.mark.parametrize("paths", [
    ["README.md"],
    ["docs/guide.md", "notes.txt", "data/prices.csv", "reports/week.tsv", "docs/plan.rst"],
    ["docs/contracts/jobs.yaml", "docs/index.md"],
])
def test_prose_and_data_only_is_docs(paths):
    assert rp.classify_paths(paths) == "docs"


@pytest.mark.parametrize("code_path", [
    "tool.py", "web/app.js", "scripts/seo-pause.mjs", "src/x.ts", "engine/poller-cron.sh",
    "config.json", "package.json", "crontab.txt", "setup/cron/backlog-run.md", "Makefile",
    "Dockerfile", "bin/tg-send", "config.yaml", "panels.toml", ".github/workflows/ci.yml",
    ".claude/commands/close.md", "CLAUDE.md", "AGENTS.md", "skills/x/SKILL.md", "page.html",
])
def test_anything_that_can_execute_or_is_unsure_is_code(code_path):
    assert rp.classify_paths(["docs/guide.md", code_path]) == "code"
    assert rp.classify_paths([code_path]) == "code"


def test_complaint_sweep_case_a_script_among_reports_makes_it_code():
    # The sweep wrote reports AND a script: the whole branch gets the full code panel.
    paths = ["docs/complaints-2026-09.md", "reports/complaint-sweep.csv", "notes/summary.txt",
             "scripts/complaint-sweep.mjs"]
    assert rp.classify_paths(paths) == "code"
    assert rp.classify_paths(paths[:-1]) == "docs"


def test_no_paths_is_code():
    assert rp.classify_paths([]) == "code"
    assert rp.classify_paths(["", "  "]) == "code"


# ----------------------------------------------------------------------------- reviewer by UTC hour


@pytest.mark.parametrize("now,label", [
    (utc(21, 59), rp.REVIEWER_COUNCIL_DOCS),
    (utc(22, 0), rp.REVIEWER_CLAUDE_DOCS),
    (utc(23, 30), rp.REVIEWER_CLAUDE_DOCS),
    (utc(0, 0), rp.REVIEWER_CLAUDE_DOCS),
    (utc(6, 59), rp.REVIEWER_CLAUDE_DOCS),
    (utc(7, 0), rp.REVIEWER_COUNCIL_DOCS),
    (utc(12, 0), rp.REVIEWER_COUNCIL_DOCS),
])
def test_docs_reviewer_follows_the_utc_hour(now, label):
    got, why = rp.choose_reviewer("docs", now)
    assert got == label and "UTC" in why


@pytest.mark.parametrize("hour", [3, 12, 23])
def test_code_always_gets_the_full_council_panel(hour):
    assert rp.choose_reviewer("code", utc(hour))[0] == rp.REVIEWER_CODE


def test_pick_reviewer_maps_to_the_three_review_functions(monkeypatch):
    marks = {}
    for name in ("council_review", "claude_docs_review", "council_docs_review"):
        marks[name] = object()
        monkeypatch.setattr(br, name, marks[name])
    assert br.pick_reviewer(["a.py"], utc(23))[0] is marks["council_review"]
    assert br.pick_reviewer(["a.md"], utc(23))[0] is marks["claude_docs_review"]
    assert br.pick_reviewer(["a.md"], utc(12))[0] is marks["council_docs_review"]


def test_cheapest_seat_uses_prices_and_puts_unpriced_last():
    seats = [SimpleNamespace(model="dear"), SimpleNamespace(model="unknown"), SimpleNamespace(model="cheap")]
    prices = {"dear": {"input": 2.0, "output": 15.0}, "cheap": {"input": 1.4, "output": 2.8}}
    assert rp.cheapest_seat(seats, prices.get).model == "cheap"
    assert rp.cheapest_seat(seats[1:2], prices.get).model == "unknown"


def test_cheapest_real_spec_review_seat_is_a_priced_panel_member():
    from council.config import load_panels
    from venice_usage.pricing import price_row
    _settings, panels = load_panels(None)
    members = panels["spec-review"].members
    seat = rp.cheapest_seat(members)
    assert seat in members and price_row(seat.model) is not None
    total = lambda m: price_row(m.model)["input"] + price_row(m.model)["output"]  # noqa: E731
    assert all(total(seat) <= total(m) for m in members if price_row(m.model))


# ----------------------------------------------------------------------------- severity gate


def test_blocking_findings_are_always_serious():
    serious, minor = rp.split_findings(blocking=[("race before flock", "medium")],
                                       required_changes=[], classes=[])
    assert serious == ["race before flock"] and minor == []


def test_required_changes_split_by_class():
    changes = ["TOCTOU in the branch guard", "commits unrelated staged files", "typo in README",
               "add a docstring", "unknown class"]
    classes = [{"severity": "high", "verified_defect": "none"},
               {"severity": "low", "verified_defect": "correctness"},
               {"severity": "low", "verified_defect": "none"},
               {"severity": "medium", "verified_defect": "none"},
               {"severity": "whatever", "verified_defect": "none"}]
    serious, minor = rp.split_findings(blocking=[], required_changes=changes, classes=classes)
    assert serious == [changes[0], changes[1], changes[4]]
    assert minor == [changes[2], changes[3]]


@pytest.mark.parametrize("classes", [None, "low", [], [{"severity": "low"}], [{"severity": "low"}] * 3, ["low", "low"]])
def test_missing_or_misaligned_classes_fail_closed(classes):
    changes = ["a", "b"]
    serious, minor = rp.split_findings(blocking=[], required_changes=changes, classes=classes)
    assert serious == changes and minor == []


def test_ready_with_follow_ups(evidence):
    got = evaluate(record(follow_ups=["tidy wording"], review_round=1, reviewer="Claude review (docs)"),
                   branch_sha=SHA, state_dir=str(evidence))
    assert got["status"] == "ready_with_follow_ups"
    assert got["follow_ups"] == ["tidy wording"] and got["review_round"] == 1
    assert got["reviewer"] == "Claude review (docs)" and got["reasons"] == []


def test_follow_ups_never_hide_a_blocking_finding(evidence):
    got = evaluate(record(review_status="changes_requested", blocking_findings=["data loss"],
                          follow_ups=["tidy wording"], review_round=1),
                   branch_sha=SHA, state_dir=str(evidence))
    assert got["status"] == "changes_requested" and "data loss" in got["reasons"]


@pytest.mark.parametrize("bad", [{"follow_ups": "x"}, {"follow_ups": [""]}, {"follow_ups": [3]},
                                 {"review_round": 0}, {"review_round": "3"}, {"review_round": True}])
def test_malformed_policy_fields_are_unknown(evidence, bad):
    assert evaluate(record(**bad), branch_sha=SHA, state_dir=str(evidence))["status"] == "unknown"


@pytest.mark.parametrize("round_,status", [(1, "changes_requested"), (2, "changes_requested"),
                                           (3, "owner_decides"), (4, "owner_decides")])
def test_round_limit_turns_changes_requested_into_owner_decides(evidence, round_, status):
    got = evaluate(record(review_status="changes_requested", blocking_findings=["still broken"],
                          review_round=round_), branch_sha=SHA, state_dir=str(evidence))
    assert got["status"] == status and "still broken" in got["reasons"]


def test_round_three_that_comes_back_clean_is_ready(evidence):
    assert evaluate(record(review_round=3), branch_sha=SHA, state_dir=str(evidence))["status"] == "ready"


# ----------------------------------------------------------------------------- council adapter gate


def _council(monkeypatch, payload, *, panels=None):
    import council.config as config
    import council.engine as engine
    import council.venice as venice
    from council.models import Member, MemberResult, Panel
    from tests.conftest import FakeClient
    fake = FakeClient(default=payload)
    panels = panels or {"code-review": Panel("code-review", "fixture", [Member("a", "m1", "x")])}
    monkeypatch.setattr(config, "load_panels", lambda _: (SimpleNamespace(timeout=1, byte_cap=100000, chair_model="chair"), panels))
    ran = []

    def run_panel(panel, *a, **kw):
        ran.append(panel)
        return [MemberResult(m.name, m.model, "approve", "ok") for m in panel.members]
    monkeypatch.setattr(engine, "run_panel", run_panel)
    monkeypatch.setattr(venice, "VeniceClient", lambda *a, **kw: fake)
    monkeypatch.setattr(br, "load_venice_key", lambda role, env_path=None: "venice-test")
    monkeypatch.setattr(br.review_budget, "enabled", lambda: False)
    return fake, ran


def test_council_minor_only_changes_become_follow_ups(world, monkeypatch):
    cfg = world.build([])
    fake, _ = _council(monkeypatch, {
        "recommendation": "Approve; two small points.", "confidence": 8, "blocking_findings": [],
        "review_status": "changes_requested", "required_changes": ["fix a typo", "rename a var"],
        "required_change_classes": [{"severity": "low", "verified_defect": "none"},
                                    {"severity": "medium", "verified_defect": "none"}]})
    rev = REAL_COUNCIL_REVIEW(cfg, "diff", item_id="x")
    assert rev["review_status"] == "clean" and rev["blocking_findings"] == []
    assert rev["follow_ups"] == ["fix a typo", "rename a var"]
    assert "required_change_classes" in fake.calls[0]["system"]


def test_council_serious_change_still_sends_it_back(world, monkeypatch):
    cfg = world.build([])
    _council(monkeypatch, {
        "recommendation": "Fix the race.", "confidence": 8,
        "blocking_findings": [{"point": "TOCTOU", "severity": "high", "why": "guard before flock"}],
        "review_status": "changes_requested", "required_changes": ["fix a typo"],
        "required_change_classes": [{"severity": "low", "verified_defect": "none"}]})
    rev = REAL_COUNCIL_REVIEW(cfg, "diff", item_id="x")
    assert rev["review_status"] == "changes_requested"
    assert rev["blocking_findings"] == ["TOCTOU: guard before flock"] and rev["follow_ups"] == ["fix a typo"]


def test_council_docs_review_runs_one_cheapest_seat_that_also_chairs(world, monkeypatch):
    import venice_usage.pricing as pricing
    from council.models import Member, Panel
    cfg = world.build([])
    spec = Panel("spec-review", "fixture", [Member("Editor", "dear", "x"), Member("Skeptic", "cheap", "x")])
    fake, ran = _council(monkeypatch, {"recommendation": "fine", "confidence": 8, "blocking_findings": [],
                                       "review_status": "clean", "required_changes": []},
                         panels={"spec-review": spec})
    monkeypatch.setattr(pricing, "price_row", {"dear": {"input": 2, "output": 15},
                                               "cheap": {"input": 1, "output": 3}}.get)
    rev = COUNCIL_DOCS_REVIEW(cfg, "diff", item_id="x")
    assert rev["review_status"] == "clean" and rev["ok"]
    assert [[m.model for m in p.members] for p in ran] == [["cheap"]]
    assert [c["model"] for c in fake.calls] == ["cheap"]           # the seat chairs itself
    assert "spec-review, one seat" in rev["summary"]


# ----------------------------------------------------------------------------- Claude docs reviewer


FAKE_REVIEWER = r'''#!/usr/bin/env python3
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.stdin.read()
json.dump({"argv": sys.argv[1:], "env": dict(os.environ), "prompt": prompt},
          open(os.path.join(here, "review-capture.json"), "w"))
answer = open(os.path.join(here, "answer.json")).read()
print(json.dumps({"type": "result", "is_error": False, "result": answer}))
'''


def _fake_reviewer(tmp_path, answer):
    d = tmp_path / "reviewer"
    d.mkdir()
    fake = d / "claude"
    fake.write_text(FAKE_REVIEWER)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (d / "answer.json").write_text(json.dumps(answer) if not isinstance(answer, str) else answer)
    return fake, d


def test_claude_docs_review_is_read_only_and_returns_the_council_shape(world, tmp_path):
    cfg = world.build([])
    fake, d = _fake_reviewer(tmp_path, {
        "recommendation": "Mostly right.", "confidence": 8,
        "blocking_findings": [{"point": "wrong command", "severity": "high", "why": "deletes data"}],
        "review_status": "changes_requested", "required_changes": ["reword step 2"],
        "required_change_classes": [{"severity": "low", "verified_defect": "none"}]})
    cfg.claude_bin = str(fake)
    rev = CLAUDE_DOCS_REVIEW(cfg, "diff --git a/README.md b/README.md\n+new text\n", item_id="x")
    cap = json.loads((d / "review-capture.json").read_text())
    argv = cap["argv"]
    assert argv[argv.index("--tools") + 1] == ""                       # no tools at all: cannot write
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert argv[argv.index("--effort") + 1] == "high"
    assert "--strict-mcp-config" in argv and "--dangerously-skip-permissions" not in argv
    assert "VENICE_API_KEY" not in cap["env"] and "STRIPE_SECRET_KEY" not in cap["env"]
    assert not any(k.startswith("CLAUDE") for k in cap["env"])
    assert "+new text" in cap["prompt"]
    assert rev["ok"] and rev["review_status"] == "changes_requested"
    assert rev["blocking_findings"] == ["wrong command: deletes data"]
    assert rev["follow_ups"] == ["reword step 2"]
    assert "Claude review (docs)" in rev["summary"]


def test_claude_docs_review_minor_only_is_clean_with_follow_ups(world, tmp_path):
    cfg = world.build([])
    cfg.claude_bin = str(_fake_reviewer(tmp_path, {
        "recommendation": "Fine.", "confidence": 9, "blocking_findings": [],
        "review_status": "changes_requested", "required_changes": ["tidy a heading"],
        "required_change_classes": [{"severity": "low", "verified_defect": "none"}]})[0])
    rev = CLAUDE_DOCS_REVIEW(cfg, "diff", item_id="x")
    assert rev["review_status"] == "clean" and rev["follow_ups"] == ["tidy a heading"]


@pytest.mark.parametrize("answer", ["not json", {"confidence": 3},
                                    {"recommendation": "x", "review_status": "clean",
                                     "blocking_findings": "none", "required_changes": []}])
def test_claude_docs_review_bad_answers_never_read_as_clean(world, tmp_path, answer):
    cfg = world.build([])
    cfg.claude_bin = str(_fake_reviewer(tmp_path, answer)[0])
    rev = CLAUDE_DOCS_REVIEW(cfg, "diff", item_id="x")
    assert rev.get("review_status") != "clean"


# ----------------------------------------------------------------------------- end to end


def minor_reviewer(cfg, diff, *, item_id):
    return {"ok": True, "summary": "clean with a nit", "markdown": "review body", "review_status": "clean",
            "blocking_findings": [], "follow_ups": ["tidy the wording in step 2"]}


def serious_reviewer(cfg, diff, *, item_id):
    return {"ok": True, "summary": "fix the race", "markdown": "review body",
            "review_status": "changes_requested", "blocking_findings": ["TOCTOU before flock"],
            "follow_ups": ["tidy the wording"]}


def test_work_picks_the_reviewer_by_diff_and_hour_and_records_it(world, monkeypatch):
    seen = []

    def docs_night(cfg, diff, *, item_id):
        seen.append("claude")
        return minor_reviewer(cfg, diff, item_id=item_id)
    monkeypatch.setattr(br, "claude_docs_review", docs_night)
    monkeypatch.setattr(br, "_utc_now", lambda: utc(23, 10))
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    result = br.work_one(cfg, p, log=lambda *a: None)              # reviewer=None: sized to the diff
    assert seen == ["claude"]                                       # worked.txt only: docs, at night
    assert result["review_readiness"]["status"] == "ready_with_follow_ups"
    rec = json.loads(next(Path(cfg.reviews_dir).glob("*.inputs.json")).read_text())
    assert rec["reviewer"] == "Claude review (docs)" and "22:00-07:00 UTC" in rec["reviewer_reason"]
    assert rec["review_round"] == 1 and rec["follow_ups"] == ["tidy the wording in step 2"]
    (it,) = load_items(cfg)
    assert it["review_rounds"] == 1 and it["follow_ups"] == ["tidy the wording in step 2"]
    report = br.write_report(cfg)
    assert "Ready with follow-ups" in report and "tidy the wording in step 2" in report
    assert "round 1 of 3" in report and "by Claude review (docs)" in report
    assert "`backlog-run approve 1`" in report                      # follow-ups do not send it back


def test_work_by_day_uses_the_one_seat_docs_review(world, monkeypatch):
    seen = []
    monkeypatch.setattr(br, "council_docs_review",
                        lambda cfg, diff, *, item_id: seen.append("seat") or minor_reviewer(cfg, diff, item_id=item_id))
    monkeypatch.setattr(br, "_utc_now", lambda: utc(12))
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    br.work_one(cfg, p, log=lambda *a: None)
    assert seen == ["seat"]


def test_show_lists_follow_ups_and_round(world, capsys):
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    br.work_one(cfg, p, reviewer=minor_reviewer, log=lambda *a: None)
    br.write_report(cfg)
    assert br.cmd_show(br.build_parser().parse_args(["show", "1"]), cfg) == 0
    out = capsys.readouterr().out
    assert "Ready with follow-ups" in out and "tidy the wording in step 2" in out and "round 1 of 3" in out


def rework_args(*extra):
    return br.build_parser().parse_args(["rework", "--no-notify", *extra])


def test_third_round_hands_the_item_to_the_owner_and_rework_then_needs_force(world, monkeypatch, capsys):
    monkeypatch.setattr(br, "pick_reviewer", lambda paths, now: (serious_reviewer, "council code-review", "test"))
    cfg = world.build([item("2026-01-01-a", status="held", branch="claude/bl-a", required_validations=[],
                            review_rounds=2)])
    worked_branch(world.repo, "claude/bl-a")
    assert br.cmd_rework(rework_args("2026-01-01-a"), cfg) == 0
    (it,) = load_items(cfg)
    assert it["review_rounds"] == 3
    info = br._review_readiness(cfg, it)
    assert info["status"] == "owner_decides" and info["review_round"] == 3
    report = br.write_report(cfg)
    assert "Owner decides (3 rounds)" in report and "round 3 of 3" in report
    assert "TOCTOU before flock" in report and "tidy the wording" in report
    capsys.readouterr()
    before = Path(cfg.backlog_path).read_bytes()
    assert br.cmd_rework(rework_args("2026-01-01-a"), cfg) == 1
    assert br.cmd_rework(rework_args("2026-01-01-a", "--dry-run"), cfg) == 1
    err = capsys.readouterr().err
    assert "3 review rounds" in err and "--force" in err
    assert Path(cfg.backlog_path).read_bytes() == before
    assert br.cmd_rework(rework_args("2026-01-01-a", "--force", "--dry-run"), cfg) == 0
    assert "next review round: 4 of 3" in capsys.readouterr().out
    assert br.cmd_rework(rework_args("2026-01-01-a", "--force"), cfg) == 0
    assert load_items(cfg)[0]["review_rounds"] == 4


def test_skipped_review_is_not_a_round(world):
    cfg = world.build([item("2026-01-01-a", status="held", branch="claude/bl-a", required_validations=[],
                            review_rounds=1)])
    worked_branch(world.repo, "claude/bl-a")
    assert br.cmd_rework(rework_args("2026-01-01-a", "--no-council"), cfg) == 0
    assert load_items(cfg)[0]["review_rounds"] == 1
