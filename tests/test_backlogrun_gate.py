"""The pre-session hold gate: before an unattended Sonnet session is spent on an item, Jev is
asked whether finishing the item needs an outward-facing or irreversible action (README
safety rule 2). The gate may only ADD a hold. It can never clear one, and any Jev failure
leaves the run exactly as it was before the gate existed.

Measured on 157 past items (docs/jev-replays-2026-09-19.md): AUC 0.95; at 0.7 it holds 47 of
53 outward items with 8 false holds, and of the 5 accepted sessions it would have held, 3
carried an instruction rule 2 forbids.
"""
import json

import pytest

import backlogrun.cli as br
from backlogrun import gate
from tests.test_backlogrun import item, load_items, make_repo, work_args, world  # noqa: F401  (fixture)


def answer(p):
    return {"answers": {"outward": {"type": "noul", "noul": p}}, "model": gate.MODEL, "input_tokens": 100}


# --- the decision -----------------------------------------------------------

def test_a_high_score_holds_with_a_reason_that_says_how_to_release_it():
    hold, reason = gate.check({"id": "x-deploy", "repo": "ai-harness", "title": "Deploy the worker", "prompt": "run npm run deploy"},
                              ask=lambda *a, **k: answer(0.93))
    assert hold is True
    assert "0.93" in reason and "outward" in reason and "reopen x-deploy --gate-ok" in reason


def test_a_low_score_does_not_hold():
    assert gate.check({"id": "x", "repo": "ai-harness", "title": "t", "prompt": "edit a file"}, ask=lambda *a, **k: answer(0.12)) == (False, "")


def test_the_line_is_point_seven_and_a_score_on_the_line_holds():
    assert gate.LINE == 0.7
    assert gate.check({"id": "x", "repo": "ai-harness", "prompt": "p"}, ask=lambda *a, **k: answer(0.7))[0] is True
    assert gate.check({"id": "x", "repo": "ai-harness", "prompt": "p"}, ask=lambda *a, **k: answer(0.69))[0] is False


def test_no_answer_from_jev_never_holds():
    assert gate.check({"id": "x", "repo": "ai-harness", "prompt": "p"}, ask=lambda *a, **k: None) == (False, "")
    def boom(*a, **k):
        raise RuntimeError("anything")
    assert gate.check({"id": "x", "repo": "ai-harness", "prompt": "p"}, ask=boom) == (False, "")
    assert gate.check({"id": "x", "repo": "ai-harness", "prompt": "p"}, ask=lambda *a, **k: {"answers": {}}) == (False, "")


def test_the_owners_gate_ok_wins_and_jev_is_not_even_asked():
    calls = []
    got = gate.check({"id": "x", "repo": "ai-harness", "prompt": "deploy", "gate_ok": True}, ask=lambda *a, **k: calls.append(1) or answer(0.99))
    assert got == (False, "") and calls == []


def test_what_is_sent_is_repo_title_and_the_redacted_prompt_with_a_pinned_model():
    seen = {}
    def ask(state, questions, **kw):
        seen.update(state=state, questions=questions, kw=kw)
        return answer(0.1)
    gate.check({"id": "x", "repo": "ai-harness", "title": "T", "prompt": "mail rex@example.com then stop",
                "note": "runner: private note", "council": "verdict"}, ask=ask)
    assert seen["state"] == {"repository": "ai-harness", "title": "T", "task": "mail <email> then stop"}
    assert seen["kw"]["model"] == "jev-1.13.0" and seen["kw"]["project"] == "backlog-run" and seen["kw"]["task"] == "outward-gate"
    assert list(seen["questions"]) == ["outward"] and seen["questions"]["outward"]["type"] == "noul"


def test_the_wording_is_the_measured_wording():
    # Change the wording and the 0.7 line means nothing: re-run the replay first.
    import hashlib
    digest = hashlib.sha256(json.dumps(gate.QUESTION, sort_keys=True).encode()).hexdigest()
    assert digest == gate.QUESTION_SHA256


