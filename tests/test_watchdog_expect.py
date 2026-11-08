"""MeetTrack "expected but missing" monitor: model, alert policy, investigation
and the run glue. Fake clock (fixed Lisbon times), fake database rows, fake
sender, fake probes: nothing here touches the network or Telegram."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from watchdog.expectations import Snapshot, evaluate, make_config
from watchdog.expect_alerts import plan, render
from watchdog import expect_investigate as inv
from watchdog import expect_run as er

LIS = ZoneInfo("Europe/Lisbon")
DAY = "2026-10-10"
NEXT = "2026-10-11"


def at(hhmm, day=DAY):
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(*map(int, day.split("-")), h, m, tzinfo=LIS).timestamp()


def iso(epoch):
    return datetime.fromtimestamp(epoch, tz=ZoneInfo("UTC")).isoformat()


CFG = make_config({})
POLICY = {"repeat_crit_min": 30, "repeat_warn_min": 60, "repeat_startlist_min": 360}


def reg(sid="11113", city="Silves", **kw):
    row = {"sr_meet_id": sid, "name": f"Meet {sid}", "city": city, "nation": "POR",
           "start_date": DAY, "end_date": NEXT, "feed_type": "pdf", "ingest_status": "polling",
           "last_ingest_at": None, "updated_at": iso(at("00:00")), "events_published": 0,
           "events_with_results": 0, "last_tick_errors": 0, "coverage_at": None,
           "entries_requested_at": iso(at("12:30", "2026-10-09")),
           "entries_ingested_at": iso(at("14:00", "2026-10-09")), "dispatch_paused": False}
    row.update(kw)
    return row


def ev(n, entries, results, relay=1, day_time=None):
    return {"event_number": n, "session_number": 1, "day_time": day_time, "relay_count": relay,
            "entries": entries, "results": results}


def snap(now, rows, events=None, today=None, procs=None, **kw):
    return Snapshot(now=now, db_ok=True, db_latency_s=0.1, registry=rows,
                    events=events or {}, results_today=today or {},
                    processes=procs if procs is not None else {r["sr_meet_id"]: ["1 00:10 track_pdf_meet.py"] for r in rows},
                    **kw)


def by_id(exps):
    return {e.id: e for e in exps}


# --- the 10 Oct 2026 replay -------------------------------------------------

SILVES_EVENTS = [ev(1, 21, 0), ev(2, 12, 0), ev(3, 19, 0), ev(4, 27, 0), ev(5, 23, 1),
                 ev(6, 14, 2), ev(7, 24, 2), ev(8, 36, 1), ev(9, 45, 2), ev(10, 30, 0),
                 ev(11, 33, 0), ev(12, 0, 0, relay=4), ev(14, 6, 0)]
BENEDITA_EVENTS = [ev(1, 34, 28), ev(2, 73, 53), ev(3, 66, 0), ev(4, 52, 0)]


def replay_snapshot(now):
    silves = reg(last_ingest_at=iso(at("17:19")), events_published=11, events_with_results=5,
                 coverage_at=iso(now - 30))
    benedita = reg("10987", "Benedita", feed_type="fragment", last_ingest_at=iso(at("16:52")),
                   events_published=2, events_with_results=2, coverage_at=iso(now - 20),
                   entries_ingested_at=iso(at("23:15", "2026-10-08")))
    burgas = reg("10990", "Burgas", nation="BUL", feed_type="unknown", ingest_status="unsupported")
    return snap(now, [silves, benedita, burgas],
                events={"11113": SILVES_EVENTS, "10987": BENEDITA_EVENTS},
                today={"11113": 8, "10987": 81},
                stale_active=[{"live_rankings_id": "sr-10162", "nation": "BIH", "end_date": "2026-06-20"},
                              {"live_rankings_id": "sr-10279", "nation": "MKD", "end_date": "2026-06-28"}])


def test_replay_silves_is_missed_results_and_benedita_is_clean():
    now = at("17:45")
    exps, _ = evaluate(replay_snapshot(now), CFG, {})
    e = by_id(exps)
    silves = e["results:11113:2026-10-10"]
    assert silves.status == "missed" and silves.severity == "crit"
    assert "8 results for 142 entries" in silves.evidence           # events 5-9, thin
    # events 1-4 and 10 have no results; event 11 is the newest, still being swum
    assert "events 1, 2, 3, 4, 10" in silves.evidence
    assert "event 11, the newest, is in progress (no results yet)" in silves.evidence
    missed = [x for x in exps if x.status == "missed" and x.severity != "info"]
    assert [x.id for x in missed] == ["results:11113:2026-10-10"]
    assert all(x.status in ("met", "n/a") for x in exps if x.sr_meet_id == "10987")
    assert not any(x.sr_meet_id == "10990" for x in exps)            # Portugal only
    hyg = e["hygiene:stale-active"]
    assert hyg.severity == "info" and "sr-10162" in hyg.evidence and "sr-10279" in hyg.evidence


def test_thin_events_count_from_their_own_newest_result():
    # 17:58: one new Silves row at 17:57 must not restart the clock on events
    # 5-9, which have looked the same since their last row at 16:35.
    now = at("17:58")
    s = replay_snapshot(now)
    s.registry[0]["last_ingest_at"] = iso(at("17:57"))
    exps, _ = evaluate(s, CFG, {})
    assert by_id(exps)["results:11113:2026-10-10"].status == "pending"
    s.thin_last_write["11113"] = at("16:35")
    exps, _ = evaluate(s, CFG, {})
    e = by_id(exps)["results:11113:2026-10-10"]
    assert e.status == "missed" and "short since 16:35" in e.evidence


def test_snapshot_asks_for_the_newest_result_on_thin_events_only():
    calls = []

    class FakeRest:
        def get(self, path, timeout, count=False):
            calls.append(path)
            if path.startswith("meet_registry?select=sr_meet_id&limit=1"):
                return [{"sr_meet_id": "1"}], None
            if path.startswith("meet_registry"):
                return [reg()], None
            if path.startswith("meets?select=id"):
                return [{"id": "m1", "live_rankings_id": "sr-11113"}], None
            if path.startswith("events"):
                return [{"id": "e1", "event_number": 1, "relay_count": 1,
                         "results": [{"count": 2}], "heats": [{"count": 30}]},
                        {"id": "e2", "event_number": 2, "relay_count": 1,
                         "results": [{"count": 30}], "heats": [{"count": 30}]}], None
            if path.startswith("results?select=created_at"):
                return [{"created_at": iso(at("16:35"))}], None
            if path.startswith("results"):
                return 32, None
            return [], None
    s = er.build_snapshot(at("17:58"), CFG, RUN_CFG, FakeRest(), ps=lambda: "")
    newest = [p for p in calls if p.startswith("results?select=created_at")]
    assert newest == ["results?select=created_at&event_id=in.(e1)&order=created_at.desc&limit=1"]
    assert s.thin_last_write["11113"] == at("16:35")


def test_replay_pages_only_silves():
    now = at("17:45")
    exps, _ = evaluate(replay_snapshot(now), CFG, {})
    notices, state = plan(exps, {}, now, POLICY, racing_now=True)
    assert [(n.kind, n.id) for n in notices] == [("new", "results:11113:2026-10-10")]
    text = render(notices, now)
    assert "Silves (sr-11113)" in text and "results missing" in text
    assert "Benedita" not in text and "sr-10162" not in text


# --- database --------------------------------------------------------------

def down(now):
    return Snapshot(now=now, db_ok=False, db_latency_s=8.0, db_error="ReadTimeout")


def test_one_failed_probe_is_pending_two_are_missed():
    now = at("14:10")
    exps, mem = evaluate(down(now), CFG, {"last_live": ["Silves (sr-11113)"]})
    assert by_id(exps)["db:reachable"].status == "pending"
    exps, mem = evaluate(down(now + 300), CFG, mem)
    db = by_id(exps)["db:reachable"]
    assert db.status == "missed" and db.window == "always"
    assert "ReadTimeout" in db.evidence and "Silves (sr-11113)" in db.evidence
    assert by_id(exps)["blind:Silves (sr-11113)"].status == "unknown"


def test_db_down_alerts_at_night_and_resolves():
    night = at("03:00")
    _, mem = evaluate(down(night), CFG, {})
    exps, mem = evaluate(down(night + 300), CFG, mem)
    notices, state = plan(exps, {}, night + 300, POLICY, racing_now=False)
    assert [(n.kind, n.id) for n in notices] == [("new", "db:reachable")]
    exps, mem = evaluate(snap(night + 600, []), CFG, mem)
    notices, state = plan(exps, state, night + 600, POLICY, racing_now=False)
    assert [(n.kind, n.id) for n in notices] == [("resolved", "db:reachable")]
    assert mem["db_failures"] == 0


def test_db_down_repeats_every_30_minutes_not_6_hours():
    t = at("14:00")
    _, mem = evaluate(down(t), CFG, {})
    state, kinds = {}, []
    for step in range(1, 14):                         # an hour of 5-minute runs
        now = t + step * 300
        exps, mem = evaluate(down(now), CFG, mem)
        notices, state = plan(exps, state, now, POLICY, racing_now=True)
        kinds += [n.kind for n in notices]
    assert kinds == ["new", "reminder", "reminder"]


def test_registry_unreadable_is_its_own_crit():
    now = at("14:00")
    s = Snapshot(now=now, db_ok=True, db_latency_s=0.2, registry=None, registry_error="HTTP 500")
    exps, _ = evaluate(s, CFG, {})
    reg_exp = by_id(exps)["db:registry"]
    assert reg_exp.status == "missed" and "HTTP 500" in reg_exp.evidence


def test_open_meet_alerts_stay_open_while_the_db_is_down():
    now = at("16:00")
    state = {"open": {"results:11113:2026-10-10": {"kind": "results", "severity": "crit",
                                                    "first_missed": now - 3600,
                                                    "last_sent": now - 600, "sends": 1}}}
    _, mem = evaluate(down(now), CFG, {})
    exps, mem = evaluate(down(now + 300), CFG, mem)
    notices, state = plan(exps, state, now + 300, POLICY, racing_now=True)
    assert "results:11113:2026-10-10" in state["open"]
    assert not any(n.kind in ("closed", "resolved") for n in notices)


# --- start lists ------------------------------------------------------------

def test_requested_start_list_missing_after_t_minus_24h_is_missed_at_any_hour():
    now = at("22:30")
    row = reg("11234", "Entroncamento", start_date=NEXT, end_date=NEXT,
              entries_requested_at=iso(at("10:00")), entries_ingested_at=None,
              ingest_status="discovered")
    exps, _ = evaluate(snap(now, [row]), CFG, {})
    e = by_id(exps)["startlist:11234"]
    assert e.status == "missed" and e.window == "always"
    notices, _ = plan(exps, {}, now, POLICY, racing_now=False)
    assert [n.id for n in notices] == ["startlist:11234"]


def test_a_late_request_gets_a_grace_period():
    row = reg("11234", start_date=NEXT, end_date=NEXT, entries_requested_at=iso(at("20:00")),
              entries_ingested_at=None, ingest_status="discovered")
    exps, _ = evaluate(snap(at("20:30"), [row]), CFG, {})
    assert by_id(exps)["startlist:11234"].status == "pending"
    exps, _ = evaluate(snap(at("21:01"), [row]), CFG, {})
    assert by_id(exps)["startlist:11234"].status == "missed"


def test_unrequested_start_lists_are_not_expected_unless_scope_is_all():
    row = reg("11021", start_date=NEXT, end_date=NEXT, entries_requested_at=None,
              entries_ingested_at=None, ingest_status="discovered")
    exps, _ = evaluate(snap(at("20:00"), [row]), CFG, {})
    assert by_id(exps)["startlist:11021"].status == "n/a"
    exps, _ = evaluate(snap(at("20:00"), [row]), make_config({"startlist_scope": "all"}), {})
    assert by_id(exps)["startlist:11021"].status == "missed"


def test_loaded_start_list_is_met_and_racing_start_closes_a_missing_one():
    row = reg()
    exps, _ = evaluate(snap(at("08:00"), [row]), CFG, {})
    assert by_id(exps)["startlist:11113"].status == "met"
    row = reg("11234", start_date=DAY, end_date=DAY, entries_ingested_at=None)
    exps, _ = evaluate(snap(at("09:05"), [row]), CFG, {})
    assert by_id(exps)["startlist:11234"].status == "closed"


# --- the live writer --------------------------------------------------------

def test_unlaunched_writer_is_missed_after_the_launch_grace():
    row = reg(ingest_status="discovered")
    assert by_id(evaluate(snap(at("08:20"), [row]), CFG, {})[0])["writer:11113:2026-10-10"].status == "pending"
    e = by_id(evaluate(snap(at("08:31"), [row]), CFG, {})[0])["writer:11113:2026-10-10"]
    assert e.status == "missed" and "not launched" in e.evidence


def test_failed_writer_and_stopped_ticks_are_missed():
    now = at("15:00")
    exps, _ = evaluate(snap(now, [reg(ingest_status="failed")]), CFG, {})
    assert by_id(exps)["writer:11113:2026-10-10"].status == "missed"
    row = reg(coverage_at=iso(now - 31 * 60), events_published=3)
    exps, _ = evaluate(snap(now, [row], procs={"11113": []}), CFG, {})
    e = by_id(exps)["writer:11113:2026-10-10"]
    assert e.status == "missed" and "31 min ago" in e.evidence and "no writer process" in e.evidence


def test_ticking_writer_is_met_and_finished_meet_has_no_results_expectations():
    now = at("15:00")
    exps, _ = evaluate(snap(now, [reg(coverage_at=iso(now - 60))]), CFG, {})
    assert by_id(exps)["writer:11113:2026-10-10"].status == "met"
    exps, _ = evaluate(snap(now, [reg(ingest_status="finished")]), CFG, {})
    ids = by_id(exps)
    assert ids["writer:11113:2026-10-10"].status == "met"
    assert "results:11113:2026-10-10" not in ids and "first:11113:2026-10-10" not in ids


def test_a_meet_without_a_live_feed_is_a_note_not_a_page():
    row = reg("11148", "Castro Daire", feed_type="none", ingest_status="discovered",
              start_date=DAY, end_date=DAY, entries_requested_at=None)
    exps, _ = evaluate(snap(at("12:00"), [row]), CFG, {})
    e = by_id(exps)["feed:11148"]
    assert e.severity == "info"
    assert plan(exps, {}, at("12:00"), POLICY, racing_now=True)[0] == []


# --- first results (morning grace) ------------------------------------------

def test_morning_grace_without_a_start_time_only_when_the_host_is_unseen():
    # The writer counts the host's files and there are none (Alapraia, 7 Nov):
    # the host has published nothing, so 17:00 passing is not a fault.
    row = reg(coverage_at=iso(at("12:00")))
    exps, mem = evaluate(snap(at("12:00"), [row]), CFG, {})
    assert by_id(exps)["first:11113:2026-10-10"].status == "pending"
    exps, _ = evaluate(snap(at("17:01"), [reg(coverage_at=iso(at("17:00")))]), CFG, mem)
    assert by_id(exps)["first:11113:2026-10-10"].status == "pending"
    # The writer has not counted them (no tick yet): we cannot see the host, so
    # the morning grace still decides (afternoon meets: results by 17:00).
    blind = reg(events_published=None, events_with_results=None)
    exps, mem = evaluate(snap(at("12:00"), [blind]), CFG, {})
    assert by_id(exps)["first:11113:2026-10-10"].status == "pending"
    exps, _ = evaluate(snap(at("17:01"), [blind]), CFG, mem)
    e = by_id(exps)["first:11113:2026-10-10"]
    assert e.status == "missed" and e.severity == "warn" and "0 results today" in e.evidence


def test_a_stated_start_moves_the_deadline():
    cfg = make_config({"start_times": [{"sr_meet_id": "11113", "date": DAY, "start": "15:30"}]})
    # writer blind to the host: start + 60 min, as before
    blind = reg(events_published=None, events_with_results=None)
    exps, mem = evaluate(snap(at("16:29"), [blind]), cfg, {})
    assert by_id(exps)["first:11113:2026-10-10"].status == "pending"
    exps, _ = evaluate(snap(at("16:31"), [blind]), cfg, mem)
    e = by_id(exps)["first:11113:2026-10-10"]
    assert e.status == "missed" and "racing started about 61 min ago" in e.evidence
    # the writer sees a host with no files: only long after the start (180 min)
    exps, mem = evaluate(snap(at("16:31"), [reg(coverage_at=iso(at("16:31")))]), cfg, {})
    assert by_id(exps)["first:11113:2026-10-10"].status == "pending"
    exps, _ = evaluate(snap(at("18:30"), [reg(coverage_at=iso(at("18:30")))]), cfg, mem)
    e = by_id(exps)["first:11113:2026-10-10"]
    assert e.status == "missed" and "no result files 180 min after the stated start time" in e.evidence


def test_first_results_met_by_todays_results_and_day_two_counts_only_new_files():
    row = reg(coverage_at=iso(at("17:30")))
    exps, _ = evaluate(snap(at("17:30"), [row], today={"11113": 3}), CFG, {})
    assert by_id(exps)["first:11113:2026-10-10"].status == "met"
    # day two: yesterday's 10 published files are the baseline, not today's news
    events = {"11113": [ev(n, 10, 10) for n in range(1, 11)] + [ev(11, 10, 0)]}
    row = reg(events_published=10, events_with_results=10, coverage_at=iso(at("08:00", NEXT)))
    exps, mem = evaluate(snap(at("08:00", NEXT), [row], events=events), CFG, {})
    assert mem["baseline:11113:2026-10-11"] == 10
    assert by_id(exps)["first:11113:2026-10-11"].status == "pending"
    # one new file today and still nothing in MeetTrack: due 60 min after it appeared
    row.update(events_published=11, coverage_at=iso(at("12:00", NEXT)))
    exps, mem = evaluate(snap(at("12:00", NEXT), [row], events=events), CFG, mem)
    e = by_id(exps)["first:11113:2026-10-11"]
    assert e.status == "pending" and "(1 new today)" in e.evidence
    row["coverage_at"] = iso(at("13:00", NEXT))
    exps, _ = evaluate(snap(at("13:00", NEXT), [row], events=events), CFG, mem)
    assert by_id(exps)["first:11113:2026-10-11"].status == "missed"


# --- results complete -------------------------------------------------------

def test_published_without_results_waits_the_lag_then_misses():
    t = at("15:00")
    row = reg(events_published=4, events_with_results=3, coverage_at=iso(t - 30),
              last_ingest_at=iso(t - 120))
    # event 2 is published without results; event 4, the newest, has its results
    events = {"11113": [ev(1, 10, 10), ev(2, 10, 0), ev(3, 10, 10), ev(4, 10, 10)]}

    def run(minutes, mem):
        row["coverage_at"] = iso(t + minutes * 60 - 30)
        exps, mem = evaluate(snap(t + minutes * 60, [row], events=events), CFG, mem)
        return by_id(exps)["results:11113:2026-10-10"], mem
    e, mem = run(0, {})
    assert e.status == "pending"
    e, mem = run(19, mem)
    assert e.status == "pending"
    e, mem = run(20, mem)
    assert e.status == "missed" and "1 of them has no results" in e.evidence
    row["events_with_results"] = 4
    e, mem = run(25, mem)
    assert e.status == "met"
    assert "results:11113:2026-10-10" not in mem["late_events"]


def test_stale_counters_do_not_count_as_missing():
    # ...nor as complete: not judged (council round 1, 2026-11-07)
    t = at("15:00")
    row = reg(events_published=4, events_with_results=1, coverage_at=iso(t - 45 * 60))
    exps, _ = evaluate(snap(t, [row]), CFG, {})
    assert by_id(exps)["results:11113:2026-10-10"].status == "unknown"


def test_relays_and_unseeded_events_are_never_thin():
    t = at("15:00")
    row = reg(events_published=3, events_with_results=3, coverage_at=iso(t - 30),
              last_ingest_at=iso(t - 3600))
    events = [ev(1, 20, 18), ev(2, 0, 3), ev(3, 0, 5, relay=4), ev(4, 3, 1)]
    exps, _ = evaluate(snap(t, [row], events={"11113": events}), CFG, {})
    assert by_id(exps)["results:11113:2026-10-10"].status == "met"


# --- schedule and source notes ----------------------------------------------

def test_events_past_their_slot_without_results_are_missed():
    now = at("12:00")
    events = [ev(1, 10, 10, day_time=f"{DAY}T09:00:00"), ev(2, 10, 0, day_time=f"{DAY}T10:00:00"),
              ev(3, 10, 0, day_time=f"{DAY}T10:30:00"), ev(4, 10, 0, day_time=f"{DAY}T11:30:00")]
    row = reg(coverage_at=iso(now - 30), events_published=1, events_with_results=1,
              last_ingest_at=iso(at("09:20")))
    exps, _ = evaluate(snap(now, [row], events={"11113": events}, today={"11113": 10}), CFG, {})
    e = by_id(exps)["schedule:11113:2026-10-10"]
    assert e.status == "missed" and "2 of 3 events" in e.evidence and "events 2, 3" in e.evidence


def test_a_quiet_host_is_a_note_only():
    now = at("17:45")
    row = reg("10987", "Benedita", events_published=2, events_with_results=2,
              coverage_at=iso(now - 30), last_ingest_at=iso(now - 70 * 60))
    exps, _ = evaluate(snap(now, [row], events={"10987": BENEDITA_EVENTS},
                            today={"10987": 81}), CFG, {})
    note = by_id(exps)["source:10987:2026-10-10"]
    assert note.severity == "info" and "2 of 4" in note.evidence
    assert plan(exps, {}, now, POLICY, racing_now=True)[0] == []


# --- alert policy -----------------------------------------------------------

def _missed_results(now):
    # event 1 is thin and no longer the newest (event 2 is complete)
    row = reg(last_ingest_at=iso(now - 3600), events_published=5, events_with_results=5,
              coverage_at=iso(now - 30))
    return evaluate(snap(now, [row], events={"11113": [ev(1, 20, 1), ev(2, 20, 20)]}), CFG, {})[0]


def test_new_then_reminder_every_30_minutes_then_resolved_once():
    t = at("16:00")
    n1, s = plan(_missed_results(t), {}, t, POLICY, True)
    n2, s = plan(_missed_results(t + 600), s, t + 600, POLICY, True)
    n3, s = plan(_missed_results(t + 1800), s, t + 1800, POLICY, True)
    assert [n.kind for n in n1] == ["new"] and n2 == [] and [n.kind for n in n3] == ["reminder"]
    ok_row = reg(events_published=5, events_with_results=5, coverage_at=iso(t + 2000))
    ok, _ = evaluate(snap(t + 2000, [ok_row]), CFG, {})
    n4, s = plan(ok, s, t + 2000, POLICY, True)
    assert [n.kind for n in n4] == ["resolved"] and s["open"] == {}
    assert "RESOLVED" in render(n4, t + 2000)
    n5, s = plan(ok, s, t + 2300, POLICY, True)
    assert n5 == []


def test_racing_alerts_close_when_racing_hours_end():
    t = at("21:50")
    _, s = plan(_missed_results(t), {}, t, POLICY, True)
    notices, s = plan(_missed_results(at("22:05")), s, at("22:05"), POLICY, racing_now=False)
    assert [n.kind for n in notices] == ["closed"] and "racing hours are over" in notices[0].reason
    notices, s = plan(_missed_results(at("23:00")), s, at("23:00"), POLICY, racing_now=False)
    assert notices == []


def test_an_alert_whose_meet_disappeared_is_closed():
    t = at("16:00")
    _, s = plan(_missed_results(t), {}, t, POLICY, True)
    exps, _ = evaluate(snap(t + 300, []), CFG, {})
    notices, s = plan(exps, s, t + 300, POLICY, True)
    assert [n.kind for n in notices] == ["closed"] and s["open"] == {}


def test_worse_severity_reminds_at_once():
    from watchdog.expectations import Expectation
    t = at("16:00")
    warn = Expectation("x:1", "first_results", "M", "e", None, "missed", "warn", "a")
    crit = Expectation("x:1", "first_results", "M", "e", None, "missed", "crit", "b")
    _, s = plan([warn], {}, t, POLICY, True)
    notices, _ = plan([crit], s, t + 60, POLICY, True)
    assert [n.kind for n in notices] == ["reminder"] and notices[0].reason == "now worse"


# --- investigation ----------------------------------------------------------

class Clock:
    def __init__(self, step=0.0):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


SILVES_LOG = "\n".join([
    "=== track_pdf_meet https://live.swimrankings.net/11113/ ===",
    "Published ResultList PDFs: events [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] (updated: x)",
    "[tick] events=10 results=+0 unchanged=10 errors=0 new_stubs=0"])


def fake_probes(**kw):
    base = dict(ps=lambda: "351334 01:00 /usr/bin/python3 track_pdf_meet.py --meet-url "
                           "https://live.swimrankings.net/11113/ --live\n9 00:01 other 11113",
                read_log_tail=lambda sid: SILVES_LOG,
                fetch_page=lambda sid: "".join(f'<a href="ResultList_{i}.pdf">' for i in range(1, 12)),
                supervise_tail=lambda: "ERROR db unavailable: ReadTimeout\nERROR db unavailable: ReadTimeout",
                clock=Clock())
    base.update(kw)
    return inv.Probes(**base)


def test_investigation_points_at_the_parser_for_silves():
    now = at("17:45")
    s = replay_snapshot(now)
    exps, _ = evaluate(s, CFG, {})
    out = inv.investigate(by_id(exps)["results:11113:2026-10-10"], s, fake_probes())
    assert "writer process running" in out["summary"]
    assert "swimrankings lists 11 result PDFs; MeetTrack has results for 5" in out["summary"]
    assert "parser" in out["summary"]
    titles = [t for t, _ in out["sections"]]
    assert "Writer process" in titles and "MeetTrack per-event counts" in titles
    procs = dict(out["sections"])["Writer process"]
    assert len(procs) == 1 and "351334" in procs[0]


def test_a_failing_probe_costs_only_its_own_line():
    now = at("17:45")
    s = replay_snapshot(now)
    exps, _ = evaluate(s, CFG, {})

    def boom(sid):
        raise TimeoutError("page slow")
    out = inv.investigate(by_id(exps)["results:11113:2026-10-10"], s, fake_probes(fetch_page=boom))
    assert any("probe failed: TimeoutError" in ln for _, lines in out["sections"] for ln in lines)
    assert out["summary"]


def test_the_time_budget_skips_remaining_probes():
    now = at("17:45")
    s = replay_snapshot(now)
    exps, _ = evaluate(s, CFG, {})
    out = inv.investigate(by_id(exps)["results:11113:2026-10-10"], s,
                          fake_probes(clock=Clock(step=30)), budget_s=40)
    assert any("time budget" in ln for _, lines in out["sections"] for ln in lines)


def test_db_investigation_reads_the_supervisor_log():
    now = at("14:10")
    _, mem = evaluate(down(now), CFG, {})
    exps, _ = evaluate(down(now + 300), CFG, mem)
    out = inv.investigate(by_id(exps)["db:reachable"], down(now + 300), fake_probes())
    assert "database is not answering" in out["summary"] and "db unavailable" in out["summary"]


# --- the run glue -------------------------------------------------------------

class Sender:
    def __init__(self, status="accepted"):
        self.status, self.sent = status, []

    def __call__(self, text):
        self.sent.append(text)
        return self.status


RUN_CFG = {**er.RUN_DEFAULTS, **POLICY}


def test_run_once_sends_one_direct_message_with_the_investigation_link(tmp_path):
    now = at("17:45")
    sender = Sender()
    res = er.run_once(now, CFG, RUN_CFG, {}, replay_snapshot(now), probes=fake_probes(),
                      sender=sender, inv_dir=tmp_path / "investigations")
    assert res["delivery"] == "accepted" and len(sender.sent) == 1
    files = list((tmp_path / "investigations").glob("*.md"))
    assert len(files) == 1 and "results-11113-2026-10-10" in files[0].name
    assert str(files[0]) in sender.sent[0] and "Checked:" in sender.sent[0]
    body = files[0].read_text()
    assert "# Investigation: results:11113:2026-10-10" in body and "Poller log" in body
    assert "results:11113:2026-10-10" in res["state"]["alerts"]["open"]
    # the next run, 5 minutes on, says nothing new
    res2 = er.run_once(now + 300, CFG, RUN_CFG, res["state"], replay_snapshot(now + 300),
                       probes=fake_probes(), sender=sender, inv_dir=tmp_path / "investigations")
    assert res2["notices"] == [] and len(sender.sent) == 1


def test_a_failed_send_is_retried_next_run(tmp_path):
    now = at("17:45")
    sender = Sender("failed")
    res = er.run_once(now, CFG, RUN_CFG, {}, replay_snapshot(now), probes=fake_probes(),
                      sender=sender, inv_dir=tmp_path)
    assert res["state"]["alerts"] == {}
    sender.status = "accepted"
    res2 = er.run_once(now + 300, CFG, RUN_CFG, res["state"], replay_snapshot(now + 300),
                       probes=fake_probes(), sender=sender, inv_dir=tmp_path)
    assert [n.kind for n in res2["notices"]] == ["new"] and len(sender.sent) == 2


def test_dry_run_writes_and_sends_nothing(tmp_path):
    now = at("17:45")
    sender = Sender()
    res = er.run_once(now, CFG, RUN_CFG, {}, replay_snapshot(now), probes=fake_probes(),
                      sender=sender, inv_dir=tmp_path / "inv", dry_run=True)
    assert sender.sent == [] and not (tmp_path / "inv").exists()
    out = er.format_dry_run(res, now)
    assert "[MISSED crit] results:11113:2026-10-10" in out and "WOULD SEND" in out
    assert "investigation for results:11113:2026-10-10" in out


def test_investigations_per_run_are_capped(tmp_path):
    now = at("17:45")
    rows = [reg(str(i), f"M{i}", ingest_status="failed") for i in range(5)]
    res = er.run_once(now, CFG, {**RUN_CFG, "max_investigations": 2}, {}, snap(now, rows),
                      probes=fake_probes(), sender=Sender(), inv_dir=tmp_path)
    assert len(res["investigations"]) == 2
    assert sum(1 for n in res["notices"] if n.kind == "new") == 5   # one per writer, not per symptom


def test_snapshot_skips_meet_reads_when_the_probe_fails():
    calls = []

    class FakeRest:
        def get(self, path, timeout, count=False):
            calls.append((path, timeout))
            return None, "ReadTimeout"
    s = er.build_snapshot(at("14:00"), CFG, RUN_CFG, FakeRest(), ps=lambda: "")
    assert not s.db_ok and s.db_error == "ReadTimeout"
    assert len(calls) == 1 and calls[0][1] == RUN_CFG["db_timeout_s"]


def test_snapshot_reads_only_cheap_scoped_queries():
    calls = []
    now = at("17:45")

    class FakeRest:
        def get(self, path, timeout, count=False):
            calls.append((path, count))
            if path.startswith("meet_registry?select=sr_meet_id&limit=1"):
                return [{"sr_meet_id": "1"}], None
            if path.startswith("meet_registry"):
                return [reg()], None
            if path.startswith("meets?select=id"):
                return [{"id": "m1", "live_rankings_id": "sr-11113"}], None
            if path.startswith("events"):
                return [{"event_number": 1, "relay_count": 1, "results": [{"count": 2}],
                         "heats": [{"count": 9}], "due": [{"count": 4}]}], None
            if path.startswith("results"):
                return 2, None
            return [], None
    s = er.build_snapshot(now, CFG, RUN_CFG, FakeRest(),
                          ps=lambda: "7 01:00 track_pdf_meet.py https://live.swimrankings.net/11113/")
    assert s.events["11113"] == [{"event_number": 1, "id": None, "session_number": None,
                                  "day_time": None, "relay_count": 1, "results": 2, "entries": 9,
                                  "entries_due": 4}]
    # due: heats past their slot by heat_due_min (25), or with no slot; 17:45
    # Lisbon is 16:45 UTC in October
    ev_q = [p for p, _ in calls if p.startswith("events")][0]
    assert "due:heats(count)" in ev_q
    assert "&due.or=(scheduled_at.is.null,scheduled_at.lte.2026-10-10T16:20:00Z)" in ev_q
    assert s.results_today["11113"] == 2 and s.processes["11113"]
    reg_q = [p for p, _ in calls if p.startswith("meet_registry?select=sr_meet_id,name")][0]
    assert "nation=in.(POR)" in reg_q
    assert all("meet_id=eq." in p for p, c in calls if p.startswith("results"))
    assert all(p.split("?")[0] in ("meet_registry", "meets", "events", "results") for p, _ in calls)


def test_narrative_is_spawned_detached_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret")
    seen = {}

    def popen(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
    assert er.spawn_narrative("/x/inv.md", RUN_CFG, tmp_path, popen=popen)
    assert seen["argv"][:3] == ["timeout", "--kill-after=30", str(RUN_CFG["ai_timeout_s"] + 60)]
    assert "--narrate" in seen["argv"] and seen["kw"]["start_new_session"] is True
    assert "SUPABASE_SERVICE_ROLE_KEY" not in seen["kw"]["env"]
    assert "TELEGRAM_BOT_TOKEN" not in seen["kw"]["env"]


def test_narrate_appends_and_sends_a_follow_up(tmp_path, monkeypatch):
    monkeypatch.setattr(er, "BASE", er.Path(__file__).resolve().parents[1])
    f = tmp_path / "inv.md"
    f.write_text("# Investigation: results:11113\n")

    class R:
        stdout = '{"result": "Likely cause: parser\\nNext step: check ResultList_1.pdf"}'
    sent = []
    er.narrate(str(f), RUN_CFG, tmp_path, runner=lambda *a, **k: R(), sender=sent.append)
    assert "## AI narrative" in f.read_text() and "Likely cause" in sent[0]


def test_narrate_timeout_is_recorded_not_raised(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(er, "BASE", er.Path(__file__).resolve().parents[1])
    f = tmp_path / "inv.md"
    f.write_text("# Investigation\n")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("claude", 300)
    sent = []
    assert er.narrate(str(f), RUN_CFG, tmp_path, runner=slow, sender=sent.append) == 0
    assert "unavailable: TimeoutExpired" in f.read_text() and sent == []


def test_a_held_run_lock_skips_the_run(tmp_path):
    a, b = er.RunLock(tmp_path / "expect.lock"), er.RunLock(tmp_path / "expect.lock")
    assert a.acquire() and not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


def test_monitors_toml_carries_the_expect_section():
    cfg, run_cfg = er.load_config(er.Path(__file__).resolve().parents[1] / "watchdog" / "monitors.toml")
    assert cfg["nations"] == ["POR"] and cfg["results_lag_min"] == 20
    assert run_cfg["db_timeout_s"] == 8 and run_cfg["repeat_crit_min"] == 60
    assert "db_timeout_s" not in cfg and "enabled" not in cfg


def test_a_stopped_writer_is_one_alert_not_three():
    now = at("17:45")
    row = reg(last_ingest_at=iso(now - 3600), events_published=5, events_with_results=3,
              coverage_at=iso(now - 40 * 60))
    exps, _ = evaluate(snap(now, [row], events={"11113": [ev(1, 20, 1), ev(2, 20, 20)]},
                            procs={"11113": []}), CFG, {})
    e = by_id(exps)
    assert e["writer:11113:2026-10-10"].status == "missed"
    assert e["results:11113:2026-10-10"].status == "unknown"
    notices, _ = plan(exps, {}, now, POLICY, True)
    assert [n.id for n in notices] == ["writer:11113:2026-10-10"]


# --- the cron wrapper ---------------------------------------------------------

import json
import os
import subprocess
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[1] / "watchdog" / "run-expect.sh"


def _wrapper_env(tmp_path):
    fake = tmp_path / "fake-tg-send"
    fake.write_text('#!/usr/bin/env bash\ncat >> "$(dirname "$0")/sent.txt"\n'
                    'echo --- >> "$(dirname "$0")/sent.txt"\n')
    fake.chmod(0o755)
    return {**os.environ, "WATCHDOG_TG_SEND": str(fake),
            "WATCHDOG_ENV_FILE": str(tmp_path / "no.env"),
            "WATCHDOG_LOG_DIR": str(tmp_path / "logs"), "SUPABASE_SERVICE_ROLE_KEY": ""}


def _wrap(env, *args):
    return subprocess.run(["bash", str(WRAPPER), *args], env=env, capture_output=True,
                          text=True, timeout=60)


def test_wrapper_pages_once_an_hour_when_the_monitor_crashes(tmp_path):
    env = _wrapper_env(tmp_path)
    bad = tmp_path / "state-is-a-dir"
    bad.mkdir()
    env["WATCHDOG_EXPECT_STATE"] = str(bad)          # saving fails -> python exits 1
    r1, r2 = _wrap(env), _wrap(env)
    assert r1.returncode == 1 and r2.returncode == 1
    sent = (tmp_path / "sent.txt").read_text()
    assert sent.count("could not run") == 1 and "NOT being checked" in sent


def test_wrapper_without_a_key_counts_db_failures_and_pages_on_the_second(tmp_path):
    env = _wrapper_env(tmp_path)
    env["WATCHDOG_EXPECT_STATE"] = str(tmp_path / "state.json")
    r = _wrap(env)
    assert r.returncode == 0, r.stdout + r.stderr
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["memory"]["db_failures"] == 1 and "last_run" in state
    assert not (tmp_path / "sent.txt").exists()
    _wrap(env)
    sent = (tmp_path / "sent.txt").read_text()
    assert "database not answering" in sent and "SUPABASE_SERVICE_ROLE_KEY not set" in sent


def test_wrapper_retries_a_failed_crash_page_on_the_next_run(tmp_path):
    env = _wrapper_env(tmp_path)
    flaky = tmp_path / "flaky-tg-send"
    flaky.write_text('#!/usr/bin/env bash\nd="$(dirname "$0")"\n'
                     'if [ ! -f "$d/failed-once" ]; then touch "$d/failed-once"; exit 1; fi\n'
                     'cat >> "$d/sent.txt"\n')
    flaky.chmod(0o755)
    env["WATCHDOG_TG_SEND"] = str(flaky)
    bad = tmp_path / "dir-state"
    bad.mkdir()
    env["WATCHDOG_EXPECT_STATE"] = str(bad)
    _wrap(env)
    assert not (tmp_path / "sent.txt").exists()
    _wrap(env)
    assert "could not run" in (tmp_path / "sent.txt").read_text()


# --- review round 1: partial blindness, ids, one process matcher -------------

from watchdog.expectations import valid_sid, writer_lines

PS = """\
 351232 01:00 /usr/bin/python3 track_pdf_meet.py --meet-url https://live.swimrankings.net/11185/ --live
 351300 01:00 /usr/bin/python3 lastheat_ingest.py --meet-url https://live.swimrankings.net/10987/ --live --ingest
 351301 01:00 /usr/bin/python3 reconcile_meet.py --sr-meet-id 10976
 351302 01:00 /usr/bin/python3 poller.py --feed lenex --meet-url https://live.swimrankings.net/111850/ --live
 351303 01:00 vim notes-11185.txt
