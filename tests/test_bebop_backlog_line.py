"""The backlog line in the morning briefing.

`backlog-run` parks finished work as `in_review` and everything it could not finish as
`held`. Nothing told Rex, so items sat for weeks. `bebop/backlog_line.py` builds one line
about that, in code, so the briefing model can neither drop it nor reword it.

Two rules run through every test here:
  * it never speaks unless something is actually waiting — a line that appears every
    morning is a line nobody reads;
  * it fails open. A missing file, broken YAML or a shape nobody expected costs Rex the
    line, never the briefing.
"""
import ast
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from bebop.backlog_line import briefing_line, line_from_file

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bebop" / "backlog_line.py"
TODAY = date(2026, 9, 21)
TAIL = "Look: backlog-run report"


def item(iid, status, **kw):
    return {"id": iid, "title": iid, "repo": "none", "status": status,
            "created": date(2026, 9, 1), **kw}


# --- counts and plurals ----------------------------------------------------------

def test_nothing_waiting_says_nothing():
    items = [item("2026-09-01-a", "open"), item("2026-09-02-b", "done")]
    assert briefing_line(items, today=TODAY) == ""


def test_an_empty_backlog_says_nothing():
    assert briefing_line([], today=TODAY) == ""


def test_one_item_in_review_is_singular():
    items = [item("2026-09-03-review-complaint-sweep", "in_review", worked=date(2026, 9, 3))]
    assert briefing_line(items, today=TODAY) == (
        "Backlog: 1 waits for your review (oldest 18 days: review-complaint-sweep). " + TAIL)


def test_three_items_in_review_are_plural_and_name_the_oldest():
    items = [item("2026-09-03-review-complaint-sweep", "in_review", worked=date(2026, 9, 3)),
             item("2026-09-04-engine-cron-branch-guard", "in_review", worked=date(2026, 9, 20)),
             item("2026-09-04-seo-pause-script", "in_review", worked=date(2026, 9, 21))]
    assert briefing_line(items, today=TODAY) == (
        "Backlog: 3 wait for your review (oldest 18 days: review-complaint-sweep). " + TAIL)


def test_only_in_review_counts_as_waiting_for_review():
    items = [item("2026-09-01-a", "open"), item("2026-09-02-b", "held"),
             item("2026-09-03-c", "in_review", worked=date(2026, 9, 20))]
    assert "1 waits for your review" in briefing_line(items, today=TODAY)


# --- the age of the oldest item --------------------------------------------------

def test_age_comes_from_worked_when_the_item_has_one():
    items = [item("2026-09-03-c", "in_review", created=date(2026, 1, 1), worked=date(2026, 9, 20))]
    assert "(oldest 1 day: c)" in briefing_line(items, today=TODAY)


def test_age_falls_back_to_created_when_there_is_no_worked():
    items = [item("2026-09-03-c", "in_review", created=date(2026, 9, 3))]
    assert "(oldest 18 days: c)" in briefing_line(items, today=TODAY)


def test_an_item_from_today_reads_today_not_zero_days():
    items = [item("2026-09-21-c", "in_review", worked=date(2026, 9, 21))]
    assert "(oldest today: c)" in briefing_line(items, today=TODAY)


def test_a_date_in_the_future_is_clamped_to_today():
    items = [item("2026-09-30-c", "in_review", worked=date(2026, 9, 30))]
    assert "(oldest today: c)" in briefing_line(items, today=TODAY)


def test_dates_written_as_strings_work_too():
    items = [item("2026-09-03-c", "in_review", created="2026-09-01", worked="2026-09-03")]
    assert "(oldest 18 days: c)" in briefing_line(items, today=TODAY)


def test_the_short_name_drops_only_a_leading_date():
    items = [item("2026-09-03-seo-2026-report", "in_review", worked=date(2026, 9, 20))]
    assert "(oldest 1 day: seo-2026-report)" in briefing_line(items, today=TODAY)


def test_an_id_without_a_leading_date_is_used_whole():
    items = [item("tidy-the-shed", "in_review", worked=date(2026, 9, 20))]
    assert "(oldest 1 day: tidy-the-shed)" in briefing_line(items, today=TODAY)


def test_the_oldest_is_chosen_by_date_then_by_id():
    items = [item("2026-09-03-zulu", "in_review", worked=date(2026, 9, 3)),
             item("2026-09-03-alpha", "in_review", worked=date(2026, 9, 3))]
    assert "(oldest 18 days: alpha)" in briefing_line(items, today=TODAY)