def test_the_env_switch_turns_the_gate_off(monkeypatch):
    monkeypatch.setenv("BACKLOG_GATE", "off")
    assert gate.enabled() is False
    monkeypatch.delenv("BACKLOG_GATE")
    assert gate.enabled() is True


# --- inside the plan --------------------------------------------------------

def test_a_gated_item_is_held_and_the_next_item_takes_its_slot(world):
    cfg = world.build([item("2026-01-01-deploy", created="2026-01-01"), item("2026-01-02-edit", created="2026-01-02"),
                       item("2026-01-03-more", created="2026-01-03")])
    hold_first = lambda it: (True, "gate says outward") if it["id"].endswith("deploy") else (False, "")
    by = {p.item["id"]: p for p in br.plan(cfg, load_items(cfg), max_items=1, gate=hold_first)}
    assert by["2026-01-01-deploy"].action == "hold" and "gate says outward" in by["2026-01-01-deploy"].reason
    assert by["2026-01-02-edit"].action == "work"          # the freed slot goes to the next item
    assert by["2026-01-03-more"].action == "defer"


def test_the_gate_is_only_asked_about_items_that_would_really_be_worked(world):
    cfg = world.build([item("2026-01-01-a", created="2026-01-01"), item("2026-01-02-b", created="2026-01-02"),
                       item("2026-01-03-c", repo="none", created="2026-01-03"), item("2026-01-04-d", created="2026-01-04")])
    asked = []
    br.plan(cfg, load_items(cfg), max_items=1, gate=lambda it: asked.append(it["id"]) or (False, ""))
    assert asked == ["2026-01-01-a"]                      # not the deferred ones, not the unworkable one


def test_the_gate_stops_asking_after_its_budget_and_defers_the_rest(world):
    cfg = world.build([item(f"2026-01-{n:02d}-x{n}", created=f"2026-01-{n:02d}") for n in range(1, 8)])
    asked = []
    planned = br.plan(cfg, load_items(cfg), max_items=2, gate=lambda it: asked.append(1) or (True, "outward"), gate_budget=3)
    assert len(asked) == 3
    assert [p.action for p in planned].count("hold") == 3 and [p.action for p in planned].count("work") == 0
    assert all("gate budget" in p.reason for p in planned if p.action == "defer")


def test_plan_without_a_gate_is_unchanged(world):
    cfg = world.build([item("2026-01-01-a", created="2026-01-01")])
    assert [p.action for p in br.plan(cfg, load_items(cfg))] == ["work"]


# --- in the work command ----------------------------------------------------

def test_work_holds_a_gated_item_in_the_backlog_without_spending_a_session(world, monkeypatch, capsys):
    cfg = world.build([item("2026-01-01-deploy", created="2026-01-01")])
    monkeypatch.setattr(br.gate_mod, "check", lambda it, **k: (True, "pre-session gate: outward (Jev 0.95)"))
    sessions = []
    monkeypatch.setattr(br, "work_one", lambda *a, **k: sessions.append(1))
    assert br.cmd_work(work_args(), cfg) == 0
    (it,) = load_items(cfg)
    assert it["status"] == "held" and "pre-session gate" in it["note"] and sessions == []


def test_no_gate_flag_and_env_switch_skip_the_gate(world, monkeypatch, capsys):
    cfg = world.build([item("2026-01-01-a", created="2026-01-01")])
    monkeypatch.setattr(br.gate_mod, "check", lambda it, **k: (True, "would hold"))
    br.cmd_work(work_args("--dry-run", "--no-gate"), cfg)
    assert "WORK  2026-01-01-a" in capsys.readouterr().out
    monkeypatch.setenv("BACKLOG_GATE", "off")
    br.cmd_work(work_args("--dry-run"), cfg)
    assert "WORK  2026-01-01-a" in capsys.readouterr().out