"""


def test_one_writer_matcher_knows_every_supervisor_form():
    assert len(writer_lines(PS, "11185")) == 1                 # not 111850, not vim
    assert "lastheat_ingest" in writer_lines(PS, "10987")[0]
    assert "reconcile_meet" in writer_lines(PS, "10976")[0]
    assert writer_lines(PS, "../etc") == []


def test_meet_ids_are_validated():
    assert valid_sid("11113") and valid_sid(11113)
    assert not any(valid_sid(x) for x in ("", "11113/../x", "../", "12a", None, True, "1" * 12))


def test_an_invalid_registry_id_is_reported_not_used():
    now = at("15:00")
    rows = [reg(), reg("../../etc", "Bad")]
    exps, _ = evaluate(snap(now, rows), CFG, {})
    e = by_id(exps)["db:reads"]
    assert e.status == "missed" and "not meet ids" in e.evidence
    assert not any(x.sr_meet_id == "../../etc" for x in exps)


def test_failed_per_meet_reads_are_unknown_not_a_false_miss():
    now = at("17:30")
    s = snap(now, [reg(coverage_at=iso(now - 30))])
    s.read_errors = {"11113": ["results today: ReadTimeout"]}
    exps, mem = evaluate(s, CFG, {})
    e = by_id(exps)
    assert e["first:11113:2026-10-10"].status == "unknown"
    assert e["db:reads"].status == "pending"
    exps, mem = evaluate(s, CFG, mem)
    reads = by_id(exps)["db:reads"]
    assert reads.status == "missed" and "ReadTimeout" in reads.evidence
    notices, _ = plan(exps, {}, now, POLICY, True)
    assert [n.id for n in notices] == ["db:reads"]
    assert "some database reads failing" in render(notices, now)


def test_snapshot_records_read_failures():
    class FakeRest:
        def get(self, path, timeout, count=False):
            if path.startswith("meet_registry?select=sr_meet_id&limit=1"):
                return [{"sr_meet_id": "1"}], None
            if path.startswith("meet_registry"):
                return [reg(), reg("x/../y")], None
            if path.startswith("meets?select=id"):
                return [{"id": "m1", "live_rankings_id": "sr-11113"}], None
            if path.startswith("events"):
                return None, "HTTP 500"
            if path.startswith("results"):
                return None, "ReadTimeout"
            return [], None
    s = er.build_snapshot(at("17:00"), CFG, RUN_CFG, FakeRest(), ps=lambda: "")
    assert s.read_errors["11113"] == ["events: HTTP 500", "results today: ReadTimeout"]
    assert set(s.processes) == {"11113"}


def test_log_tail_refuses_ids_that_are_not_meet_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(er, "SPLASH_LOGS", tmp_path)
    (tmp_path / "poller-11113.log").write_text("hello\n")
    probes = er.real_probes()
    assert probes.read_log_tail("11113") == "hello\n"
    with pytest.raises(ValueError):
        probes.read_log_tail("../secret")


def test_partial_read_failures_do_not_freeze_closing():
    t = at("16:00")
    _, s = plan(_missed_results(t), {}, t, POLICY, True)
    from watchdog.expectations import Expectation
    reads = Expectation("db:reads", "db", None, "e", None, "missed", "crit", "events: HTTP 500",
                        window="always")
    probe = Expectation("db:reachable", "db", None, "e", None, "met", "crit", "ok", window="always")
    notices, s = plan([probe, reads], s, t + 300, POLICY, True)
    kinds = {(n.kind, n.id) for n in notices}
    assert ("closed", "results:11113:2026-10-10") in kinds and ("new", "db:reads") in kinds


def test_only_met_resolves_pending_keeps_and_n_a_closes():
    from watchdog.expectations import Expectation
    t = at("16:00")
    missed = Expectation("startlist:1", "startlist", "M", "e", None, "missed", "warn", "x",
                         window="always")
    _, s = plan([missed], {}, t, POLICY, True)
    pending = Expectation("startlist:1", "startlist", "M", "e", None, "pending", "warn", "y",
                          window="always")
    notices, s = plan([pending], s, t + 300, POLICY, True)
    assert notices == [] and "startlist:1" in s["open"]
    na = Expectation("startlist:1", "startlist", "M", "e", None, "n/a", "info", "z",
                     window="always")
    notices, s = plan([na], s, t + 600, POLICY, True)
    assert [n.kind for n in notices] == ["closed"] and s["open"] == {}


def test_the_ai_narrative_is_off_by_default():
    assert er.RUN_DEFAULTS["ai_narrative"] is False
    _, run_cfg = er.load_config(er.Path(__file__).resolve().parents[1] / "watchdog" / "monitors.toml")
    assert run_cfg["ai_narrative"] is False


def test_wrapper_dry_run_never_sends(tmp_path):
    env = _wrapper_env(tmp_path)
    bad = tmp_path / "d"
    bad.mkdir()
    env["WATCHDOG_EXPECT_STATE"] = str(bad)
    r = _wrap(env, "--dry-run")
    assert r.returncode == 0 and "dry-run" in r.stdout
    assert not (tmp_path / "sent.txt").exists()


def test_startlist_scope_all_skips_meets_auto_ingest_does_not_fetch():
    import datetime as _dt
    from zoneinfo import ZoneInfo
    from watchdog import expectations as ex
    tz = ZoneInfo("Europe/Lisbon")
    now = _dt.datetime(2026, 10, 10, 18, 0, tzinfo=tz).timestamp()
    cfg = dict(ex.DEFAULTS); cfg["startlist_scope"] = "all"
    snap = ex.Snapshot(now=now, db_ok=True, db_latency_s=0.1, registry=[])
    base = {"sr_meet_id": "1", "name": "M", "nation": "POR", "start_date": "2026-10-11",
            "end_date": "2026-10-11", "ingest_status": "discovered"}
    for extra, word in (({"listed": False}, "unlisted"), ({"dispatch_paused": True}, "paused"),
                        ({"feed_type": "lenex"}, "Lenex")):
        out = ex.eval_startlist({**base, **extra}, snap, cfg, tz)
        assert out[0].status == "n/a" and word in out[0].evidence
    out = ex.eval_startlist({**base, "listed": True, "feed_type": "pdf"}, snap, cfg, tz)
    assert out[0].status in ("missed", "pending")


# --- a meet on another SPLASH host (splash_poller meet coverage, 2026-10-26) --
from watchdog.expectations import label, valid_sid, writer_lines

SPK = "sp-anlisboa-anl02-2627-fajjs"
ANL = "http://anlisboa.info/results/2026-27/anl02-2627-fajjs/"


@pytest.mark.parametrize("sid, ok", [
    (SPK, True), ("11113", True), ("sp-a b", False), ("sp-", False), ("sp-x/../y", False),
    ("sp-" + "a" * 61, False), ("1234567890", False), (None, False)])
def test_valid_sid_accepts_splash_keys(sid, ok):
    assert valid_sid(sid) is ok


def test_writer_lines_finds_a_splash_meets_writers():
    ps = "\n".join([
        f"7 01:00 /usr/bin/python3 track_pdf_meet.py --meet-url {ANL} --live",
        f"8 00:02 /usr/bin/python3 reconcile_meet.py --sr-meet-id {SPK}",
        "9 00:10 /usr/bin/python3 track_pdf_meet.py --meet-url http://anlisboa.info/results/2026-27/anl05-2627-x/ --live",
    ])
    assert len(writer_lines(ps, SPK, meet_url=ANL)) == 2
    assert len(writer_lines(ps, SPK)) == 2                 # the key alone names its folder


def test_a_splash_meet_is_labelled_by_its_own_id():
    assert label(reg(SPK, city="Setúbal")) == f"Setúbal ({SPK})"
    assert label(reg()) == "Silves (sr-11113)"


def test_an_unlaunched_splash_meet_is_missed_like_any_meet():
    row = reg(SPK, city="Setúbal", ingest_status="discovered", meet_url=ANL)
    e = by_id(evaluate(snap(at("08:31"), [row], procs={SPK: []}), CFG, {})[0])[f"writer:{SPK}:2026-10-10"]
    assert e.status == "missed" and "not launched" in e.evidence


def test_snapshot_reads_a_splash_meet_by_its_own_ids():
    calls = []

    class FakeRest:
        def get(self, path, timeout, count=False):
            calls.append(path)
            if path.startswith("meet_registry?select=sr_meet_id&limit=1"):
                return [{"sr_meet_id": "1"}], None
            if path.startswith("meet_registry"):
                return [reg(SPK, city="Setúbal", meet_url=ANL)], None
            if path.startswith("meets?select=id"):
                return [{"id": "m1", "live_rankings_id": SPK}], None
            if path.startswith("events"):
                return [], None
            if path.startswith("results"):
                return 0, None
            return [], None
    s = er.build_snapshot(at("17:45"), CFG, RUN_CFG, FakeRest(),
                          ps=lambda: f"7 01:00 track_pdf_meet.py --meet-url {ANL} --live")
    assert any(f"live_rankings_id=in.({SPK})" in p for p in calls)
    assert "meet_url" in [p for p in calls if p.startswith("meet_registry?select=sr_meet_id,name")][0]
    assert s.meets[SPK]["id"] == "m1" and s.processes[SPK]


def test_the_page_probe_reads_a_splash_meets_own_folder(monkeypatch):
    import requests
    seen = []

    class R:
        text = "ok"

        def raise_for_status(self):
            pass
    monkeypatch.setattr(requests, "get", lambda url, **k: seen.append(url) or R())
    probes = er.real_probes(urls={SPK: ANL, "bad": "file:///etc/passwd"})
    assert probes.fetch_page(SPK) == "ok" and seen == [ANL]
    with pytest.raises(ValueError):
        probes.fetch_page("sp-anlisboa-no-url")


def test_writer_lines_matches_by_the_key_the_folder_makes_even_with_hyphens():
    # A host label with a hyphen: the key cannot be split back at its first '-'.
    sid = "sp-provas-annp-52"
    ps = "\n".join([
        "7 01:00 python3 track_pdf_meet.py --meet-url https://provas-annp.pt/provas2526/52/ --live",
        "8 01:00 python3 track_pdf_meet.py --meet-url https://provas-annp.pt/provas2526/53/ --live",
    ])
    assert writer_lines(ps, sid) == [ps.splitlines()[0].strip()]


def test_a_key_with_a_trailing_newline_is_not_valid():
    assert not valid_sid(SPK + "\n")


@pytest.mark.parametrize("url", ["http://user:pw@anlisboa.info/results/x/",
                                 "http://anlisboa.info:8080/results/x/"])
def test_the_page_probe_refuses_credentials_and_ports(url):
    probes = er.real_probes(urls={SPK: url})
    with pytest.raises(ValueError):
        probes.fetch_page(SPK)


def test_the_page_probe_does_not_follow_redirects(monkeypatch):
    import requests
    seen = {}

    class R:
        text = "ok"

        def raise_for_status(self):
            pass
    monkeypatch.setattr(requests, "get", lambda url, **k: seen.update(k) or R())
    er.real_probes(urls={SPK: ANL}).fetch_page(SPK)
    assert seen.get("allow_redirects") is False


@pytest.mark.parametrize("url, key", [
    # The same keys splash_poller's tests pin for meet_config.from_meet_url.
    (ANL, SPK),
    ("http://anlisboa.info/results/2026-27/anl02-2627-fajjs", SPK),
    ("https://provas.annp.pt/provas2526/52/", "sp-provas-52"),
    ("https://ancnp.pt/Resultados/Natacao_Pura/25-26/23_XXXV_Torneio_Cidade_Estarreja/",
     "sp-ancnp-23_xxxv_torneio_cidade_estarreja"),
    ("ftp://anlisboa.info/x/", None),
])
def test_splash_key_for_matches_splash_poller(url, key):
    from watchdog.expectations import splash_key_for
    assert splash_key_for(url) == key


# --- the 7 Nov 2026 replay: alerts that mean something is wrong ----------------
#
# Leiria (sr-11115) opened "results" at 10:00 and reminded 7 times while the writer
# was healthy: gap_since stayed at 09:36 because some event, often the newest one
# still being swum, was always a little behind. Events 2 and 16 also had a second,
# empty session row under the same number. Alapraia (sr-11531) paged at 17:00
# although its host had published nothing; Ponte da Barca (sr-11334) paged at 17:00
# and was a real failure (no meet row, so the writer could write nothing).

from watchdog.expectations import in_racing_window

D7, D8 = "2026-11-07", "2026-11-08"
LIVE_POLICY = {"repeat_crit_min": 60, "repeat_warn_min": 60, "repeat_startlist_min": 360}

# start-list entries per event number (the larger row where a number has two)
LEIRIA_ENTRIES = {1: 19, 2: 16, 3: 14, 4: 8, 5: 20, 6: 3, 7: 4, 8: 12, 9: 0, 10: 3, 11: 22,
                  12: 0, 13: 25, 14: 45, 15: 24, 16: 16, 17: 17, 18: 16, 19: 8, 20: 8,
                  21: 12, 22: 4, 23: 14, 24: 3, 25: 0, 26: 3, 27: 10, 28: 11, 29: 35,
                  30: 39, 31: 14, 32: 16}
LEIRIA_RELAYS = {1, 17}
LEIRIA_SHARED = {2: 8, 16: 8, 18: 8, 32: 8}      # the second, empty session row

# What the poller log (splash_poller logs/poller-11115.log) shows on 7 Nov: when
# each ResultList first appeared, and every result write (event, total after it).
# The host published most files empty and filled them later; event 2 stayed at
# 4 rows until 16:10 and event 16 (the newest) at 5 until 17:26. 494 written by 16:10.
LEIRIA_PUBLISHED = {1: "09:34", 2: "09:36", 3: "09:47", 4: "09:56", 5: "10:08", 6: "10:20",
                    7: "10:26", 8: "10:38", 9: "10:44", 10: "10:47", 11: "11:00", 12: "11:08",
                    13: "11:12", 14: "11:19", 15: "11:26", 16: "11:29"}
LEIRIA_WRITES = [("09:34", 1, 20), ("09:36", 2, 4), ("10:20", 6, 1), ("10:38", 8, 1),
                 ("10:44", 9, 15), ("11:00", 11, 1), ("11:08", 12, 18), ("11:29", 16, 5),
                 ("11:46", 3, 32), ("11:48", 4, 32), ("12:03", 5, 43), ("12:03", 6, 25),
                 ("12:03", 7, 27), ("12:14", 8, 35), ("12:15", 10, 27), ("12:39", 11, 41),
                 ("12:47", 13, 46), ("13:10", 14, 68), ("13:20", 15, 44), ("13:52", 11, 43),
                 ("16:10", 2, 14), ("17:26", 16, 16)]


def hhmm(epoch):
    return datetime.fromtimestamp(epoch, tz=LIS).strftime("%H:%M")


def leiria_snapshot(now, published_at, writes, day_times=None, shared_slot=f"{D7}T15:15:00"):
    res, last = {}, None
    for when, n, total in writes:
        t = at(when, D7)
        if t <= now:
            res[n] = total
            last = t if last is None else max(last, t)
    pub = [n for n, when in published_at.items() if at(when, D7) <= now]
    events = []
    for n, ent in LEIRIA_ENTRIES.items():
        events.append(ev(n, ent, res.get(n, 0), relay=4 if n in LEIRIA_RELAYS else 1,
                         day_time=(day_times or {}).get(n)))
        if n in LEIRIA_SHARED:
            # 7 Nov: the empty rows of events 2 and 16 sat in the afternoon session
            # ("events 2, 16 ... first was due at 16:00"); 18 and 32 race on day two.
            slot = shared_slot if day_times and n in (2, 16) else None
            events.append(ev(n, LEIRIA_SHARED[n], 0, day_time=slot))
    row = reg("11115", "Leiria", start_date=D7, end_date=D8, events_published=len(pub),
              events_with_results=sum(1 for n in pub if res.get(n, 0) > 0),
              coverage_at=iso(now - 30), last_ingest_at=iso(last) if last else None)
    return snap(now, [row], events={"11115": events}, today={"11115": sum(res.values())})


def run_day(make, start, end, day=D7, policy=LIVE_POLICY, cfg=CFG, step=300):
    """Every 5-minute run of the monitor over a day: (HH:MM, kind, id) per notice."""
    mem, state, sent = {}, {}, []
    t = at(start, day)
    while t <= at(end, day):
        exps, mem = evaluate(make(t), cfg, mem)
        notices, state = plan(exps, state, t, policy, in_racing_window(t, cfg))
        sent += [(hhmm(t), n.kind, n.id, n.reason) for n in notices]
        t += step
    return sent, mem, state


def flowing_day(stuck=()):
    """A healthy Leiria-shaped morning: event k is published 7 min after event k-1
    with a few results, and is complete 10 min after the next one appears. The
    newest, event 16, then sits at 5 of 16 until 17:26. `stuck` events never get
    a result (a parser that reads nothing from their file)."""
    published = {k: hhmm(at("09:30", D7) + 7 * 60 * (k - 1)) for k in range(1, 17)}
    writes = []
    for k in range(1, 16):
        if k in stuck:
            continue
        ent = max(LEIRIA_ENTRIES[k], 10)
        writes.append((published[k], k, max(1, ent // 4)))
        writes.append((hhmm(at(published[k + 1], D7) + 600), k, ent))
    writes += [(published[16], 16, 5), ("17:26", 16, 16)]
    writes.sort(key=lambda w: w[0])
    slots = {k: f"{D7}T{published[k]}:00" for k in published}
    return published, writes, slots


def test_leiria_healthy_flow_with_a_lagging_newest_event_sends_nothing():
    published, writes, slots = flowing_day()
    sent, _, _ = run_day(lambda t: leiria_snapshot(t, published, writes, slots), "09:00", "18:00")
    assert [s for s in sent if s[2].startswith(("results:", "schedule:"))] == []


def test_leiria_newest_event_in_progress_is_shown_not_paged():
    published, writes, slots = flowing_day()
    now = at("16:25", D7)
    exps, _ = evaluate(leiria_snapshot(now, published, writes, slots), CFG,
                       {"last_run": now - 300})
    e = by_id(exps)[f"results:11115:{D7}"]
    assert e.status == "met"
    assert "event 16, the newest, is in progress (5 of 16)" in e.evidence


def test_leiria_replay_alerts_once_and_reminds_only_when_nothing_moves():
    sent, mem, state = run_day(
        lambda t: leiria_snapshot(t, LEIRIA_PUBLISHED, LEIRIA_WRITES), "09:00", "18:00")
    results = [s[:3] for s in sent if s[2] == f"results:11115:{D7}"]
    # Event 2 (4 of 16 since 09:36, superseded at 09:47) is late from 10:07. While
    # results keep arriving (until 13:52) nothing repeats. From 13:52 to 16:10
    # nothing is written at all and event 2 does not move: that is stuck.
    assert results == [("10:10", "new", f"results:11115:{D7}"),
                       ("14:55", "reminder", f"results:11115:{D7}"),
                       ("15:55", "reminder", f"results:11115:{D7}"),
                       ("16:10", "resolved", f"results:11115:{D7}")]
    reasons = [s[3] for s in sent if s[1] == "reminder"]
    assert all(r.startswith("nothing new for") for r in reasons)
    assert state["open"] == {}


def test_leiria_replay_evidence_names_the_late_events_not_a_morning_gap():
    now = at("10:10", D7)
    s = leiria_snapshot(now, LEIRIA_PUBLISHED, LEIRIA_WRITES)
    mem = {"last_run": now - 300,
           "late_events": {f"results:11115:{D7}": {"2": {"since": at("09:50", D7), "results": 4},
                                                  "3": {"since": at("10:00", D7), "results": 0},
                                                  "4": {"since": at("10:10", D7), "results": 0}}}}
    exps, mem = evaluate(s, CFG, mem)
    e = by_id(exps)[f"results:11115:{D7}"]
    assert e.status == "missed"
    assert "event 2" in e.evidence and "4 results for 16 entries" in e.evidence
    assert "event 5, the newest, is in progress (no results yet)" in e.evidence
    assert "short since 09:50" in e.evidence


def test_a_truly_stuck_event_alerts_and_reminds_once_the_meet_stops_moving():
    published, writes, slots = flowing_day(stuck={3})
    sent, _, state = run_day(lambda t: leiria_snapshot(t, published, writes), "09:00", "15:00")
    results = [s[:2] for s in sent if s[2] == f"results:11115:{D7}"]
    # event 3 is superseded at 09:51: late at 10:11, paged at the 10:15 run.
    assert results[0] == ("10:15", "new")
    # results keep arriving until 11:25: no reminder until an hour after that
    reminders = [t for t, kind in results if kind == "reminder"]
    assert reminders and reminders[0] == "12:25"
    assert reminders == ["12:25", "13:25", "14:25"]
    assert f"results:11115:{D7}" in state["open"]


def test_a_meet_that_is_stuck_everywhere_reminds_every_hour():
    now = at("12:00", D7)
    rows = [reg("11115", "Leiria", start_date=D7, end_date=D8, events_published=6,
                events_with_results=2, coverage_at=iso(now - 30),
                last_ingest_at=iso(at("09:40", D7)))]
    events = [ev(1, 20, 20), ev(2, 20, 20), ev(3, 20, 0), ev(4, 20, 0), ev(5, 20, 0), ev(6, 20, 0)]

    def make(t):
        rows[0]["coverage_at"] = iso(t - 30)
        return snap(t, rows, events={"11115": events}, today={"11115": 40})
    sent, _, _ = run_day(make, "10:00", "13:05")
    kinds = [(s[0], s[1]) for s in sent if s[2] == f"results:11115:{D7}"]
    assert kinds == [("10:20", "new"), ("11:20", "reminder"), ("12:20", "reminder")]


def test_event_rows_sharing_a_number_are_one_event():
    # 17:45 on 7 Nov: events 2 and 16 each have a second, empty session row.
    now = at("17:45", D7)
    published, writes, slots = flowing_day()
    s = leiria_snapshot(now, published, writes, slots)
    exps, _ = evaluate(s, CFG, {"last_run": now - 300})
    e = by_id(exps)
    assert e[f"schedule:11115:{D7}"].status == "met"
    assert e[f"results:11115:{D7}"].status == "met"
    from watchdog.expectations import by_event_number
    groups = {g["event_number"]: g for g in by_event_number(s.events["11115"])}
    assert groups[2]["results"] == 16 and groups[2]["entries"] == 16 and groups[2]["rows"] == 2


def alapraia(now, **kw):
    row = reg("11531", "Alapraia", start_date=D7, end_date=D7, events_published=0,
              events_with_results=0, coverage_at=iso(now - 30), last_ingest_at=None)
    row.update(kw)
    events = [ev(n, ent, 0, relay=4) for n, ent in enumerate([7, 10, 8, 10, 1, 8], start=1)]
    return snap(now, [row], events={"11531": events})


def test_alapraia_host_with_nothing_published_and_no_start_time_is_not_an_alert():
    sent, _, _ = run_day(alapraia, "08:00", "21:55")
    assert sent == []
    exps, _ = evaluate(alapraia(at("17:05", D7)), CFG, {})
    e = by_id(exps)[f"first:11531:{D7}"]
    assert e.status == "pending" and "the host has published no results yet" in e.evidence


def test_a_silent_host_after_a_known_start_alerts_only_long_after_it():
    # Abrantes (sr-11511): the start list says 09:00, the host published its
    # first result about 11:10. That is the host, not MeetTrack: no page at 10:00.
    def make(t):
        s = alapraia(t)
        for e in s.events["11531"]:
            e["day_time"] = f"{D7}T09:00:00"
        return s
    exps, mem = evaluate(make(at("10:05", D7)), CFG, {})
    assert by_id(exps)[f"first:11531:{D7}"].status == "pending"
    exps, _ = evaluate(make(at("12:00", D7)), CFG, mem)
    e = by_id(exps)[f"first:11531:{D7}"]
    assert e.status == "missed" and "no result files 180 min after" in e.evidence


def ponte_da_barca(now):
    # The writer runs, but there is no meets row: it cannot tick, so it never
    # counts the host's files (counters NULL) and writes nothing.
    row = reg("11334", "Ponte da Barca", start_date=D7, end_date=D7, events_published=None,
              events_with_results=None, coverage_at=None, last_ingest_at=None)
    return snap(now, [row], procs={"11334": ["2727722 20:00 track_pdf_meet.py --meet-url "
                                             "https://live.swimrankings.net/11334/ --live"]})


def test_ponte_da_barca_unmeasured_host_and_no_results_still_alerts():
    sent, _, _ = run_day(ponte_da_barca, "16:40", "17:10")
    assert [s[:3] for s in sent] == [("17:00", "new", f"first:11334:{D7}")]
    exps, _ = evaluate(ponte_da_barca(at("17:00", D7)), CFG, {})
    e = by_id(exps)[f"first:11334:{D7}"]
    assert "has not reported the host's result list" in e.evidence
    assert "MeetTrack holds no events for this meet" in e.evidence


def test_first_results_fire_when_the_host_lists_files_and_meettrack_has_none():
    # The host lists one result file; the writer counts it, but nothing reaches
    # MeetTrack. Only one file, so it is the newest and results:* waits on it.
    def make(t):
        return alapraia(t, events_published=1 if t >= at("15:00", D7) else 0)
    sent, _, _ = run_day(make, "14:00", "16:30")
    assert [s[:3] for s in sent] == [("16:00", "new", f"first:11531:{D7}")]
    exps, _ = evaluate(make(at("16:00", D7)), CFG, {"files_seen:11531:" + D7: at("15:00", D7),
                                                    "baseline:11531:" + D7: 0})
    e = by_id(exps)[f"first:11531:{D7}"]
    assert "the host lists 1 result file" in e.evidence and "first seen 15:00" in e.evidence


def test_reminders_wait_while_the_problem_is_moving():
    from watchdog.expectations import Expectation
    t = at("12:00", D7)

    def exp(progress_at):
        return Expectation("results:1:d", "results", "M", "e", None, "missed", "crit", "x",
                           data={"progress_at": progress_at})
    _, s = plan([exp(t - 600)], {}, t, LIVE_POLICY, True)
    notices, s = plan([exp(t + 3000)], s, t + 3600, LIVE_POLICY, True)
    assert notices == []                               # results arrived 10 min ago
    notices, s = plan([exp(t + 3000)], s, t + 3000 + 3600, LIVE_POLICY, True)
    assert [n.kind for n in notices] == ["reminder"]
    assert notices[0].reason == "nothing new for 1h00"
    assert "nothing new for 1h00" in render(notices, t + 6600)


def test_investigation_counts_event_numbers_not_rows():
    now = at("17:45", D7)
    s = leiria_snapshot(now, LEIRIA_PUBLISHED, LEIRIA_WRITES)
    from watchdog.expectations import Expectation
    e = Expectation(f"results:11115:{D7}", "results", "Leiria (sr-11115)", "e", None, "missed",
                    "crit", "x", sr_meet_id="11115", day=D7)
    out = inv.investigate(e, s, fake_probes(fetch_page=lambda sid: ""))
    assert out["facts"]["events_with_results"] == 16          # not 16 + empty session rows
    table = dict(out["sections"])["MeetTrack per-event counts"]
    assert table[-1] == "(rows that share a number are judged as one event: 2, 16, 18, 32)"


def test_stale_counters_never_resolve_events_that_had_no_results():
    t = at("15:00")
    row = reg(events_published=4, events_with_results=2, coverage_at=iso(t - 30),
              last_ingest_at=iso(t - 60))
    events = {"11113": [ev(1, 10, 10), ev(2, 10, 0), ev(3, 10, 10), ev(4, 10, 0)]}
    exps, mem = evaluate(snap(t, [row], events=events), CFG, {})
    assert by_id(exps)["results:11113:2026-10-10"].status == "pending"     # event 2 is short
    row["coverage_at"] = iso(t - 45 * 60)                                  # writer stops counting
    exps, mem = evaluate(snap(t + 300, [row], events=events), CFG, mem)
    e = by_id(exps)["results:11113:2026-10-10"]
    assert e.status == "unknown" and "counters are older than 30 min" in e.evidence
    assert "2" in mem["late_events"]["results:11113:2026-10-10"]           # its clock is kept


# --- council round 1 (2026-11-07): stale state, restarts, unlisted events ------

def _stale_row(now, **kw):
    row = reg(events_published=4, events_with_results=2, coverage_at=iso(now - 45 * 60),
              last_ingest_at=iso(now - 50 * 60))
    row.update(kw)
    return row


FOUR = {"11113": [ev(1, 10, 10), ev(2, 10, 0), ev(3, 10, 10), ev(4, 10, 0)]}


def test_stale_counters_on_a_cold_start_are_unknown_never_met():
    # After a restart (no late_events memory) the old counters cannot show
    # which published events lack results: never "met" on them.
    now = at("15:00")
    exps, mem = evaluate(snap(now, [_stale_row(now)], events=FOUR), CFG, {})
    e = by_id(exps)["results:11113:2026-10-10"]
    assert e.status == "unknown" and "older than 30 min" in e.evidence
    # ...so an alert left open before the restart is neither resolved nor reminded
    state = {"open": {"results:11113:2026-10-10": {"kind": "results", "severity": "crit",
                                                    "first_missed": now - 3600,
                                                    "last_sent": now - 7200, "sends": 2}}}
    notices, state = plan(exps, state, now, POLICY, True)
    assert not [n for n in notices if n.id.startswith("results:")]
    assert "results:11113:2026-10-10" in state["open"]


def test_a_pdf_writer_that_has_not_counted_yet_is_unknown_for_results():
    now = at("15:00")
    row = reg(events_published=None, events_with_results=None, coverage_at=None)
    exps, _ = evaluate(snap(now, [row], events=FOUR), CFG, {})
    assert by_id(exps)["results:11113:2026-10-10"].status == "unknown"
    # a writer that never counts files (fragment, Lenex) is judged on its rows
    row = reg(feed_type="fragment", events_published=None, events_with_results=None,
              coverage_at=iso(now - 30))
    exps, _ = evaluate(snap(now, [row], events=FOUR), CFG, {})
    assert by_id(exps)["results:11113:2026-10-10"].status == "met"


def test_stale_to_fresh_recovery_resumes_the_frozen_clocks():
    t = at("15:00")
    row = reg(events_published=4, events_with_results=2, coverage_at=iso(t - 30),
              last_ingest_at=iso(t - 60))
    exps, mem = evaluate(snap(t, [row], events=FOUR), CFG, {})
    assert by_id(exps)["results:11113:2026-10-10"].status == "pending"     # event 2 since 15:00
    row["coverage_at"] = iso(t - 31 * 60)                                  # stale for 25 min
    for step in range(1, 6):
        exps, mem = evaluate(snap(t + step * 300, [row], events=FOUR), CFG, mem)
        assert by_id(exps)["results:11113:2026-10-10"].status == "unknown"
    row["coverage_at"] = iso(t + 1795)                                     # fresh again
    exps, mem = evaluate(snap(t + 1800, [row], events=FOUR), CFG, mem)
    e = by_id(exps)["results:11113:2026-10-10"]
    assert e.status == "missed" and "short since 15:00" in e.evidence      # clock kept


def test_counters_exactly_thirty_minutes_old_are_still_fresh():
    now = at("15:00")
    exps, _ = evaluate(snap(now, [_stale_row(now, coverage_at=iso(now - 1800))], events=FOUR),
                       CFG, {})
    assert by_id(exps)["results:11113:2026-10-10"].status == "pending"
    exps, _ = evaluate(snap(now, [_stale_row(now, coverage_at=iso(now - 1801))], events=FOUR),
                       CFG, {})
    assert by_id(exps)["results:11113:2026-10-10"].status == "unknown"


def test_an_open_racing_alert_that_is_not_judged_closes_at_the_end_of_racing():
    # Stale counters freeze the clock (unknown). At 22:00 the alert closes like
    # a missed one; it does not linger until the id changes at midnight.
    t = at("21:30")
    _, s = plan(_missed_results(t), {}, t, POLICY, True)
    row = _stale_row(at("21:55"))
    exps, _ = evaluate(snap(at("21:55"), [row], events=FOUR), CFG, {"last_run": at("21:50")})
    assert by_id(exps)["results:11113:2026-10-10"].status == "unknown"
    notices, s = plan(exps, s, at("21:55"), POLICY, True)
    assert not [n for n in notices if n.id.startswith("results:")]   # (the writer pages)
    notices, s = plan(exps, s, at("22:00"), POLICY, racing_now=False)
    closed = [n for n in notices if n.id.startswith("results:")]
    assert [(n.kind, n.id) for n in closed] == [("closed", "results:11113:2026-10-10")]
    assert "racing hours are over; not judged at close" in closed[0].reason
    assert s["open"] == {}


def test_a_sole_publication_missing_from_meettracks_event_list_is_the_newest():
    # The host lists 1 file, MeetTrack has no row for it: it is the newest
    # publication (being swum), so results:* waits; first:* covers it.
    t = at("15:00")
    row = reg(events_published=1, events_with_results=0, coverage_at=iso(t - 30))
    mem = {}
    for step in range(0, 13):                              # an hour
        now = t + step * 300
        row["coverage_at"] = iso(now - 30)
        exps, mem = evaluate(snap(now, [row]), CFG, mem)
        e = by_id(exps)["results:11113:2026-10-10"]
        assert e.status == "met", (step, e.evidence)
    assert "the newest" in e.evidence and "not in MeetTrack's event list" in e.evidence


def test_a_second_unlisted_publication_makes_the_first_one_late():
    t = at("15:00")
    row = reg(events_published=1, events_with_results=0, coverage_at=iso(t - 30))
    exps, mem = evaluate(snap(t, [row]), CFG, {})
    row.update(events_published=2, coverage_at=iso(t + 270))
    exps, mem = evaluate(snap(t + 300, [row]), CFG, mem)
    assert by_id(exps)["results:11113:2026-10-10"].status == "pending"
    row["coverage_at"] = iso(t + 1470)
    exps, mem = evaluate(snap(t + 1500, [row]), CFG, mem)
    e = by_id(exps)["results:11113:2026-10-10"]
    assert e.status == "missed" and "1 not in MeetTrack's event list" in e.evidence


def test_unlisted_clock_is_frozen_while_counters_are_stale_and_ends_when_they_clear():
    t = at("15:00")
    row = reg(events_published=2, events_with_results=0, coverage_at=iso(t - 30))
    exps, mem = evaluate(snap(t, [row]), CFG, {})
    row["coverage_at"] = iso(t - 40 * 60)                       # writer stops counting
    exps, mem = evaluate(snap(t + 300, [row]), CFG, mem)
    assert by_id(exps)["results:11113:2026-10-10"].status == "unknown"
    # the writer comes back and the events now have rows with results
    row.update(events_with_results=2, coverage_at=iso(t + 570))
    exps, mem = evaluate(snap(t + 600, [row], events={"11113": [ev(1, 10, 10), ev(2, 10, 9)]}),
                         CFG, mem)
    assert by_id(exps)["results:11113:2026-10-10"].status == "met"
    assert "results:11113:2026-10-10" not in mem["late_events"]


def test_investigation_entries_total_counts_each_event_number_once():
    now = at("17:45", D7)
    s = leiria_snapshot(now, LEIRIA_PUBLISHED, LEIRIA_WRITES)
    from watchdog.expectations import Expectation
    e = Expectation(f"results:11115:{D7}", "results", "Leiria (sr-11115)", "e", None, "missed",
                    "crit", "x", sr_meet_id="11115", day=D7)
    facts = inv.investigate(e, s, fake_probes(fetch_page=lambda sid: ""))["facts"]
    assert facts["entries_total"] == sum(LEIRIA_ENTRIES.values())            # not + 4 x 8
    assert facts["results_total"] == 505


def test_files_seen_is_kept_while_first_stands_down_and_cleared_by_results():
    # Two files, nothing in MeetTrack: results:* pages (event 1 is not the
    # newest) and first:* stands down, keeping the time the files were seen.
    t0 = at("15:00")
    events = {"11113": [ev(1, 10, 0), ev(2, 10, 0), ev(3, 10, 0)]}
    row = reg(events_published=2, events_with_results=0, coverage_at=iso(t0 - 30))
    mem = {"baseline:11113:2026-10-10": 0}         # watched since midnight: both files are new
    for step in range(0, 14):
        now = t0 + step * 300
        row["coverage_at"] = iso(now - 30)
        exps, mem = evaluate(snap(now, [row], events=events), CFG, mem)
    e = by_id(exps)
    assert e["results:11113:2026-10-10"].status == "missed"
    assert e["first:11113:2026-10-10"].status == "closed"           # superseded by results:*
    assert "superseded by results:11113" in e["first:11113:2026-10-10"].evidence
    assert mem["files_seen:11113:2026-10-10"] == t0
    # The host withdraws one file: results:* is quiet again, MeetTrack still has
    # nothing. first:* pages at once: the files were seen 70 min ago, not now.
    now = t0 + 70 * 60
    row.update(events_published=1, coverage_at=iso(now - 30))
    exps, mem = evaluate(snap(now, [row], events=events), CFG, mem)
    f = by_id(exps)["first:11113:2026-10-10"]
    assert f.status == "missed" and "first seen 15:00" in f.evidence
    # Results arrive: the clock is cleared.
    exps, mem = evaluate(snap(now + 300, [row], events=events, today={"11113": 5}), CFG, mem)
    assert by_id(exps)["first:11113:2026-10-10"].status == "met"
    assert "files_seen:11113:2026-10-10" not in mem


def test_memory_of_a_meet_that_leaves_the_registry_or_the_day_is_dropped():
    t = at("15:00")
    row = reg(events_published=4, events_with_results=2, coverage_at=iso(t - 30))
    exps, mem = evaluate(snap(t, [row], events=FOUR), CFG, {})
    assert "results:11113:2026-10-10" in mem["late_events"]
    exps, mem = evaluate(snap(t + 300, []), CFG, mem)                    # meet left the registry
    assert mem["late_events"] == {} and mem["results_progress"] == {}
    assert not [k for k in mem if k.startswith(("baseline:", "files_seen:"))]
    # a not-judged meet leaves no empty record behind
    s = snap(t + 600, [row], events=FOUR)
    s.read_errors = {"11113": ["events: HTTP 500"]}
    exps, mem = evaluate(s, CFG, mem)
    assert mem["late_events"] == {}


def test_numeric_string_event_numbers_are_grouped_as_numbers():
    from watchdog.expectations import by_event_number
    rows = [{"event_number": "2", "results": 0, "entries": 8},
            {"event_number": 2, "results": 14, "entries": 16},
            {"event_number": "10", "results": 3, "entries": 3},
            {"event_number": " 9 ", "results": 1, "entries": 1},
            {"event_number": "x", "results": 0, "entries": 0},
            {"event_number": True, "results": 0, "entries": 0}]
    groups = by_event_number(rows)
    assert [g["event_number"] for g in groups[:3]] == [2, 9, 10]
    assert groups[0]["results"] == 14 and groups[0]["rows"] == 2
    assert all(g["key"].startswith("row:") for g in groups[3:])


def test_out_of_order_publication_never_pages_the_event_being_swum():
    # Events 1-3 have results; the host lists 5 (being swum, empty) before 4.
    # The count says one published event has no result: the lowest is taken
    # (4), and as the newest number it waits. Nothing pages in an hour.
    t = at("15:00")
    events = {"11113": [ev(1, 10, 10), ev(2, 10, 10), ev(3, 10, 10), ev(4, 10, 0),
                        ev(5, 10, 0), ev(6, 10, 0)]}
    row = reg(events_published=4, events_with_results=3, coverage_at=iso(t - 30))
    mem = {}
    for step in range(0, 13):
        now = t + step * 300
        row["coverage_at"] = iso(now - 30)
        exps, mem = evaluate(snap(now, [row], events=events), CFG, mem)
        assert by_id(exps)["results:11113:2026-10-10"].status == "met"


def test_thin_refinement_skips_event_ids_that_are_not_plain_ids():
    calls = []

    class FakeRest:
        def get(self, path, timeout, count=False):
            calls.append(path)
            if path.startswith("meet_registry?select=sr_meet_id&limit=1"):
                return [{"sr_meet_id": "1"}], None
            if path.startswith("meet_registry"):
                return [reg()], None
            if path.startswith("meets?select=id"):
                return [{"id": "m1", "live_rankings_id": "sr-11113"}], None
            if path.startswith("events"):
                return [{"id": "e1),id.neq.(x", "event_number": 1, "relay_count": 1,
                         "results": [{"count": 2}], "heats": [{"count": 30}]}], None
            if path.startswith("results"):
                return 2, None
            return [], None
    er.build_snapshot(at("17:58"), CFG, RUN_CFG, FakeRest(), ps=lambda: "")
    assert not [p for p in calls if p.startswith("results?select=created_at")]


# --- council round 2 (2026-11-07) ------------------------------------------------

def _unlisted(now, published):
    return reg(events_published=published, events_with_results=0, coverage_at=iso(now - 30))


def test_each_superseded_unlisted_publication_has_its_own_clock():
    # Published 1 -> 2 -> 3 (no MeetTrack rows): the first is superseded at
    # 15:05, the second at 15:15. At 15:25 only the first is late.
    t = at("15:00")
    mem = {}
    for minutes, n in ((0, 1), (5, 2), (10, 2), (15, 3), (20, 3), (25, 3)):
        exps, mem = evaluate(snap(t + minutes * 60, [_unlisted(t + minutes * 60, n)]), CFG, mem)
    e = by_id(exps)["results:11113:2026-10-10"]
    assert e.status == "missed"
    assert "1 of them has no results in MeetTrack after 20+ min (1 not in MeetTrack's event list)" \
        in e.evidence
    assert "1 more is short, not yet due (1 not in MeetTrack's event list, due 15:35)" in e.evidence


def test_a_published_count_that_dips_keeps_the_oldest_unlisted_clock():
    # The host's list loses a file for one run (it flickers) and gets it back:
    # the oldest clock survives and the dip is not progress.
    t = at("15:00")
    mem = {}
    for minutes, n in ((0, 1), (5, 2), (10, 3), (15, 2), (20, 3)):
        exps, mem = evaluate(snap(t + minutes * 60, [_unlisted(t + minutes * 60, n)]), CFG, mem)
    assert "results:11113:2026-10-10" not in mem.get("results_progress", {})
    exps, mem = evaluate(snap(t + 25 * 60, [_unlisted(t + 25 * 60, 3)]), CFG, mem)
    e = by_id(exps)["results:11113:2026-10-10"]
    assert e.status == "missed" and "short since 15:05" in e.evidence


def test_first_results_after_a_cold_start_past_the_deadline_ignores_yesterdays_files():
    # Day two, the monitor restarts at 18:00 with no memory. The host lists
    # yesterday's 16 files, all with results in MeetTrack, none written today.
    # They are not today's news: no "files but no results" warning, ever.
    events = {"11113": [ev(n, 10, 10) for n in range(1, 17)] + [ev(n, 10, 0) for n in range(17, 33)]}
    mem = {}
    for minutes in range(0, 121, 5):
        now = at("18:00", NEXT) + minutes * 60
        row = reg(events_published=16, events_with_results=16, coverage_at=iso(now - 30))
        exps, mem = evaluate(snap(now, [row], events=events), CFG, mem)
        f = by_id(exps)["first:11113:2026-10-11"]
        assert f.status == "pending", (minutes, f.evidence)
    # a new file today with nothing in MeetTrack is still news
    now = at("20:05", NEXT)
    row = reg(events_published=17, events_with_results=16, coverage_at=iso(now - 30))
    exps, mem = evaluate(snap(now, [row], events=events), CFG, mem)
    assert "(1 new today)" in by_id(exps)["first:11113:2026-10-11"].evidence


def test_snapshot_handles_numeric_ids_and_refuses_odd_meet_ids():
    calls = []

    class FakeRest:
        def __init__(self, meet_id):
            self.meet_id = meet_id

        def get(self, path, timeout, count=False):
            calls.append(path)
            if path.startswith("meet_registry?select=sr_meet_id&limit=1"):
                return [{"sr_meet_id": "1"}], None
            if path.startswith("meet_registry"):
                return [reg()], None
            if path.startswith("meets?select=id"):
                return [{"id": self.meet_id, "live_rankings_id": "sr-11113"}], None
            if path.startswith("events"):
                return [{"id": 41, "event_number": 1, "relay_count": 1,
                         "results": [{"count": 2}], "heats": [{"count": 30}]}], None
            if path.startswith("results?select=created_at"):
                return [{"created_at": iso(at("16:35"))}], None
            if path.startswith("results"):
                return 2, None
            return [], None
    s = er.build_snapshot(at("17:58"), CFG, RUN_CFG, FakeRest(7), ps=lambda: "")
    assert "results?select=created_at&event_id=in.(41)&order=created_at.desc&limit=1" in calls
    assert s.thin_last_write["11113"] == at("16:35")
    calls.clear()
    s = er.build_snapshot(at("17:58"), CFG, RUN_CFG, FakeRest("m1&select=*"), ps=lambda: "")
    assert not [p for p in calls if p.startswith(("events", "results"))]
    assert s.read_errors["11113"] == ["meets: meet id is not a plain id"]


# --- council round 3 (2026-11-07) ------------------------------------------------

def test_a_counting_writer_is_recognised_whatever_the_case_of_its_feed_type():
    from watchdog.expectations import eval_results
    now = at("15:00")
    row = reg(feed_type=" PDF ", events_published=None, events_with_results=None)
    e = eval_results(row, snap(now, [row], events=FOUR), CFG, LIS, DAY, {})
    assert e.status == "unknown"


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_future_or_broken_progress_time_never_mutes_reminders(bad):
    from watchdog.expectations import Expectation
    t = at("12:00", D7)

    def exp(progress_at):
        return Expectation("results:1:d", "results", "M", "e", None, "missed", "crit", "x",
                           data={"progress_at": progress_at})
    _, s = plan([exp(None)], {}, t, LIVE_POLICY, True)
    for p in (t + 6 * 3600, bad):                      # 6 h ahead (clock skew), or not a number
        notices, s2 = plan([exp(p)], s, t + 3600, LIVE_POLICY, True)
        assert [n.kind for n in notices] == ["reminder"], p
        assert notices[0].reason == ""


def test_evidence_says_when_event_numbers_are_a_guess():
    # Events 1 and 4 have results, 2 and 3 none; the counts say one published
    # event lacks results. In order, 2 and 3 would both be published: the host
    # did not publish in number order, so the named event is a guess.
    t = at("15:00")
    events = {"11113": [ev(1, 10, 10), ev(2, 10, 0), ev(3, 10, 0), ev(4, 10, 10), ev(5, 10, 10)]}
    row = reg(events_published=4, events_with_results=3, coverage_at=iso(t - 30))
    exps, mem = evaluate(snap(t, [row], events=events), CFG, {})
    row["coverage_at"] = iso(t + 1170)
    exps, mem = evaluate(snap(t + 1200, [row], events=events), CFG, mem)
    e = by_id(exps)["results:11113:2026-10-10"]
    assert e.status == "missed"
    assert "event numbers are a guess" in e.evidence
    # in number order there is no such note
    exps, _ = evaluate(snap(t + 1200, [row], events={"11113": [
        ev(1, 10, 10), ev(2, 10, 0), ev(3, 10, 10), ev(4, 10, 10), ev(5, 10, 0)]}), CFG, mem)
    assert "guess" not in by_id(exps)["results:11113:2026-10-10"].evidence


# --- council round 4 (2026-11-07) ------------------------------------------------

def test_files_seen_survives_a_blind_interval():
    # The host lists a new file at 15:00. The writer's last count is from 15:05
    # while 15:40-15:50 run; at 16:00 it counts again: the deadline is still
    # 16:00 (files first seen 15:00), not a fresh hour from the recovery.
    t0 = at("15:00", D7)
    mem = {"baseline:11531:" + D7: 0}

    def run(minutes, fresh=True):
        nonlocal mem
        now = t0 + minutes * 60
        s = alapraia(now, events_published=1,
                     coverage_at=iso(now - 30 if fresh else t0 + 5 * 60))
        exps, mem = evaluate(s, CFG, mem)
        return by_id(exps)[f"first:11531:{D7}"]
    run(0)
    run(5)
    for m in (40, 45, 50):
        run(m, fresh=False)                         # blind: counters from 15:05
    assert mem["files_seen:11531:" + D7] == t0
    f = run(60)
    assert f.status == "missed" and "first seen 15:00" in f.evidence


def test_unlisted_clocks_are_bounded_whatever_the_counters_say():
    from watchdog.expectations import MAX_UNLISTED_CLOCKS
    t = at("15:00")
    mem = {}
    for minutes, n in ((0, 1), (5, 10_000)):
        exps, mem = evaluate(snap(t + minutes * 60, [_unlisted(t + minutes * 60, n)]), CFG, mem)
    clocks = mem["late_events"]["results:11113:2026-10-10"]["unlisted"]["sinces"]
    assert len(clocks) == MAX_UNLISTED_CLOCKS
    e = by_id(exps)["results:11113:2026-10-10"]
    assert "9999 more are short" in e.evidence or "9999 published events are short" in e.evidence


def test_repeated_dips_keep_the_oldest_unlisted_clock_and_do_not_pile_up():
    t = at("15:00")
    mem = {}
    seq = [(0, 1), (5, 2), (10, 3)] + [(15 + 5 * i, 2 if i % 2 == 0 else 3) for i in range(6)]
    for minutes, n in seq:
        exps, mem = evaluate(snap(t + minutes * 60, [_unlisted(t + minutes * 60, n)]), CFG, mem)
        clocks = mem["late_events"]["results:11113:2026-10-10"]["unlisted"]["sinces"]
        assert len(clocks) == n - 1                      # the newest has no clock
        assert minutes == 0 or clocks[0] == t + 300      # the oldest survives every dip
    assert by_id(exps)["results:11113:2026-10-10"].status == "missed"     # 15:05 + 20 min


def test_save_json_never_leaves_a_half_written_state_file(tmp_path, monkeypatch):
    path = tmp_path / "expect-state.json"
    er.save_json(path, {"memory": {"late_events": {"a": 1}}})
    before = path.read_text()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(er.os, "fsync", boom)
    with pytest.raises(OSError):
        er.save_json(path, {"memory": {}})
    assert path.read_text() == before


def test_plain_id_accepts_the_ids_splash_poller_writes():
    # meets.id and events.id are text NOT NULL, filled by db.gen_id(): uuid4.
    import uuid
    assert er._PLAIN_ID.fullmatch(str(uuid.uuid4()))
    for bad in ("a,b", "a)", "a.b", "a&b", "a=b", "a b", "a*", "a:b", ""):
        assert not er._PLAIN_ID.fullmatch(bad), bad


# --- council round 5 (2026-11-07) ------------------------------------------------

def test_an_open_first_alert_is_closed_once_when_results_takes_over():
    # 15:00 the host lists one file, MeetTrack nothing: first:* pages at 16:00.
    # 16:05 a second file: event 1 is no longer the newest and is late at
    # 16:25, so results:* pages and first:* is closed as superseded, once.
    # 16:30 results arrive: one RESOLVED, for results:* only.
    t0 = at("15:00", D7)
    events = [ev(1, 10, 0), ev(2, 10, 0), ev(3, 10, 0)]

    def make(t):
        pub = 1 if t < at("16:05", D7) else 2
        done = t >= at("16:30", D7)
        row = reg("11531", "Alapraia", start_date=D7, end_date=D7, events_published=pub,
                  events_with_results=pub if done else 0, coverage_at=iso(t - 30),
                  last_ingest_at=iso(at("16:30", D7)) if done else None)
        evs = [ev(1, 10, 10), ev(2, 10, 10), ev(3, 10, 0)] if done else events
        return snap(t, [row], events={"11531": evs}, today={"11531": 20 if done else 0})
    mem, state, sent = {"baseline:11531:" + D7: 0}, {}, []
    t = t0
    while t <= at("22:10", D7):
        exps, mem = evaluate(make(t), CFG, mem)
        notices, state = plan(exps, state, t, LIVE_POLICY, in_racing_window(t, CFG))
        sent += [(hhmm(t), n.kind, n.id.split(":")[0], n.reason) for n in notices]
        t += 300
    assert [s[:3] for s in sent] == [("16:00", "new", "first"), ("16:25", "new", "results"),
                                     ("16:25", "closed", "first"), ("16:30", "resolved", "results")]
    assert sent[2][3].startswith("superseded by results:11531:")
    assert state["open"] == {}


# --- council round 6 (2026-11-07) ------------------------------------------------

def test_blind_first_results_evidence_names_the_real_reason():
    from watchdog.expectations import eval_first_results
    now = at("17:05")
    # a PDF writer that ticks but stamps NULL counters: its counters are broken
    row = reg(events_published=None, events_with_results=None, coverage_at=iso(now - 30))
    e = eval_first_results(row, snap(now, [row], events=FOUR), CFG, LIS, DAY, {})
    assert "did not report its file counts" in e.evidence and "does not count" not in e.evidence
    # a fragment or Lenex writer genuinely does not count the host's files
    row = reg(feed_type="lenex", events_published=None, events_with_results=None,
              coverage_at=iso(now - 30))
    e = eval_first_results(row, snap(now, [row], events=FOUR), CFG, LIS, DAY, {})
    assert "this writer does not count files" in e.evidence


def test_an_invalid_meet_id_makes_the_meet_unknown_and_pages_db_reads():
    class FakeRest:
        def get(self, path, timeout, count=False):
            if path.startswith("meet_registry?select=sr_meet_id&limit=1"):
                return [{"sr_meet_id": "1"}], None
            if path.startswith("meet_registry"):
                return [reg(events_published=4, events_with_results=2,
                            coverage_at=iso(at("16:59")))], None
            if path.startswith("meets?select=id"):
                return [{"id": "m1,id.neq.x", "live_rankings_id": "sr-11113"}], None
            return [], None
    mem, kinds = {}, []
    for now in (at("17:00"), at("17:05")):
        s = er.build_snapshot(now, CFG, RUN_CFG, FakeRest(), ps=lambda: "")
        exps, mem = evaluate(s, CFG, mem)
        e = by_id(exps)
        assert e["results:11113:2026-10-10"].status == "unknown"
        assert e["first:11113:2026-10-10"].status == "unknown"
        kinds.append(e["db:reads"].status)
    assert kinds == ["pending", "missed"]
    assert "meet id is not a plain id" in e["db:reads"].evidence
    notices, _ = plan(exps, {}, at("17:05"), POLICY, True)
    assert "db:reads" in [n.id for n in notices]


# --- council round 7 (2026-11-07) ------------------------------------------------

def test_a_cold_start_on_the_first_day_counts_listed_files_as_todays():
    # The meet races today for the first time; the monitor (re)starts at 10:00
    # and the host already lists a file with nothing in MeetTrack. No start time.
    sent, mem, _ = run_day(lambda t: alapraia(t, events_published=1), "10:00", "11:05")
    assert mem["baseline:11531:" + D7] == 0
    assert [s[:3] for s in sent] == [("11:00", "new", f"first:11531:{D7}")]


def test_a_meet_that_enters_mid_afternoon_with_files_still_pages():
    # Like Ponte da Barca at 16:40, but with a writer that counts the files.
    # One file (the newest, so results:* waits): first:* pages an hour later.
    sent, _, _ = run_day(lambda t: alapraia(t, events_published=1), "16:40", "17:45")
    assert [s[:3] for s in sent] == [("17:40", "new", f"first:11531:{D7}")]
    # Six files: results:* pages 20 min after the first five were superseded,
    # and first:* stands down (never opened, so nothing more is sent).
    sent, _, _ = run_day(lambda t: alapraia(t, events_published=6), "16:40", "17:45")
    assert [s[:3] for s in sent] == [("17:00", "new", f"results:11531:{D7}")]


def test_day_two_baseline_is_taken_before_racing_starts():
    events = {"11113": [ev(n, 10, 10) for n in range(1, 11)] + [ev(11, 10, 0)]}
    row = reg(events_published=10, events_with_results=10, coverage_at=iso(at("07:55", NEXT)))
    _, mem = evaluate(snap(at("07:55", NEXT), [row], events=events), CFG, {})
    assert mem["baseline:11113:2026-10-11"] == 10
    # a cold start after racing began: only files whose events have results are known to be old
    row.update(events_published=11, coverage_at=iso(at("12:00", NEXT)))
    _, mem = evaluate(snap(at("12:00", NEXT), [row], events=events), CFG, {})
    assert mem["baseline:11113:2026-10-11"] == 10


@pytest.mark.parametrize("published, covered", [(2, 3), (-1, 0), (True, 0)])
def test_inconsistent_counters_are_not_judged_and_keep_the_clocks(published, covered):
    t0 = at("15:00", D7)
    mem = {"baseline:11531:" + D7: 0}
    exps, mem = evaluate(alapraia(t0, events_published=1), CFG, mem)
    assert mem["files_seen:11531:" + D7] == t0
    bad = alapraia(t0 + 600, events_published=published, events_with_results=covered)
    exps, mem = evaluate(bad, CFG, mem)
    e = by_id(exps)
    assert e[f"results:11531:{D7}"].status == "unknown"
    assert "do not add up" in e[f"results:11531:{D7}"].evidence
    assert e[f"first:11531:{D7}"].status == "pending"
    assert mem["files_seen:11531:" + D7] == t0                 # the deadline is kept
    exps, mem = evaluate(alapraia(t0 + 3600, events_published=1), CFG, mem)
    assert by_id(exps)[f"first:11531:{D7}"].status == "missed"


# --- council round 8 (2026-11-07) ------------------------------------------------

def test_a_one_run_dip_in_the_hosts_files_keeps_the_first_seen_time():
    t0 = at("15:00", D7)
    mem = {"baseline:11531:" + D7: 0}
    for minutes, pub in ((0, 1), (5, 1), (20, 0), (25, 1)):         # one run without the file
        exps, mem = evaluate(alapraia(t0 + minutes * 60, events_published=pub), CFG, mem)
    assert mem["files_seen:11531:" + D7] == t0
    exps, mem = evaluate(alapraia(t0 + 3600, events_published=1), CFG, mem)
    f = by_id(exps)[f"first:11531:{D7}"]
    assert f.status == "missed" and "first seen 15:00" in f.evidence


def test_files_gone_for_ten_minutes_clears_the_first_seen_time():
    t0 = at("15:00", D7)
    mem = {"baseline:11531:" + D7: 0}
    for minutes, pub in ((0, 1), (20, 0), (25, 0)):
        exps, mem = evaluate(alapraia(t0 + minutes * 60, events_published=pub), CFG, mem)
    assert "files_seen:11531:" + D7 in mem                           # 5 min: not yet confirmed
    exps, mem = evaluate(alapraia(t0 + 30 * 60, events_published=0), CFG, mem)
    assert "files_seen:11531:" + D7 not in mem and "files_gone:11531:" + D7 not in mem


def test_first_results_keeps_its_memory_while_the_meets_reads_fail():
    t0 = at("15:00", D7)
    mem = {"baseline:11531:" + D7: 0}
    exps, mem = evaluate(alapraia(t0, events_published=1), CFG, mem)
    before = {k: v for k, v in mem.items() if k.startswith(("baseline:", "files_"))}
    s = alapraia(t0 + 300, events_published=0)
    s.read_errors = {"11531": ["results today: ReadTimeout"]}
    exps, mem = evaluate(s, CFG, mem)
    assert by_id(exps)[f"first:11531:{D7}"].status == "unknown"
    assert {k: v for k, v in mem.items() if k.startswith(("baseline:", "files_"))} == before
    # a cold start during failed reads does not fix a baseline either
    s = alapraia(t0, events_published=3)
    s.read_errors = {"11531": ["events: HTTP 500"]}
    _, fresh_mem = evaluate(s, CFG, {})
    assert not [k for k in fresh_mem if k.startswith(("baseline:", "files_"))]


# --- council round 9 (2026-11-07) ------------------------------------------------

def test_a_fresh_count_without_events_with_results_is_not_a_measured_host():
    # events_published=1 but events_with_results NULL: both checks treat the
    # host as unseen. results:* is unknown; first:* follows the blind clock and
    # neither starts nor expires files_seen.
    t0 = at("15:00", D7)
    exps, mem = evaluate(alapraia(t0, events_published=1, events_with_results=None), CFG,
                         {"baseline:11531:" + D7: 0})
    e = by_id(exps)
    assert e[f"results:11531:{D7}"].status == "unknown"
    assert "did not count the events with results" in e[f"results:11531:{D7}"].evidence
    assert e[f"first:11531:{D7}"].status == "pending"
    assert "did not count the events with results" in e[f"first:11531:{D7}"].evidence
    assert "files_seen:11531:" + D7 not in mem
    # with a clock already running, such runs neither restart nor clear it
    mem["files_seen:11531:" + D7] = t0 - 600
    for m in (5, 20, 35):
        exps, mem = evaluate(alapraia(t0 + m * 60, events_published=1, events_with_results=None),
                             CFG, mem)
    assert mem["files_seen:11531:" + D7] == t0 - 600 and "files_gone:11531:" + D7 not in mem


def test_results_alert_through_missed_unknown_missed_reminds_only_when_stuck():
    t = at("15:00")
    row = reg(events_published=4, events_with_results=2, coverage_at=iso(t - 30),
              last_ingest_at=iso(t - 60))
    mem, state, kinds = {}, {}, []
    for minutes in range(0, 181, 5):
        now = t + minutes * 60
        stale = 70 <= minutes < 100                       # the writer stops counting for a while
        row["coverage_at"] = iso(now - (40 * 60 if stale else 30))
        exps, mem = evaluate(snap(now, [row], events=FOUR), CFG, mem)
        notices, state = plan(exps, state, now, LIVE_POLICY, True)
        kinds += [(minutes, n.kind) for n in notices if n.id.startswith("results:")]
        if stale:
            assert by_id(exps)["results:11113:2026-10-10"].status == "unknown"
    # NEW at +20; reminders once an hour; none while not judged (70-95)
    assert kinds == [(20, "new"), (100, "reminder"), (160, "reminder")]


# --- council round 10 (2026-11-07) -----------------------------------------------

def test_a_clocked_event_missing_from_the_snapshot_is_not_judged_until_confirmed_gone():
    t = at("15:00")
    row = reg(events_published=4, events_with_results=2, coverage_at=iso(t - 30),
              last_ingest_at=iso(t - 60))
    mem = {}

    def run(minutes, events):
        nonlocal mem
        now = t + minutes * 60
        row["coverage_at"] = iso(now - 30)
        exps, mem = evaluate(snap(now, [row], events={"11113": events}), CFG, mem)
        return by_id(exps)["results:11113:2026-10-10"]
    full = FOUR["11113"]
    without_2 = [e for e in full if e["event_number"] != 2]
    assert run(0, full).status == "pending"                     # event 2: clock from 15:00
    e = run(5, without_2)                                       # event 2 vanished from the read
    assert e.status == "unknown" and "event 2" in e.evidence
    assert mem["late_events"]["results:11113:2026-10-10"]["2"]["since"] == t
    e = run(20, full)                                           # back: its clock is intact
    assert e.status == "missed" and "short since 15:00" in e.evidence
    for m in (25, 30, 35, 40):                                  # gone for good (15 min)
        e = run(m, without_2)
    assert e.status != "unknown"
    assert "2" not in mem["late_events"].get("results:11113:2026-10-10", {})


def test_thin_refinement_queries_the_usable_ids_and_says_which_it_skipped():
    calls = []

    class FakeRest:
        def get(self, path, timeout, count=False):
            calls.append(path)
            if path.startswith("meet_registry?select=sr_meet_id&limit=1"):
                return [{"sr_meet_id": "1"}], None
            if path.startswith("meet_registry"):
                return [reg(events_published=3, events_with_results=3,
                            coverage_at=iso(at("17:57")), last_ingest_at=iso(at("17:57")))], None
            if path.startswith("meets?select=id"):
                return [{"id": "m1", "live_rankings_id": "sr-11113"}], None
            if path.startswith("events"):
                return [{"id": "e1", "event_number": 1, "relay_count": 1,
                         "results": [{"count": 2}], "heats": [{"count": 30}]},
                        {"id": "bad,id", "event_number": 2, "relay_count": 1,
                         "results": [{"count": 2}], "heats": [{"count": 30}]},
                        {"id": "e3", "event_number": 3, "relay_count": 1,
                         "results": [{"count": 30}], "heats": [{"count": 30}]}], None
            if path.startswith("results?select=created_at"):
                return [{"created_at": iso(at("16:35"))}], None
            if path.startswith("results"):
                return 34, None
            return [], None
    s = er.build_snapshot(at("17:58"), CFG, RUN_CFG, FakeRest(), ps=lambda: "")
    assert [p for p in calls if p.startswith("results?select=created_at")] == [
        "results?select=created_at&event_id=in.(e1)&order=created_at.desc&limit=1"]
    assert s.thin_last_write["11113"] == at("16:35") and s.thin_refined["11113"] == {"1"}
    # cold start: event 1 counts from its own newest result (16:35), event 2
    # (not queried) from the meet's last write (17:57)
    exps, mem = evaluate(s, CFG, {})
    e = by_id(exps)["results:11113:2026-10-10"]
    recs = mem["late_events"]["results:11113:2026-10-10"]
    assert recs["1"]["since"] == at("16:35") and recs["2"]["since"] == at("17:57")
    assert "1 thin event has ids that cannot be queried" in e.evidence


# --- 8 Nov 2026: heats swum outside the start list's slot --------------------

def due_ev(n, entries, due, results, day_time=None):
    e = ev(n, entries, results, day_time=day_time)
    e["entries_due"] = due
    return e


def test_a_start_list_whose_heats_are_not_due_is_not_thin():
    # Leiria, 8 Nov 2026, 11:15: the host swam event 30's fast heats in the
    # morning and printed only its slow heats, all slotted 12:23. 19 results
    # for 39 start-list swimmers who were not due yet is not a gap.
    t = at("11:15", D8)
    row = reg("11115", "Leiria", start_date=D7, end_date=D8, feed_type="fragment",
              events_published=None, events_with_results=None,
              last_ingest_at=iso(at("10:54", D8)), coverage_at=iso(at("10:54", D8)))
    events = [due_ev(29, 35, 0, 24, f"{D8}T12:16:00"), due_ev(30, 39, 0, 19, f"{D8}T12:23:00"),
              due_ev(31, 14, 0, 22, f"{D8}T12:33:00")]
    mem = {"last_run": t - 300}
    exps, mem = evaluate(snap(t, [row], events={"11115": events}), CFG, mem)
    e = by_id(exps)[f"results:11115:{D8}"]
    assert e.status == "met" and e.data["newest"] == 31
    # 12:50: its heats are due (12:23 + 25 min) and nothing new came: short,
    # not yet due; late 20 min on (slot + 45 min).
    t2 = at("12:50", D8)
    events[1]["entries_due"] = 39
    exps, mem = evaluate(snap(t2, [row], events={"11115": events}), CFG, {**mem, "last_run": t2 - 300})
    e = by_id(exps)[f"results:11115:{D8}"]
    assert e.status == "pending" and e.data["thin"] == [30]
    t3 = at("13:10", D8)
    exps, mem = evaluate(snap(t3, [row], events={"11115": events}), CFG, {**mem, "last_run": t3 - 300})
    e = by_id(exps)[f"results:11115:{D8}"]
    assert e.status == "missed" and "19 results for 39 entries;" in e.evidence


def test_only_due_heats_count_and_the_evidence_says_so():
    # Leiria event 18: heat 1 at 09:15, heat 2 (the fast heat) at 16:00. At
    # 11:15 four results for its 8 due swimmers is half: not thin. Two is.
    t = at("11:15", D8)
    row = reg("11115", "Leiria", start_date=D7, end_date=D8, feed_type="fragment",
              events_published=None, events_with_results=None,
              last_ingest_at=iso(t - 60), coverage_at=iso(t - 60))
    events = [due_ev(18, 16, 8, 4, f"{D8}T09:15:00"), due_ev(19, 8, 8, 27, f"{D8}T09:20:00")]
    exps, _ = evaluate(snap(t, [row], events={"11115": events}), CFG, {"last_run": t - 300})
    assert by_id(exps)[f"results:11115:{D8}"].status == "met"
    events[0]["results"] = 2
    mem = {"last_run": t - 300,
           "late_events": {f"results:11115:{D8}": {"18": {"since": t - 1500, "results": 2}}}}
    exps, _ = evaluate(snap(t, [row], events={"11115": events}), CFG, mem)
    e = by_id(exps)[f"results:11115:{D8}"]
    assert e.status == "missed"
    assert "2 results for 8 entries due by the schedule; event 18" in e.evidence


def _gap_snapshot(t, events, due_at="absent"):
    row = reg("11115", "Leiria", start_date=D7, end_date=D8, feed_type="fragment",
              events_published=None, events_with_results=None,
              last_ingest_at=iso(t - 900), coverage_at=iso(t - 900))
    s = snap(t, [row], events={"11115": events})
    s.thin_last_write["11115"] = at("10:47", D8)
    s.thin_refined["11115"] = {str(e["event_number"]) for e in events}
    if due_at != "absent":
        s.thin_due_at["11115"] = due_at
    return s


def test_after_a_memory_gap_a_thin_clock_never_starts_before_its_newest_due_heat():
    # No memory: the clock would start at the newest result (10:47). The slow
    # heats were due only from 12:48 (12:23 + 25), so at 12:55 it is short,
    # not late.
    t = at("12:55", D8)
    events = [due_ev(30, 39, 39, 19, f"{D8}T12:23:00"), due_ev(31, 14, 14, 22, f"{D8}T12:33:00")]
    exps, _ = evaluate(_gap_snapshot(t, events, due_at=at("12:23", D8)), CFG, {})
    e = by_id(exps)[f"results:11115:{D8}"]
    assert e.status == "pending" and "short since 12:48" in e.evidence


def test_after_a_memory_gap_a_later_heat_that_made_it_thin_bounds_the_clock():
    # Event 18: 4 results for heat 1's 8 swimmers (09:15) is not thin; heat 2
    # (16:00) is due from 16:25 and makes it 4 of 16. At 16:30 with no memory
    # that is 5 min old, not 6 hours (its newest result, 10:47).
    t = at("16:30", D8)
    events = [due_ev(18, 16, 16, 4, f"{D8}T09:15:00"), due_ev(19, 8, 8, 27, f"{D8}T09:20:00")]
    exps, _ = evaluate(_gap_snapshot(t, events, due_at=at("16:00", D8)), CFG, {})
    e = by_id(exps)[f"results:11115:{D8}"]
    assert e.status == "pending" and "short since 16:25" in e.evidence


def test_after_a_memory_gap_an_unknown_due_time_starts_the_clock_now():
    t = at("16:30", D8)
    events = [due_ev(18, 16, 16, 4, f"{D8}T09:15:00"), due_ev(19, 8, 8, 27, f"{D8}T09:20:00")]
    exps, _ = evaluate(_gap_snapshot(t, events), CFG, {})
    e = by_id(exps)[f"results:11115:{D8}"]
    assert e.status == "pending" and "short since 16:30" in e.evidence


def test_after_a_memory_gap_heats_without_a_slot_keep_the_old_backdating():
    # Every due heat has no slot (always due): its newest result bounds it.
    t = at("16:30", D8)
    events = [due_ev(18, 16, 16, 4), due_ev(19, 8, 8, 27)]
    exps, _ = evaluate(_gap_snapshot(t, events, due_at=None), CFG, {})
    e = by_id(exps)[f"results:11115:{D8}"]
    assert e.status == "missed" and "short since 10:47" in e.evidence


def test_rows_sharing_a_number_judge_the_larger_rows_due_count():
    # One event, two rows (heats, and the final of the same swimmers): the
    # start list is the larger row's, and so is its due count.
    from watchdog.expectations import by_event_number, due_entries
    g = by_event_number([due_ev(3, 14, 14, 32), due_ev(3, 8, 0, 0)])[0]
    assert (g["entries"], g["entries_due"], due_entries(g)) == (14, 14, 14)
    g = by_event_number([ev(3, 14, 32), ev(3, 8, 0)])[0]
    assert g["entries_due"] is None and due_entries(g) == 14


def test_snapshot_asks_when_the_thin_events_newest_due_heat_was_due():
    calls = []
    now = at("13:10", D8)

    class FakeRest:
        def get(self, path, timeout, count=False):
            calls.append(path)
            if path.startswith("meet_registry?select=sr_meet_id&limit=1"):
                return [{"sr_meet_id": "1"}], None
            if path.startswith("meet_registry"):
                return [reg("11115", "Leiria", start_date=D7, end_date=D8)], None
            if path.startswith("meets?select=id"):
                return [{"id": "m1", "live_rankings_id": "sr-11115"}], None
            if path.startswith("events"):
                return [{"id": "e30", "event_number": 30, "relay_count": 1,
                         "results": [{"count": 19}], "heats": [{"count": 39}],
                         "due": [{"count": 39}]}], None
            if path.startswith("results?select=created_at"):
                return [{"created_at": iso(at("10:47", D8))}], None
            if path.startswith("heats?select=scheduled_at"):
                return [{"scheduled_at": "2026-11-08T12:23:00+00:00"}], None
            if path.startswith("results"):
                return 19, None
            return [], None
    s = er.build_snapshot(now, CFG, RUN_CFG, FakeRest(), ps=lambda: "")
    q = [p for p in calls if p.startswith("heats?")]
    assert q == ["heats?select=scheduled_at&event_id=in.(e30)&scheduled_at=lte.2026-11-08T12:45:00Z"
                 "&order=scheduled_at.desc&limit=1"]
    assert s.thin_due_at["11115"] == at("12:23", D8)