def test_an_item_with_no_usable_date_still_counts_but_cannot_be_the_oldest():
    items = [item("2026-09-03-dated", "in_review", created=date(2026, 9, 3)),
             {"id": "2026-09-01-undated", "status": "in_review"}]
    assert briefing_line(items, today=TODAY) == (
        "Backlog: 2 wait for your review (oldest 18 days: dated). " + TAIL)


def test_the_parenthesis_is_dropped_when_no_item_has_a_usable_date():
    items = [{"id": "2026-09-01-a", "status": "in_review"},
             {"id": "2026-09-02-b", "status": "in_review", "created": "not a date"}]
    assert briefing_line(items, today=TODAY) == "Backlog: 2 wait for your review. " + TAIL


# --- newly held ------------------------------------------------------------------

def test_one_item_held_by_last_nights_run():
    items = [item("2026-09-20-a", "held", worked=date(2026, 9, 21))]
    assert briefing_line(items, today=TODAY) == (
        "Backlog: 1 new on hold since yesterday. " + TAIL)


def test_two_items_held_by_last_nights_run():
    items = [item("2026-09-20-a", "held", worked=date(2026, 9, 21)),
             item("2026-09-20-b", "held", worked="2026-09-21")]
    assert briefing_line(items, today=TODAY) == (
        "Backlog: 2 new on hold since yesterday. " + TAIL)


def test_an_older_hold_is_not_new():
    items = [item("2026-09-20-a", "held", worked=date(2026, 9, 20))]
    assert briefing_line(items, today=TODAY) == ""


def test_a_hold_with_no_worked_date_is_never_counted_as_new():
    items = [item("2026-07-20-a", "held")]
    assert briefing_line(items, today=TODAY) == ""


def test_an_item_worked_today_but_left_in_review_is_not_counted_as_held():
    items = [item("2026-09-20-a", "in_review", worked=date(2026, 9, 21))]
    line = briefing_line(items, today=TODAY)
    assert "on hold" not in line and "1 waits for your review" in line


def test_both_clauses_appear_in_order():
    items = [item("2026-09-03-review-complaint-sweep", "in_review", worked=date(2026, 9, 3)),
             item("2026-09-04-b", "in_review", worked=date(2026, 9, 20)),
             item("2026-09-05-c", "in_review", worked=date(2026, 9, 21)),
             item("2026-09-19-d", "held", worked=date(2026, 9, 21))]
    assert briefing_line(items, today=TODAY) == (
        "Backlog: 3 wait for your review (oldest 18 days: review-complaint-sweep). "
        "1 new on hold since yesterday. " + TAIL)


# --- shapes nobody expected ------------------------------------------------------

@pytest.mark.parametrize("items", [None, "items", 7, {"id": "a"}, [None, 3, "x"]])
def test_a_shape_that_is_not_a_list_of_items_says_nothing(items):
    assert briefing_line(items, today=TODAY) == ""


def test_items_that_are_not_dictionaries_are_skipped():
    items = ["junk", None, item("2026-09-03-c", "in_review", worked=date(2026, 9, 20))]
    assert "1 waits for your review" in briefing_line(items, today=TODAY)


def test_an_item_with_no_id_is_skipped():
    items = [{"status": "in_review", "worked": date(2026, 9, 20)},
             item("2026-09-03-c", "in_review", worked=date(2026, 9, 20))]
    assert "1 waits for your review" in briefing_line(items, today=TODAY)


def test_a_status_that_is_not_a_string_is_skipped():
    assert briefing_line([{"id": "a", "status": ["in_review"]}], today=TODAY) == ""


# --- the line itself -------------------------------------------------------------

def test_the_line_is_one_line_with_no_em_dash():
    items = [item("2026-09-03-c", "in_review", worked=date(2026, 9, 3)),
             item("2026-09-19-d", "held", worked=date(2026, 9, 21))]
    line = briefing_line(items, today=TODAY)
    assert "\n" not in line and "—" not in line and "–" not in line


# --- reading the file ------------------------------------------------------------

def write_backlog(tmp_path, text):
    p = tmp_path / "backlog.yaml"
    p.write_text(text)
    return p