def test_dry_run_shows_the_gate_hold(world, monkeypatch, capsys):
    cfg = world.build([item("2026-01-01-a", created="2026-01-01")])
    monkeypatch.setattr(br.gate_mod, "check", lambda it, **k: (True, "pre-session gate: outward (Jev 0.95)"))
    br.cmd_work(work_args("--dry-run"), cfg)
    assert "HOLD  2026-01-01-a" in capsys.readouterr().out


def test_reopen_with_gate_ok_records_the_owners_decision(world):
    cfg = world.build([item("2026-01-01-a", status="held", created="2026-01-01")])
    assert br.cmd_reopen(br.build_parser().parse_args(["reopen", "2026-01-01-a", "--gate-ok"]), cfg) == 0
    (it,) = load_items(cfg)
    assert it["status"] == "open" and it["gate_ok"] is True


# --- council review 2026-09-19: fail-open at the edges ----------------------

@pytest.mark.parametrize("bogus", [float("nan"), float("inf"), -0.2, 1.7, "high", None, True])
def test_a_score_that_is_not_a_real_probability_never_holds(bogus):
    assert gate.check({"id": "x", "repo": "ai-harness", "prompt": "p"}, ask=lambda *a, **k: answer(bogus)) == (False, "")


def test_the_call_is_short_and_retried_at_most_once_so_a_slow_api_cannot_stall_the_night():
    seen = {}
    gate.check({"id": "x", "repo": "ai-harness", "prompt": "p"}, ask=lambda *a, **k: seen.update(k) or answer(0.1))
    assert seen["timeout"] <= 10 and seen["retries"] <= 1
    assert gate.BUDGET * (seen["retries"] + 1) * seen["timeout"] <= 300      # worst case for a whole run: 5 minutes


def test_a_missing_or_broken_jev_package_means_no_gate_not_a_broken_runner(monkeypatch):
    def broken():
        raise ImportError("no module named jev")
    monkeypatch.setattr(gate, "_jev", broken)
    assert gate.check({"id": "x", "repo": "ai-harness", "prompt": "deploy to production"}) == (False, "")
    assert gate.item_state({"id": "x", "title": "mail rex@example.com", "prompt": "p"})["title"] == "mail rex@example.com"


def test_the_title_is_redacted_like_the_prompt():
    seen = {}
    gate.check({"id": "x", "repo": "ai-harness", "title": "ask rex@example.com", "prompt": "p"}, ask=lambda state, q, **k: seen.update(state) or answer(0.1))
    assert seen["title"] == "ask <email>"


# --- owner decision 2026-09-19: the repo allow-list (jev/scope.py, contract rule 7) ----------
# The gate sends an item's title and full prompt to an outside vendor. Student, customer,
# financial and tax work must never go there, and redaction does not make feedback text safe.
# An item from a repository that is not on the list skips Jev: no call, no hold, and it runs
# as it did before the gate existed.

ALLOWED = ["ai-harness", "swimtrack", "swimtrack-website", "ultimate-portugal", "aris-management-website"]


def counting_ask(p=0.99):
    calls = []
    def ask(state, questions, **kw):
        calls.append(state)
        return answer(p)
    return ask, calls


@pytest.mark.parametrize("repo", ["sat-prep", "monthly-bidding", "romance-empire", "tax-advisor", "none",
                                  "brand-new-repo", "", None, "AI-Harness", "ai-harness-fork",
                                  "/home/dev/projects/ai-harness"])
def test_an_item_from_a_repo_that_is_not_on_the_allow_list_is_never_sent(repo):
    ask, calls = counting_ask(0.99)
    got = gate.check({"id": "2026-09-19-copy-from-feedback", "repo": repo, "title": "Deploy it",
                      "prompt": "a customer wrote: ... then run npm run deploy"}, ask=ask)
    assert got == (False, "") and calls == []


def test_an_item_with_no_repo_at_all_is_never_sent():
    ask, calls = counting_ask(0.99)
    assert gate.check({"id": "x", "title": "Deploy it", "prompt": "npm run deploy"}, ask=ask) == (False, "")
    assert calls == []


