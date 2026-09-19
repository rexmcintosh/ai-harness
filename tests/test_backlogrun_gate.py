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
from tests.test_backlogrun import item, load_items, work_args, world  # noqa: F401  (fixture)


def answer(p):
    return {"answers": {"outward": {"type": "noul", "noul": p}}, "model": gate.MODEL, "input_tokens": 100}


# --- the decision -----------------------------------------------------------

def test_a_high_score_holds_with_a_reason_that_says_how_to_release_it():
    hold, reason = gate.check({"id": "x-deploy", "title": "Deploy the worker", "prompt": "run npm run deploy"},
                              ask=lambda *a, **k: answer(0.93))
    assert hold is True
    assert "0.93" in reason and "outward" in reason and "reopen x-deploy --gate-ok" in reason


def test_a_low_score_does_not_hold():
    assert gate.check({"id": "x", "title": "t", "prompt": "edit a file"}, ask=lambda *a, **k: answer(0.12)) == (False, "")


def test_the_line_is_point_seven_and_a_score_on_the_line_holds():
    assert gate.LINE == 0.7
    assert gate.check({"id": "x", "prompt": "p"}, ask=lambda *a, **k: answer(0.7))[0] is True
    assert gate.check({"id": "x", "prompt": "p"}, ask=lambda *a, **k: answer(0.69))[0] is False


def test_no_answer_from_jev_never_holds():
    assert gate.check({"id": "x", "prompt": "p"}, ask=lambda *a, **k: None) == (False, "")
    def boom(*a, **k):
        raise RuntimeError("anything")
    assert gate.check({"id": "x", "prompt": "p"}, ask=boom) == (False, "")
    assert gate.check({"id": "x", "prompt": "p"}, ask=lambda *a, **k: {"answers": {}}) == (False, "")


def test_the_owners_gate_ok_wins_and_jev_is_not_even_asked():
    calls = []
    got = gate.check({"id": "x", "prompt": "deploy", "gate_ok": True}, ask=lambda *a, **k: calls.append(1) or answer(0.99))
    assert got == (False, "") and calls == []


def test_what_is_sent_is_repo_title_and_the_redacted_prompt_with_a_pinned_model():
    seen = {}
    def ask(state, questions, **kw):
        seen.update(state=state, questions=questions, kw=kw)
        return answer(0.1)
    gate.check({"id": "x", "repo": "alpha", "title": "T", "prompt": "mail rex@example.com then stop",
                "note": "runner: private note", "council": "verdict"}, ask=ask)
    assert seen["state"] == {"repository": "alpha", "title": "T", "task": "mail <email> then stop"}
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
