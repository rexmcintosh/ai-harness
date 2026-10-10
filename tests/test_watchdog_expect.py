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
    assert "6 of them have no results" in silves.evidence
    assert "8 results for 142 entries" in silves.evidence
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
    assert e.status == "missed" and "gap open since 16:35" in e.evidence


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

def test_morning_grace_without_a_start_time():
    row = reg(coverage_at=iso(at("12:00")))
    exps, mem = evaluate(snap(at("12:00"), [row]), CFG, {})
    assert by_id(exps)["first:11113:2026-10-10"].status == "pending"   # afternoon meets
    exps, _ = evaluate(snap(at("17:01"), [reg(coverage_at=iso(at("17:00")))]), CFG, mem)
    e = by_id(exps)["first:11113:2026-10-10"]
    assert e.status == "missed" and e.severity == "warn" and "0 results today" in e.evidence


def test_a_stated_start_moves_the_deadline():
    cfg = make_config({"start_times": [{"sr_meet_id": "11113", "date": DAY, "start": "15:30"}]})
    row = reg(coverage_at=iso(at("16:29")))
    exps, mem = evaluate(snap(at("16:29"), [row]), cfg, {})
    assert by_id(exps)["first:11113:2026-10-10"].status == "pending"
    exps, _ = evaluate(snap(at("16:31"), [reg(coverage_at=iso(at("16:31")))]), cfg, mem)
    e = by_id(exps)["first:11113:2026-10-10"]
    assert e.status == "missed" and "racing started about 61 min ago" in e.evidence


def test_first_results_met_by_todays_results_or_new_publications():
    row = reg(coverage_at=iso(at("17:30")))
    exps, _ = evaluate(snap(at("17:30"), [row], today={"11113": 3}), CFG, {})
    assert by_id(exps)["first:11113:2026-10-10"].status == "met"
    # day two: yesterday's 10 published files are the baseline, not today's news
    row = reg(events_published=10, events_with_results=10, coverage_at=iso(at("08:00", NEXT)))
    exps, mem = evaluate(snap(at("08:00", NEXT), [row]), CFG, {})
    assert mem["baseline:11113:2026-10-11"] == 10
    row["events_published"] = 11
    exps, _ = evaluate(snap(at("17:30", NEXT), [row]), CFG, mem)
    assert by_id(exps)["first:11113:2026-10-11"].status == "met"


# --- results complete -------------------------------------------------------

def test_published_without_results_waits_the_lag_then_misses():
    t = at("15:00")
    row = reg(events_published=4, events_with_results=3, coverage_at=iso(t - 30),
              last_ingest_at=iso(t - 120))
    exps, mem = evaluate(snap(t, [row]), CFG, {})
    assert by_id(exps)["results:11113:2026-10-10"].status == "pending"
    row["coverage_at"] = iso(t + 19 * 60)
    exps, mem = evaluate(snap(t + 19 * 60, [row]), CFG, mem)
    assert by_id(exps)["results:11113:2026-10-10"].status == "pending"
    row["coverage_at"] = iso(t + 20 * 60)
    exps, mem = evaluate(snap(t + 20 * 60, [row]), CFG, mem)
    e = by_id(exps)["results:11113:2026-10-10"]
    assert e.status == "missed" and "1 of them has no results" in e.evidence
    row.update(events_with_results=4, coverage_at=iso(t + 25 * 60))
    exps, mem = evaluate(snap(t + 25 * 60, [row]), CFG, mem)
    assert by_id(exps)["results:11113:2026-10-10"].status == "met"
    assert "results:11113:2026-10-10" not in mem["gap_since"]


def test_stale_counters_do_not_count_as_missing():
    t = at("15:00")
    row = reg(events_published=4, events_with_results=1, coverage_at=iso(t - 45 * 60))
    exps, _ = evaluate(snap(t, [row]), CFG, {})
    assert by_id(exps)["results:11113:2026-10-10"].status == "met"


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
    row = reg(last_ingest_at=iso(now - 3600), events_published=5, events_with_results=5,
              coverage_at=iso(now - 30))
    return evaluate(snap(now, [row], events={"11113": [ev(1, 20, 1)]}), CFG, {})[0]


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
                         "heats": [{"count": 9}]}], None
            if path.startswith("results"):
                return 2, None
            return [], None
    s = er.build_snapshot(now, CFG, RUN_CFG, FakeRest(),
                          ps=lambda: "7 01:00 track_pdf_meet.py https://live.swimrankings.net/11113/")
    assert s.events["11113"] == [{"event_number": 1, "id": None, "session_number": None,
                                  "day_time": None, "relay_count": 1, "results": 2, "entries": 9}]
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
    assert run_cfg["db_timeout_s"] == 8 and run_cfg["repeat_crit_min"] == 30
    assert "db_timeout_s" not in cfg and "enabled" not in cfg


def test_a_stopped_writer_is_one_alert_not_three():
    now = at("17:45")
    row = reg(last_ingest_at=iso(now - 3600), events_published=5, events_with_results=3,
              coverage_at=iso(now - 40 * 60))
    exps, _ = evaluate(snap(now, [row], events={"11113": [ev(1, 20, 1)]}, procs={"11113": []}),
                       CFG, {})
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
