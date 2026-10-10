"""The 10 Oct 2026 clean-ups in the 30-minute watchdog's own meet rules:
never skip silently, Portugal only, morning grace, no-feed meets, cheap counts,
a 1-hour repeat for meet alerts, and standing down for the expect monitor."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import watchdog.run as run
from watchdog.triage import CheckStatus, check_meet_freshness, triage

LIS = ZoneInfo("Europe/Lisbon")
UTC = ZoneInfo("UTC")


def _at(hour, minute=0, day=(2026, 10, 10)):
    return datetime(*day, hour, minute, tzinfo=LIS).timestamp()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def _row(now, **kw):
    row = {"sr_meet_id": "10987", "name": "Benedita", "nation": "POR", "feed_type": "pdf",
           "ingest_status": "polling", "last_ingest_at": None, "updated_at": _iso(now - 10 * 3600),
           "start_date": "2026-10-10", "end_date": "2026-10-11"}
    row.update(kw)
    return row


def _collect(monkeypatch, rows, now):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setattr(run, "_supabase_count", lambda *a, **k: None)
    monkeypatch.setattr(run, "_meet_registry_rows", lambda *a, **k: rows)
    monkeypatch.setattr(run, "_cmd", lambda args: "")
    out, _ = run.collect_metrics(int(now), {})
    return {s.name: s for s in out}


def test_an_unreadable_registry_is_a_crit_not_silence(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCHDOG_EXPECT_STATE", str(tmp_path / "none.json"))
    by = _collect(monkeypatch, None, _at(14, 30))
    s = by["meets.registry"]
    assert s.level == "crit" and "NOT being checked" in s.summary


def test_the_registry_read_is_portugal_only(monkeypatch):
    asked = []
    monkeypatch.setattr(run, "_supabase_rows",
                        lambda url, key, path, error=None: asked.append(path) or [])
    run._meet_registry_rows("https://db", "key", "2026-10-01T00:00:00Z")
    assert "&nation=in.(POR)" in asked[0] and "feed_type" in asked[0]
    run._meet_registry_rows("https://db", "key", "2026-10-01T00:00:00Z", nations=None)
    assert "nation=in" not in asked[1]


def test_morning_grace_before_racing_starts():
    # Lenex-style writer (no counters): before 13:00 with no result today it
    # has not started, so it is not "gone quiet".
    now = _at(11)
    assert check_meet_freshness([_row(now)], now).level == "ok"
    assert check_meet_freshness([_row(_at(14))], _at(14)).level != "ok"


def test_morning_grace_never_hides_a_measured_gap():
    now = _at(11)
    row = _row(now, events_published=4, events_with_results=1, last_tick_errors=0,
               coverage_at=_iso(now - 60))
    assert check_meet_freshness([row], now).level != "ok"


def test_results_today_end_the_grace():
    now = _at(11)
    row = _row(now, last_ingest_at=_iso(now - 40 * 60))
    assert check_meet_freshness([row], now).level == "warn"


def test_a_meet_without_a_feed_is_not_awaiting_launch():
    now = _at(12)
    row = _row(now, ingest_status="discovered", feed_type="none")
    assert check_meet_freshness([row], now).level == "ok"
    row["feed_type"] = "pdf"
    assert "awaiting launch" in check_meet_freshness([row], now).summary


def test_row_counts_are_planner_estimates(monkeypatch):
    seen = {}

    class R:
        headers = {"Content-Range": "0-0/250310"}

    def head(url, headers, timeout):
        seen.update(headers)
        return R()
    import requests
    monkeypatch.setattr(requests, "head", head)
    assert run._supabase_count("https://db", "k", "results") == 250310
    assert seen["Prefer"] == "count=planned"


def test_meet_alerts_repeat_after_an_hour_others_after_six():
    t = 1_800_000_000
    meet = CheckStatus("meets.registry", "crit", "x")
    disk = CheckStatus("disk", "crit", "y")
    first = triage([meet, disk], {}, t)
    again = triage([meet, disk], first["state"], t + 3600)
    assert [s.name for s in again["fired"]] == ["meets.registry"]
    later = triage([meet, disk], again["state"], t + 6 * 3600)
    assert {s.name for s in later["fired"]} == {"meets.registry", "disk"}


def _state(tmp_path, last_run):
    p = tmp_path / "expect-state.json"
    p.write_text(json.dumps({"last_run": last_run}))
    return p


def test_stands_down_while_the_expect_monitor_runs(monkeypatch, tmp_path):
    now = _at(15)
    monkeypatch.setenv("WATCHDOG_EXPECT_STATE", str(_state(tmp_path, now - 300)))
    called = []
    monkeypatch.setattr(run, "_load_monitors", lambda: {
        "supabase": {"url": "u", "tables": [], "warn_per_hour": 1, "crit_per_hour": 2},
        "meet_freshness": {"defer_to_expect": True}})
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setattr(run, "_meet_registry_rows", lambda *a, **k: called.append(1) or [])
    out, _ = run.collect_metrics(int(now), {})
    by = {s.name: s for s in out}
    assert by["meets.expect-heartbeat"].level == "ok" and called == []
    assert "meets.freshness" not in by


def test_takes_over_and_warns_when_the_expect_monitor_stops(monkeypatch, tmp_path):
    now = _at(15)
    monkeypatch.setenv("WATCHDOG_EXPECT_STATE", str(_state(tmp_path, now - 3600)))
    monkeypatch.setattr(run, "_load_monitors", lambda: {
        "supabase": {"url": "u", "tables": [], "warn_per_hour": 1, "crit_per_hour": 2},
        "meet_freshness": {"defer_to_expect": True}})
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setattr(run, "_meet_registry_rows", lambda *a, **k: [])
    out, _ = run.collect_metrics(int(now), {})
    by = {s.name: s for s in out}
    assert by["meets.expect-heartbeat"].level == "warn"
    assert "meets.freshness" in by and "meets.liveness" in by


def test_no_expect_state_means_the_old_rules_run_quietly(monkeypatch, tmp_path):
    now = _at(15)
    monkeypatch.setenv("WATCHDOG_EXPECT_STATE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(run, "_load_monitors", lambda: {
        "supabase": {"url": "u", "tables": [], "warn_per_hour": 1, "crit_per_hour": 2},
        "meet_freshness": {"defer_to_expect": True}})
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setattr(run, "_meet_registry_rows", lambda *a, **k: [])
    out, _ = run.collect_metrics(int(now), {})
    by = {s.name: s for s in out}
    assert "meets.expect-heartbeat" not in by and "meets.freshness" in by