REAL_SHAPE = """\
items:
- id: 2026-09-03-review-complaint-sweep
  title: The review-complaint sweep
  repo: romance-empire
  status: in_review
  created: 2026-09-03
  worked: 2026-09-20
  prompt: |
    Do the thing.
- id: 2026-07-20-shots-dir-retention-prune
  title: Prune the screenshot dir
  repo: none
  status: held
  created: 2026-07-20
  worked: 2026-09-21
  note: 'runner: no target repo directory'
- id: 2026-09-01-still-open
  title: Something else
  repo: none
  status: open
  created: 2026-09-01
"""


def test_a_real_shaped_file_is_read(tmp_path):
    path = write_backlog(tmp_path, REAL_SHAPE)
    assert line_from_file(path, today=TODAY) == (
        "Backlog: 1 waits for your review (oldest 1 day: review-complaint-sweep). "
        "1 new on hold since yesterday. " + TAIL)


def test_a_bare_list_of_items_is_read_too(tmp_path):
    path = write_backlog(tmp_path, "- id: 2026-09-03-c\n  status: in_review\n  worked: 2026-09-20\n")
    assert "1 waits for your review" in line_from_file(path, today=TODAY)


def test_a_missing_file_says_nothing(tmp_path):
    assert line_from_file(tmp_path / "nope.yaml", today=TODAY) == ""


def test_a_directory_in_place_of_the_file_says_nothing(tmp_path):
    assert line_from_file(tmp_path, today=TODAY) == ""


def test_malformed_yaml_says_nothing(tmp_path):
    path = write_backlog(tmp_path, "items:\n- id: [unclosed\n  status: in_review\n")
    assert line_from_file(path, today=TODAY) == ""


def test_an_empty_file_says_nothing(tmp_path):
    assert line_from_file(write_backlog(tmp_path, ""), today=TODAY) == ""


def test_a_top_level_scalar_says_nothing(tmp_path):
    assert line_from_file(write_backlog(tmp_path, "just a string\n"), today=TODAY) == ""


def test_the_env_var_overrides_the_default_path(tmp_path, monkeypatch):
    path = write_backlog(tmp_path, REAL_SHAPE)
    monkeypatch.setenv("BEBOP_BACKLOG_FILE", str(path))
    assert "1 waits for your review" in line_from_file(today=TODAY)


def test_with_no_path_and_no_env_var_the_default_is_the_shared_backlog(monkeypatch):
    monkeypatch.delenv("BEBOP_BACKLOG_FILE", raising=False)
    from bebop.backlog_line import backlog_path
    assert backlog_path() == Path.home() / "projects" / "backlog" / "backlog.yaml"


# --- the command line ------------------------------------------------------------

def helper(tmp_path, backlog_text, *, now="2026-09-21", **env):
    path = write_backlog(tmp_path, backlog_text) if backlog_text is not None else tmp_path / "gone.yaml"
    return subprocess.run([sys.executable, str(HELPER)], capture_output=True, text=True, timeout=30,
                          env={**os.environ, "BEBOP_BACKLOG_FILE": str(path),
                               "BEBOP_BACKLOG_NOW": now, **env})


def test_the_helper_prints_the_line_and_exits_zero(tmp_path):
    proc = helper(tmp_path, REAL_SHAPE)
    assert proc.returncode == 0
    assert proc.stdout.strip() == (
        "Backlog: 1 waits for your review (oldest 1 day: review-complaint-sweep). "
        "1 new on hold since yesterday. " + TAIL)


def test_the_helper_prints_nothing_when_nothing_waits(tmp_path):
    proc = helper(tmp_path, "items: []\n")
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_a_broken_file_gives_an_empty_stdout_and_a_zero_exit(tmp_path):
    proc = helper(tmp_path, "items:\n- id: [unclosed\n")
    assert proc.returncode == 0
    assert proc.stdout == "" and "Traceback" not in proc.stdout


def test_a_missing_file_gives_an_empty_stdout_and_a_zero_exit(tmp_path):
    proc = helper(tmp_path, None)
    assert proc.returncode == 0 and proc.stdout == ""


def test_an_unreadable_now_override_falls_back_to_the_real_today(tmp_path):
    proc = helper(tmp_path, "items: []\n", now="the day after tomorrow")
    assert proc.returncode == 0 and proc.stdout == ""


# --- it reads the backlog file and nothing else ----------------------------------

def test_the_helper_imports_nothing_that_could_reach_the_network_or_jev():
    allowed = {"__future__", "os", "re", "sys", "datetime", "pathlib", "yaml"}
    tree = ast.parse(HELPER.read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    assert found <= allowed, f"unexpected imports: {sorted(found - allowed)}"
