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

def _covering(now, published, covered, last_min=5, upd_min=5, coverage_min=1):
    """A live meet reporting coverage `coverage_min` minutes ago (None = the
    counters are there but their timestamp is not)."""
    return _live_row(now, "polling", last_min=last_min, upd_min=upd_min,
                     events_published=published, events_with_results=covered,
                     last_tick_errors=0,
                     coverage_at=_iso(now, coverage_min) if coverage_min is not None
                     else None)


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
                   events_with_results=None, last_tick_errors=0,
                   coverage_at=_iso(now, 1))], now).level == "ok"


def test_a_coverage_gap_outside_racing_hours_is_silent():
    night = _now(3)
    assert check_meet_freshness([_covering(night, 40, 37)], night).level == "ok"


def test_errors_on_the_last_tick_warn_even_at_full_coverage():
    now = _now(15)
    s = check_meet_freshness(
        [_live_row(now, "polling", last_min=5, events_published=None,
                   events_with_results=None, last_tick_errors=2,
                   coverage_at=_iso(now, 1))], now)
    assert s.level == "warn"
    assert "error" in s.evidence


# --- stale counters are not evidence ----------------------------------------
# coverage_at is when the counters were computed. Yesterday's reading must
# never raise today's alarm: the meet it described is over.

def test_yesterdays_counters_cannot_warn_today():
    now = _now(15)
    assert check_meet_freshness([_covering(now, 40, 37, coverage_min=24 * 60)],
                                now).level == "ok"


def test_counters_without_a_timestamp_stay_quiet():
    # Pre-migration rows, or a registry row patched by something that does not
    # report when it measured: unusable as evidence.
    now = _now(15)
    assert check_meet_freshness([_covering(now, 40, 37, coverage_min=None)],
                                now).level == "ok"


def test_stale_counters_cannot_raise_a_tick_error_either():
    now = _now(15)
    s = check_meet_freshness(
        [_live_row(now, "polling", last_min=5, events_published=None,
                   events_with_results=None, last_tick_errors=2,
                   coverage_at=_iso(now, 24 * 60))], now)
    assert s.level == "ok"


def test_the_counter_freshness_bound_follows_the_stale_floor():
    # Unset, the bound IS stale_warn_min: the counters come from the same tick
    # whose silence that threshold measures.
    now = _now(15)
    rows = [_covering(now, 40, 37, coverage_min=30)]
    assert check_meet_freshness(rows, now, stale_warn_min=45).level == "warn"
    assert check_meet_freshness(rows, now, stale_warn_min=15).level == "ok"


def test_the_counter_freshness_bound_can_be_set_on_its_own():
    now = _now(15)
    rows = [_covering(now, 40, 37, coverage_min=30)]
    assert check_meet_freshness(rows, now, stale_warn_min=15,
                                coverage_max_age_min=60).level == "warn"


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

def _failing_first(reason):
    """A _supabase_rows stand-in: the coverage select fails with `reason`
    (filled into the caller's error dict), the plain one succeeds."""
    asked = []
    def fake_rows(url, key, path, error=None):
        asked.append(path)
        if "events_published" not in path:
            return [{"sr_meet_id": "1"}]
        if error is not None:
            error.update(reason)
        return None
    return asked, fake_rows


def test_the_registry_read_drops_the_coverage_columns_when_they_are_absent(monkeypatch, capsys):
    asked, fake_rows = _failing_first(
        {"status": 400, "body": '{"code":"42703","message":"column ... does not exist"}'})
    monkeypatch.setattr(run, "_supabase_rows", fake_rows)

    rows = run._meet_registry_rows("https://db", "key", "2026-09-01T00:00:00Z")

    assert rows == [{"sr_meet_id": "1"}]          # the check still runs
    assert len(asked) == 2 and "events_published" not in asked[1]
    assert "no tick-coverage columns" in capsys.readouterr().out


def test_a_failure_with_no_visible_cause_is_reported_neutrally(monkeypatch, capsys):
    # A timeout or a 5xx is not evidence that the migration has not run. Say
    # what happened, not why — the freshness rule runs either way.
    asked, fake_rows = _failing_first({"status": None, "body": ""})
    monkeypatch.setattr(run, "_supabase_rows", fake_rows)

    rows = run._meet_registry_rows("https://db", "key", "2026-09-01T00:00:00Z")

    assert rows == [{"sr_meet_id": "1"}]
    out = capsys.readouterr().out
    assert "coverage select failed" in out
    assert "tick-coverage columns" not in out


def test_a_non_schema_error_is_reported_neutrally(monkeypatch, capsys):
    asked, fake_rows = _failing_first({"status": 503, "body": "upstream timeout"})
    monkeypatch.setattr(run, "_supabase_rows", fake_rows)
    run._meet_registry_rows("https://db", "key", "2026-09-01T00:00:00Z")
    assert "coverage select failed" in capsys.readouterr().out


def test_the_registry_read_asks_for_the_coverage_columns_first(monkeypatch):
    asked = []
    monkeypatch.setattr(run, "_supabase_rows",
                        lambda url, key, path, error=None: asked.append(path) or [])
    run._meet_registry_rows("https://db", "key", "2026-09-01T00:00:00Z")
    assert len(asked) == 1
    assert "events_with_results" in asked[0] and "coverage_at" in asked[0]


def test_a_failure_is_recorded_only_as_far_as_it_is_visible():
    # This is what lets the caller above tell a schema problem from a network
    # one — and stay silent about causes when the failure carries no response.
    class _Resp:
        status_code = 400
        text = '{"code":"PGRST204","message":"could not find the column"}'

    class _WithResponse(Exception):
        response = _Resp()

    seen = {}
    run._note_failure(seen, _WithResponse())
    assert seen["status"] == 400 and "PGRST204" in seen["body"]

    blind = {}
    run._note_failure(blind, RuntimeError("connection timed out"))
    assert blind["status"] is None and blind["body"] == ""