@pytest.mark.parametrize("repo", ALLOWED)
def test_each_allowed_repo_is_still_asked_once_and_a_high_score_still_holds(repo):
    ask, calls = counting_ask(0.93)
    hold, reason = gate.check({"id": "x-deploy", "repo": repo, "title": "Deploy", "prompt": "npm run deploy"}, ask=ask)
    assert hold is True and "0.93" in reason and len(calls) == 1
    assert calls[0]["repository"] == repo


@pytest.mark.parametrize("iid", ["2026-09-19-bebop-briefing-fix", "2026-09-19-tax-export", "2026-09-19-gmail-rules"])
def test_an_allowed_repo_is_not_sent_when_the_item_id_holds_an_out_of_scope_word(iid):
    ask, calls = counting_ask(0.99)
    assert gate.check({"id": iid, "repo": "ai-harness", "title": "t", "prompt": "p"}, ask=ask) == (False, "")
    assert calls == []


def test_if_the_scope_rule_cannot_be_read_nothing_is_sent(monkeypatch):
    def broken():
        raise ImportError("no module named jev.scope")
    monkeypatch.setattr(gate, "_repo_in_scope", broken)
    ask, calls = counting_ask(0.99)
    assert gate.check({"id": "x", "repo": "ai-harness", "prompt": "deploy"}, ask=ask) == (False, "")
    assert calls == []


def test_the_gate_uses_the_shared_scope_record_not_a_list_of_its_own():
    from jev import scope
    assert gate._repo_in_scope() is scope.repo_in_scope
    assert not hasattr(gate, "IN_SCOPE_REPOS")


def test_importing_the_gate_still_does_not_import_jev():
    # cli.py imports the gate at the top: a missing jev package must not stop the runner.
    import subprocess
    import sys
    code = "import sys, backlogrun.gate; sys.exit(1 if any(m == 'jev' or m.startswith('jev.') for m in sys.modules) else 0)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_plan_works_an_out_of_scope_item_without_asking_and_still_holds_an_in_scope_one(world):
    make_repo(world.root, "sat-prep")
    make_repo(world.root, "ai-harness")
    cfg = world.build([item("2026-01-01-feedback-copy", repo="sat-prep", created="2026-01-01"),
                       item("2026-01-02-deploy", repo="ai-harness", created="2026-01-02"),
                       item("2026-01-03-alpha", created="2026-01-03")])        # repo alpha: unknown, so out of scope
    ask, calls = counting_ask(0.95)
    by = {p.item["id"]: p for p in br.plan(cfg, load_items(cfg), max_items=3, gate=lambda it: gate.check(it, ask=ask))}
    assert by["2026-01-01-feedback-copy"].action == "work" and by["2026-01-01-feedback-copy"].reason == ""
    assert by["2026-01-03-alpha"].action == "work"
    assert by["2026-01-02-deploy"].action == "hold"
    assert [c["repository"] for c in calls] == ["ai-harness"]


def test_dry_run_plans_an_out_of_scope_item_as_work_with_no_jev_call(world, monkeypatch, capsys):
    from types import SimpleNamespace
    make_repo(world.root, "sat-prep")
    make_repo(world.root, "ai-harness")
    cfg = world.build([item("2026-01-01-feedback-copy", repo="sat-prep", created="2026-01-01"),
                       item("2026-01-02-deploy", repo="ai-harness", created="2026-01-02")])
    ask, calls = counting_ask(0.95)
    monkeypatch.delenv("BACKLOG_GATE", raising=False)
    monkeypatch.setattr(gate, "_jev", lambda: (SimpleNamespace(try_ask=ask), lambda text: text))
    assert br.cmd_work(work_args("--dry-run"), cfg) == 0
    out = capsys.readouterr().out
    assert "WORK  2026-01-01-feedback-copy" in out and "HOLD  2026-01-01-feedback-copy" not in out
    assert "HOLD  2026-01-02-deploy" in out                # the gate is on in this run, and it still holds
    assert [c["repository"] for c in calls] == ["ai-harness"]
