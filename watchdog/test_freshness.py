"""Unit tests for check_meet_freshness (run: pytest watchdog/test_freshness.py).

Two rules live here: the heartbeat (a live meet gone quiet) and the per-event
coverage rule (a live meet still writing, but blind to some of its events).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from . import run
from .triage import check_meet_freshness

LIS = ZoneInfo("Europe/Lisbon")
UTC = ZoneInfo("UTC")


def _now(hour):
    return datetime.now(LIS).replace(hour=hour, minute=0).timestamp()


def _iso(now, minutes_ago):
    return datetime.fromtimestamp(now - minutes_ago * 60, tz=UTC).isoformat()


def _today(now):
    return datetime.fromtimestamp(now, tz=LIS).date().isoformat()


def _live_row(now, status, last_min=None, upd_min=5, **coverage):
    """A live-by-date meet. `coverage` adds the tick counters the PDF writer
    reports (events_published / events_with_results / last_tick_errors);
    omitting them is the NULL case — a writer that does not measure them."""
    row = {"sr_meet_id": "1", "name": "Meet A", "ingest_status": status,
           "last_ingest_at": _iso(now, last_min) if last_min is not None else None,
           "updated_at": _iso(now, upd_min),
           "start_date": _today(now), "end_date": _today(now)}
    row.update(coverage)
    return row


def test_quiet_live_meet_escalates_warn_then_crit():
    now = _now(15)
    assert check_meet_freshness([_live_row(now, "polling", last_min=30)], now).level == "warn"
    assert check_meet_freshness([_live_row(now, "polling", last_min=90)], now).level == "crit"


def test_fresh_live_meet_is_ok():
    now = _now(15)
    assert check_meet_freshness([_live_row(now, "polling", last_min=5)], now).level == "ok"


def test_unlaunched_live_meet_warns_after_grace():
    now = _now(15)
    s = check_meet_freshness([_live_row(now, "discovered", upd_min=40)], now)
    assert s.level == "warn" and "awaiting launch" in s.summary


def test_recent_failed_is_crit_even_at_night():
    night = _now(3)
    s = check_meet_freshness([{"sr_meet_id": "3", "name": "C", "ingest_status": "failed",
                               "updated_at": _iso(night, 60)}], night)
    assert s.level == "crit" and "FAILED" in s.summary


def test_old_failed_and_ended_meets_are_ok():
    now = _now(15)
    rows = [
        {"sr_meet_id": "3", "name": "C", "ingest_status": "failed",
         "updated_at": _iso(now, 72 * 60)},
        {"sr_meet_id": "9", "name": "Old", "ingest_status": "queued",
         "last_ingest_at": None, "updated_at": _iso(now, 90),
         "start_date": "2026-06-01", "end_date": "2026-06-02"},
    ]
    assert check_meet_freshness(rows, now).level == "ok"


def test_outside_racing_hours_staleness_is_silent():
    night = _now(3)
    assert check_meet_freshness([_live_row(night, "polling", last_min=300)], night).level == "ok"


# --- per-event coverage -----------------------------------------------------
# The gap the heartbeat cannot see: 37 of 40 events insert on every tick, so the
# meet looks perfectly healthy while three events are lost.

def _covering(now, published, covered, last_min=5, upd_min=5):
    return _live_row(now, "polling", last_min=last_min, upd_min=upd_min,
                     events_published=published, events_with_results=covered,
                     last_tick_errors=0)


def test_three_of_forty_events_uncovered_warns():
    now = _now(15)
    s = check_meet_freshness([_covering(now, 40, 37)], now)
    assert s.level == "warn"
    assert "37/40" in s.evidence


def test_full_coverage_is_ok():
    now = _now(15)
    assert check_meet_freshness([_covering(now, 40, 40)], now).level == "ok"


def test_one_event_in_flight_on_a_big_meet_is_ok():
    # A ResultList publishes and the next tick parses it — never an alert.
    now = _now(15)
    assert check_meet_freshness([_covering(now, 40, 39)], now).level == "ok"


def test_two_of_ten_uncovered_warns_on_the_share_rule():
    # Too few to trip the flat threshold, but a fifth of a small meet is gone.
    now = _now(15)
    assert check_meet_freshness([_covering(now, 10, 8)], now).level == "warn"


def test_null_counters_are_ok():
    # The fragment and Lenex writers cannot count published events; NULL means
    # "not measured", and a gap that was never measured must never alert.
    now = _now(15)
    assert check_meet_freshness([_live_row(now, "polling", last_min=5)], now).level == "ok"
    assert check_meet_freshness(
        [_live_row(now, "polling", last_min=5, events_published=None,
                   events_with_results=None, last_tick_errors=0)], now).level == "ok"


def test_a_coverage_gap_outside_racing_hours_is_silent():
    night = _now(3)
    assert check_meet_freshness([_covering(night, 40, 37)], night).level == "ok"


def test_errors_on_the_last_tick_warn_even_at_full_coverage():
    now = _now(15)
    s = check_meet_freshness(
        [_live_row(now, "polling", last_min=5, events_published=None,
                   events_with_results=None, last_tick_errors=2)], now)
    assert s.level == "warn"
    assert "error" in s.evidence


def test_a_quiet_meet_still_outranks_a_coverage_gap():
    # Both true at once: the writer that stopped is the worse news.
    now = _now(15)
    s = check_meet_freshness([_covering(now, 40, 37, last_min=None, upd_min=90)], now)
    assert "quiet" in s.summary or "quiet" in s.evidence


def test_the_thresholds_are_configurable():
    now = _now(15)
    rows = [_covering(now, 40, 37)]
    assert check_meet_freshness(rows, now, coverage_gap_warn=5,
                                coverage_gap_pct=50).level == "ok"


# --- the registry read degrades when the migration has not run --------------

def test_the_registry_read_drops_the_coverage_columns_when_they_are_absent(monkeypatch, capsys):
    asked = []
    def fake_rows(url, key, path):
        asked.append(path)
        return None if "events_published" in path else [{"sr_meet_id": "1"}]
    monkeypatch.setattr(run, "_supabase_rows", fake_rows)

    rows = run._meet_registry_rows("https://db", "key", "2026-09-01T00:00:00Z")

    assert rows == [{"sr_meet_id": "1"}]          # the check still runs
    assert len(asked) == 2 and "events_published" not in asked[1]
    assert "tick-coverage" in capsys.readouterr().out


def test_the_registry_read_asks_for_the_coverage_columns_first(monkeypatch):
    asked = []
    monkeypatch.setattr(run, "_supabase_rows",
                        lambda url, key, path: asked.append(path) or [])
    run._meet_registry_rows("https://db", "key", "2026-09-01T00:00:00Z")
    assert len(asked) == 1 and "events_with_results" in asked[0]
