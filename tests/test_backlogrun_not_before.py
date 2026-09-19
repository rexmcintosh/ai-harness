"""A date rule in an item is read by code, not by a model.

Found 2026-09-19: the open item `epistemic-probe-quarterly-rerun` says "NOT BEFORE 2026-11-15"
and was first in tonight's queue. Nothing read the date: the runner sorts by `created`, and
the Jev hold gate cannot compare dates (it scored the item 0.38). An item whose date has not
come is DEFERRED, not held: it stays open, takes no slot, and runs by itself on the day.
"""
from datetime import date

import backlogrun.cli as br
from tests.test_backlogrun import item, load_items, work_args, world  # noqa: F401  (fixture)

TODAY = date(2026, 9, 19)


# --- reading the date -------------------------------------------------------

def test_the_phrasings_real_items_use_are_all_read():
    nb = br.not_before
    assert nb({"prompt": "cache is stale. NOT BEFORE 2026-11-15. Run: python3 x.py"}) == date(2026, 11, 15)
    assert nb({"prompt": "HELD: do not run before 2026-09-22 (reopen that day)."}) == date(2026, 9, 22)
    assert nb({"prompt": "DATE GATE: if today is before 2026-10-05, do nothing and report HELD."}) == date(2026, 10, 5)
    assert nb({"title": "Day-14 read (not before 2026-09-22)", "prompt": "p"}) == date(2026, 9, 22)
    assert nb({"prompt": "Do not start this before 2026-12-01; the data is not in yet."}) == date(2026, 12, 1)


def test_an_explicit_field_wins_over_prose_and_accepts_a_yaml_date():
    assert br.not_before({"not_before": "2026-10-01", "prompt": "NOT BEFORE 2026-11-15"}) == date(2026, 10, 1)
    assert br.not_before({"not_before": date(2026, 10, 2), "prompt": "p"}) == date(2026, 10, 2)


def test_a_plain_deadline_is_not_a_run_gate():
    # Real item: the work must be finished BEFORE a date. That is the opposite of a gate.
    assert br.not_before({"prompt": "The hook must be stable before 2026-10-05 (or on that day at the latest)."}) is None
    assert br.not_before({"prompt": "Fixed the bug that existed before 2026-08-01."}) is None
    assert br.not_before({"prompt": "no dates here"}) is None


def test_several_gates_mean_the_latest_one():
    assert br.not_before({"prompt": "not before 2026-10-01. Also: do not run before 2026-11-01."}) == date(2026, 11, 1)


def test_a_date_rule_that_cannot_be_read_is_a_problem_not_a_silent_pass():
    # The owner tried to gate the item. A typo must not let it run early.
    assert br.not_before({"prompt": "NOT BEFORE 2026-13-45"}) is None
    assert "2026-13-45" in br.date_rule_problem({"prompt": "NOT BEFORE 2026-13-45"})
    assert "soon" in br.date_rule_problem({"not_before": "soon", "prompt": "p"})
    assert br.date_rule_problem({"prompt": "NOT BEFORE 2026-11-15"}) is None
    assert br.date_rule_problem({"prompt": "no dates here"}) is None


def test_a_bad_field_does_not_hide_a_good_date_in_the_prose():
    assert br.not_before({"not_before": "soon", "prompt": "NOT BEFORE 2026-11-15"}) == date(2026, 11, 15)


def test_line_wraps_inside_the_phrase_do_not_hide_it():
    assert br.not_before({"prompt": "Context first. NOT\nBEFORE 2026-11-15."}) == date(2026, 11, 15)
    assert br.not_before({"prompt": "do not run this item\n  before 2026-11-16, the data is late"}) == date(2026, 11, 16)


# --- inside the plan --------------------------------------------------------

def test_an_item_whose_date_has_not_come_is_deferred_and_takes_no_slot(world):
    cfg = world.build([item("2026-01-01-early", created="2026-01-01", prompt="NOT BEFORE 2026-11-15. run the probe"),
                       item("2026-01-02-ready", created="2026-01-02")])
    by = {p.item["id"]: p for p in br.plan(cfg, load_items(cfg), max_items=1, today=TODAY)}
    assert by["2026-01-01-early"].action == "defer" and "2026-11-15" in by["2026-01-01-early"].reason
    assert by["2026-01-02-ready"].action == "work"


def test_on_the_day_itself_the_item_runs(world):
    cfg = world.build([item("2026-01-01-early", created="2026-01-01", prompt="NOT BEFORE 2026-11-15. run the probe")])
    assert [p.action for p in br.plan(cfg, load_items(cfg), today=date(2026, 11, 14))] == ["defer"]
    assert [p.action for p in br.plan(cfg, load_items(cfg), today=date(2026, 11, 15))] == ["work"]


def test_the_jev_gate_is_not_asked_about_an_item_whose_date_has_not_come(world):
    cfg = world.build([item("2026-01-01-early", created="2026-01-01", prompt="do not run before 2026-11-15")])
    asked = []
    br.plan(cfg, load_items(cfg), today=TODAY, gate=lambda it: asked.append(1) or (False, ""))
    assert asked == []


def test_today_defaults_to_the_utc_date(world, monkeypatch):
    cfg = world.build([item("2026-01-01-early", created="2026-01-01", prompt="NOT BEFORE 2999-01-01")])
    assert [p.action for p in br.plan(cfg, load_items(cfg))] == ["defer"]


def test_a_deferred_item_stays_open_in_the_backlog_and_no_session_runs(world, monkeypatch, capsys):
    cfg = world.build([item("2026-01-01-early", created="2026-01-01", prompt="NOT BEFORE 2999-01-01")])
    sessions = []
    monkeypatch.setattr(br, "work_one", lambda *a, **k: sessions.append(1))
    assert br.cmd_work(work_args(), cfg) == 0
    (it,) = load_items(cfg)
    assert it["status"] == "open" and sessions == []


def test_dry_run_shows_why_it_is_deferred(world, capsys):
    cfg = world.build([item("2026-01-01-early", created="2026-01-01", prompt="NOT BEFORE 2999-01-01")])
    br.cmd_work(work_args("--dry-run"), cfg)
    out = capsys.readouterr().out
    assert "DEFER 2026-01-01-early" in out and "2999-01-01" in out


def test_an_unreadable_date_rule_holds_the_item_with_a_reason(world):
    cfg = world.build([item("2026-01-01-typo", created="2026-01-01", prompt="NOT BEFORE 2026-13-45. run the probe"),
                       item("2026-01-02-ready", created="2026-01-02")])
    by = {p.item["id"]: p for p in br.plan(cfg, load_items(cfg), max_items=1, today=TODAY)}
    assert by["2026-01-01-typo"].action == "hold" and "2026-13-45" in by["2026-01-01-typo"].reason
    assert by["2026-01-02-ready"].action == "work"
