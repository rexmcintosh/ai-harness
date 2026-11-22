"""Tests for the watchdog runner glue (state load/save, report formatting).

The signal collection itself does real I/O and is verified by a live dry-run;
here we test the deterministic plumbing around it.
"""
import json

from watchdog.run import format_report, load_state, save_state
from watchdog.triage import CheckStatus


def test_load_state_missing_file_returns_empty(tmp_path):
    assert load_state(tmp_path / "nope.json") == {}


def test_save_then_load_roundtrips(tmp_path):
    p = tmp_path / "state.json"
    save_state(p, {"disk": {"level": "crit", "ts": 123}})
    assert load_state(p) == {"disk": {"level": "crit", "ts": 123}}


def test_load_state_corrupt_file_returns_empty(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    assert load_state(p) == {}  # never let a corrupt state file crash the poll


def test_format_report_includes_name_summary_and_evidence():
    fired = [CheckStatus("disk", "crit", "root filesystem 96% full", evidence="/dev/sda1 96% /")]
    report = format_report(fired)
    assert "disk" in report
    assert "96% full" in report
    assert "/dev/sda1" in report


def test_format_report_orders_crit_before_warn():
    fired = [
        CheckStatus("a", "warn", "warn-thing"),
        CheckStatus("b", "crit", "crit-thing"),
    ]
    report = format_report(fired)
    assert report.index("crit-thing") < report.index("warn-thing")


def test_retired_loom_log_is_no_longer_watched():
    # Loom was retired 2026-10-09; its runs.log is a static file now, so it is not a cron log.
    from watchdog.run import CRON_LOGS
    labels = {label for label, _ in CRON_LOGS}
    assert "loom" not in labels
    assert not any("/loom/" in str(path) for _, path in CRON_LOGS)


# --- the meet discovery log (hourly, 2 lines a good run) ---------------------
# Shapes copied from the real discover.cron.log; URLs and page text shortened.

def _good_run(hour):
    ts = f"2026-09-22T{hour:02d}:17:06+00:00"
    return (f"[{ts}] [discover] scraped=10 inserted=0 updated=10 delisted=0\n"
            f"[{ts}] [discover] probed=0/0 candidates\n")


# 22 Sep 2026 02:17: the database answered 522, so the run crashed before any line.
_CRASH_522 = (
    "Traceback (most recent call last):\n"
    '  File "/home/dev/projects/splash_poller/discover_meets.py", line 650, in <module>\n'
    "    discover(\n"
    "    raise requests.HTTPError(\n"
    "requests.exceptions.HTTPError: 522 Server Error: <none> for url: "
    "https://example.supabase.co/rest/v1/meet_registry?select=sr_meet_id\n"
    '<!--[if lt IE 7]> <html class="no-js ie6 oldie" lang="en-US"> <![endif]-->\n'
)


def test_discover_log_is_watched():
    from watchdog.run import CRON_LOGS, RUN_END
    paths = dict(CRON_LOGS)
    assert str(paths["meettrack-discover"]) == "/home/dev/projects/splash_poller/logs/discover.cron.log"
    assert set(RUN_END) <= set(paths)            # every run-end rule names a watched log


def test_discover_run_end_matches_a_real_good_run_only():
    from watchdog.run import RUN_END
    rule = RUN_END["meettrack-discover"]
    ts = "[2026-11-20T16:17:06+00:00] [discover]"
    assert rule.search(f"{ts} scraped=44 inserted=0 updated=44 annp_twins=0 upcoming(14d)=10\n"
                       f"{ts} probed=1/1 candidates")
    assert rule.search(f"{ts} scraped=11 inserted=1 updated=10\n"
                       "  [twin] sr-1 ('Meet', '2026-11-21', 'Porto'): already registered\n"
                       "  [probe warn] 123: read timed out\n"
                       f"{ts} probed=0/1 candidates")
    # a probed= line alone is the blind-index path, not a good run
    assert not rule.search(f"{ts} probed=1/1 candidates")
    # a traceback between them breaks the run
    assert not rule.search(f"{ts} scraped=10\nTraceback (most recent call last):\n"
                           "  File \"x.py\", line 1, in <module>\nValueError: x\n"
                           f"{ts} probed=1/1 candidates")


def test_discover_one_failed_run_then_a_good_one_is_ok():
    from watchdog.run import cron_log_status
    log = _good_run(1) + _CRASH_522 + _good_run(3)
    assert cron_log_status("meettrack-discover", log).level == "ok"


def test_discover_crash_as_the_last_run_is_warn():
    from watchdog.run import cron_log_status
    st = cron_log_status("meettrack-discover", _good_run(1) + _CRASH_522)
    assert st.level == "warn"
    assert "522 Server Error" in st.evidence


# The blind-index path (IndexLayoutError): no scraped= line, the probe still runs and
# prints probed=, then __main__ imports the Telegram sender and prints the ERROR.
_BLIND_PROBED = "[2026-09-22T02:17:08+00:00] [discover] probed=1/1 candidates\n"
_BLIND_ERROR = "[2026-09-22T02:17:09+00:00] [discover] ERROR no meet row could be read from the index\n"


def test_discover_blind_index_error_after_the_probe_is_warn():
    from watchdog.run import cron_log_status
    assert cron_log_status("meettrack-discover", _good_run(1) + _BLIND_PROBED + _BLIND_ERROR).level == "warn"


def test_discover_blind_index_crash_before_its_error_line_is_warn():
    # The import between probed= and ERROR fails: its traceback (stderr, at once) lands
    # before the buffered probed= line, and no ERROR line is printed.
    from watchdog.run import cron_log_status
    crash = ("Traceback (most recent call last):\n"
             '  File "/home/dev/projects/splash_poller/discover_meets.py", line 664, in <module>\n'
             "    from coverage_check import _tg_send\n"
             "ModuleNotFoundError: No module named 'coverage_check'\n")
    st = cron_log_status("meettrack-discover", _good_run(1) + crash + _BLIND_PROBED)
    assert st.level == "warn"
    assert "Traceback" in st.evidence


def test_discover_blind_index_then_a_good_run_is_ok():
    from watchdog.run import cron_log_status
    log = _good_run(1) + _BLIND_PROBED + _BLIND_ERROR + _good_run(3)
    assert cron_log_status("meettrack-discover", log).level == "ok"


def test_discover_log_with_no_good_run_warns():
    # a fresh log whose runs all crashed, or summary lines that no longer fit RUN_END
    from watchdog.run import cron_log_status
    drifted = "[2026-11-20T16:17:06+00:00] [discover] done: 44 meets, 1 probed\n" * 30
    for log in (_CRASH_522, drifted):
        assert cron_log_status("meettrack-discover", log).level == "warn"


def test_discover_summary_lines_renamed_after_months_of_good_runs_warn():
    # the production shape: many old good runs, then splash_poller changes its lines
    from watchdog.run import cron_log_status
    renamed = "[2026-11-21T00:17:06+00:00] [discover] done: 44 meets, 1 probed\n"
    log = "".join(_good_run(h % 24) for h in range(1400)) + renamed * 3
    st = cron_log_status("meettrack-discover", log)
    assert st.level == "warn"
    assert "3 line(s)" in st.summary and "finished run" in st.summary


def test_discover_pair_spanning_a_crash_and_a_blind_run_still_warns():
    # Run A crashed after scraped= (its traceback first, its buffered scraped= after);
    # run B is a blind-index run: probed=, then ERROR. A's scraped= and B's probed=
    # read as one finished run, but B's ERROR still follows it.
    from watchdog.run import cron_log_status
    a_scraped = "[2026-09-22T02:17:06+00:00] [discover] scraped=10 inserted=0 updated=10 delisted=0\n"
    log = _good_run(1) + _CRASH_522 + a_scraped + _BLIND_PROBED + _BLIND_ERROR
    st = cron_log_status("meettrack-discover", log)
    assert st.level == "warn"
    assert "[discover] ERROR" in st.evidence


def test_discover_probe_warn_inside_a_finished_run_is_ok():
    # One meet's probe failing is skipped by discovery itself; the run still finishes.
    from watchdog.run import cron_log_status
    ts = "2026-09-22T03:17:06+00:00"
    log = (f"[{ts}] [discover] scraped=10 inserted=0 updated=10 delisted=0\n"
           "  [probe warn] 123: HTTPSConnectionPool(host='x.example', port=443): "
           "Max retries exceeded (Caused by ConnectTimeoutError)\n"
           f"[{ts}] [discover] probed=0/1 candidates\n")
    assert cron_log_status("meettrack-discover", log).level == "ok"


def test_discover_note_whose_error_text_spans_lines_is_still_a_finished_run():
    # e.g. a [twin warn] carrying an HTTP error with its HTML body: the continuation
    # lines are not indented, but the run still ends with probed= and is good.
    from watchdog.run import cron_log_status
    ts = "2026-11-21T03:17:06+00:00"
    log = (_good_run(2)
           + f"[{ts}] [discover] scraped=10 inserted=0 updated=10 delisted=0\n"
           "  [twin warn] calendar 2026 not read (HTTPError: 503 Server Error for url: https://x.example/\n"
           "<html><body>Service Unavailable</body></html>\n"
           "): twins not checked\n"
           f"[{ts}] [discover] probed=0/1 candidates\n")
    assert cron_log_status("meettrack-discover", log).level == "ok"


def test_discover_a_traceback_header_between_scraped_and_probed_breaks_the_pair():
    from watchdog.run import cron_log_status
    log = (_good_run(1)
           + "[2026-09-22T02:17:06+00:00] [discover] scraped=10 inserted=0 updated=10 delisted=0\n"
           + _CRASH_522 + _BLIND_PROBED)
    st = cron_log_status("meettrack-discover", log)
    assert st.level == "warn"
    assert "522 Server Error" in st.evidence


def test_discover_probe_warn_with_no_end_line_after_it_counts():
    # A run that never printed probed= (crashed, or not yet flushed) is judged in full.
    from watchdog.run import cron_log_status
    ts = "2026-09-22T03:17:06+00:00"
    log = (_good_run(2)
           + f"[{ts}] [discover] scraped=10 inserted=0 updated=10 delisted=0\n"
           "  [probe warn] 123: 503 Server Error: Service Unavailable for url: https://x.example/\n")
    assert cron_log_status("meettrack-discover", log).level == "warn"


def test_other_logs_keep_the_plain_tail_rule():
    from watchdog.run import RUN_END, RUN_MAX_AGE_H, cron_log_status, cron_status
    from watchdog.triage import check_cron_log
    log = _good_run(1) + _CRASH_522 + _good_run(3)
    for label in ("meettrack-ingest", "meettrack-supervise"):
        assert label not in RUN_END and label not in RUN_MAX_AGE_H
        assert cron_log_status(label, log) == check_cron_log(label, log)
        assert cron_status(label, log, _epoch("2030-01-01T00:00:00+00:00")) == check_cron_log(label, log)
        assert cron_log_status(label, log).level == "warn"


def _epoch(iso):
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()


def test_collect_and_the_shadow_use_the_same_discover_rule(monkeypatch, tmp_path):
    import watchdog.run as run
    log = tmp_path / "discover.cron.log"
    log.write_text(_good_run(1) + _CRASH_522 + _good_run(3))
    monkeypatch.setattr(run, "CRON_LOGS", [("meettrack-discover", log)])
    monkeypatch.setattr(run, "_cmd", lambda args: "")
    monkeypatch.setattr(run, "collect_metrics", lambda now, prior: ([], {}))
    monkeypatch.setattr(run, "alertwatch_status", lambda now: None)
    statuses, _ = run.collect(int(_epoch("2026-09-22T03:30:00+00:00")), {})
    assert [s.level for s in statuses if s.name == "cron:meettrack-discover"] == ["ok"]
    assert [lvl for label, _, lvl in run.shadow_logs({}) if label == "meettrack-discover"] == ["ok"]


# --- discovery freshness: a cron that stops running writes nothing ----------

def test_discover_last_finished_run_older_than_three_hours_warns():
    from watchdog.run import cron_status
    log = _good_run(1) + _good_run(2)               # last run finished 02:17:06
    assert cron_status("meettrack-discover", log, _epoch("2026-09-22T05:17:06+00:00")).level == "ok"
    st = cron_status("meettrack-discover", log, _epoch("2026-09-22T05:17:07+00:00"))
    assert st.level == "warn" and st.name == "cron:meettrack-discover"
    assert "no meettrack-discover run has finished for 3.0h" in st.summary
    assert "2026-09-22T02:17:06+00:00" in st.summary
    assert "probed=0/0" in st.evidence


def test_discover_age_is_read_from_the_probed_line_of_the_last_finished_run():
    # a later blind-index run (probed= with no scraped=) is not a finished run
    from watchdog.run import cron_status
    log = _good_run(1) + _BLIND_PROBED + _BLIND_ERROR        # blind run at 02:17
    st = cron_status("meettrack-discover", log, _epoch("2026-09-22T04:30:00+00:00"))
    assert st.level == "warn"
    assert "for 3.2h" in st.summary and "2026-09-22T01:17:06+00:00" in st.summary
    assert "[discover] ERROR" in st.evidence                 # the lines that follow stay as evidence


def test_discover_stale_and_crashing_is_one_alert_stream():
    # an outage that keeps crashing warns from the log rule first, then from the age:
    # same check name and level, so the 6-hour cooldown holds
    from watchdog.run import cron_status
    from watchdog.triage import triage
    log = _good_run(1) + _CRASH_522
    first = cron_status("meettrack-discover", log, _epoch("2026-09-22T01:30:00+00:00"))
    assert first.level == "warn" and "error marker" in first.summary
    out = triage([first], {}, int(_epoch("2026-09-22T01:30:00+00:00")))
    assert out["escalate"]
    later = cron_status("meettrack-discover", log, _epoch("2026-09-22T05:00:00+00:00"))
    assert later.level == "warn" and "has finished for" in later.summary
    assert "522 Server Error" in later.evidence
    assert not triage([later], out["state"], int(_epoch("2026-09-22T05:00:00+00:00")))["escalate"]


def test_discover_unreadable_end_time_warns():
    from watchdog.run import cron_status
    log = ("[sometime] [discover] scraped=10 inserted=0 updated=10\n"
           "[sometime] [discover] probed=0/0 candidates\n")
    st = cron_status("meettrack-discover", log, _epoch("2026-09-22T03:30:00+00:00"))
    assert st.level == "warn" and "cannot read the time" in st.summary


def test_discover_log_with_no_finished_run_is_left_to_the_log_rule():
    from watchdog.run import cron_log_status, cron_status
    now = _epoch("2030-01-01T00:00:00+00:00")
    assert cron_status("meettrack-discover", "", now).level == "ok"      # an empty log is not timed
    assert cron_status("meettrack-discover", _CRASH_522, now) == cron_log_status("meettrack-discover", _CRASH_522)


def test_the_shadow_verdict_never_includes_the_age(monkeypatch, tmp_path):
    import watchdog.run as run
    log = tmp_path / "discover.cron.log"
    log.write_text(_good_run(1))
    monkeypatch.setattr(run, "CRON_LOGS", [("meettrack-discover", log)])
    monkeypatch.setattr(run, "_cmd", lambda args: "")
    monkeypatch.setattr(run, "collect_metrics", lambda now, prior: ([], {}))
    monkeypatch.setattr(run, "alertwatch_status", lambda now: None)
    statuses, _ = run.collect(int(_epoch("2026-09-23T00:00:00+00:00")), {})
    assert [s.level for s in statuses if s.name == "cron:meettrack-discover"] == ["warn"]
    assert [lvl for label, _, lvl in run.shadow_logs({}) if label == "meettrack-discover"] == ["ok"]


def test_every_age_limit_reads_a_run_end_time():
    from watchdog.run import RUN_END, RUN_MAX_AGE_H
    for label in RUN_MAX_AGE_H:
        assert "end_ts" in RUN_END[label].groupindex


def test_an_age_limit_with_no_run_end_time_warns_instead_of_stopping_the_poll(monkeypatch):
    import re
    import watchdog.run as run
    no_time = re.compile(r"^\[[^\]\n]+\] \[coverage\] season=[^\n]*$", re.MULTILINE)
    monkeypatch.setattr(run, "RUN_END", {**run.RUN_END, "meettrack-coverage": no_time})
    monkeypatch.setattr(run, "RUN_MAX_AGE_H", {"meettrack-coverage": 26, "meettrack-ingest": 1})
    now = _epoch("2026-11-02T08:00:00+00:00")
    for label in ("meettrack-coverage", "meettrack-ingest"):    # no end_ts group / no RUN_END
        st = run.cron_status(label, _cov_run(1), now)
        assert st.level == "warn" and "watchdog config" in st.summary and label in st.summary


# --- the daily coverage check log (one line a good run) ----------------------
# Lines copied from the real coverage_check.cron.log; error lines from the print
# calls in splash_poller coverage_check.py.

def _cov_run(day, failed=0, alerts=0, sent=0):
    return (f"[2026-11-{day:02d}T07:00:02+00:00] [coverage] season=2026-2027 expected=4 tracked=1 "
            f"alerts={alerts} reminders=0 blind=0 sent={sent} failed={failed}\n")


_COV_CRASH = ("Traceback (most recent call last):\n"
              '  File "/home/dev/projects/splash_poller/coverage_check.py", line 390, in _run\n'
              "requests.exceptions.HTTPError: 522 Server Error: <none> for url: "
              "https://example.supabase.co/rest/v1/meet_registry\n")
_COV_LOCK = ("[coverage] ERROR another coverage check holds "
             "/home/dev/projects/splash_poller/logs/coverage_alerts.json.lock; not run\n")
_COV_REFUSED = ("tg-send: sendMessage rejected by Telegram (HTTP 400)\n"
                "  [coverage] ERROR por-2026-2027:anl-5: Telegram refused the message (exit 1); "
                "retried next run\n")
_COV_CALENDAR = "[coverage] ERROR calendar: calendar/por-2026-2027.toml: Expected '=' at line 4\n"
_COV_WARN = ("  [coverage warn] https://x.example/meet/: ReadTimeout: read timed out; "
             "checked again next run\n")
_COV_UNCERTAIN = ("tg-send: delivery uncertain (network failure)\n"
                  "  [coverage warn] por-2026-2027:anl-5: Telegram answer uncertain; not sent again\n")


def _cov(log):
    from watchdog.run import cron_log_status
    return cron_log_status("meettrack-coverage", log)


def test_coverage_run_end_matches_each_real_good_run():
    from watchdog.run import RUN_END
    rule = RUN_END["meettrack-coverage"]
    real = ("[2026-10-29T07:00:02+00:00] [coverage] season=2026-2027 expected=2 tracked=1 alerts=1 "
            "reminders=0 blind=0 sent=1 failed=0\n"
            "[2026-11-20T07:00:01+00:00] [coverage] season=2026-2027 expected=6 tracked=3 alerts=0 "
            "reminders=0 blind=0 sent=0 failed=0\n")
    assert len(list(rule.finditer(real))) == 2
    assert _cov(real).level == "ok"
    assert _cov(_cov_run(1) * 400).level == "ok"            # past the 50-line tail


def test_coverage_crash_warns_until_the_next_good_run():
    st = _cov(_cov_run(1) + _COV_CRASH)
    assert st.level == "warn" and "522 Server Error" in st.evidence
    assert _cov(_cov_run(1) + _COV_CRASH + _cov_run(2)).level == "ok"
    # the plain tail would have kept it for weeks
    from watchdog.triage import check_cron_log
    assert check_cron_log("meettrack-coverage", _cov_run(1) + _COV_CRASH + _cov_run(2) * 40).level == "warn"


def test_coverage_refused_send_is_not_a_finished_run():
    log = _cov_run(1) + _COV_REFUSED + _cov_run(2, failed=1, alerts=1)
    st = _cov(log)
    assert st.level == "warn" and "Telegram refused" in st.evidence
    assert _cov(log + _cov_run(3)).level == "ok"            # retried and sent next run
    for failed in (12, 1000):                                # any count still starts the next run
        many = _cov_run(1) + _COV_REFUSED + _cov_run(2, failed=failed, alerts=failed)
        assert _cov(many).level == "warn"
        assert _cov(many + _cov_run(3)).level == "ok"


def test_coverage_calendar_error_warns_while_it_lasts_even_with_failed_0():
    log = _cov_run(1)
    for day in (2, 3, 4):
        log += _COV_CALENDAR + _cov_run(day)
        st = _cov(log)
        assert st.level == "warn", day
        assert "ERROR calendar" in st.evidence
    assert _cov(log + _cov_run(5)).level == "ok"            # calendar fixed


def test_coverage_calendar_error_hidden_behind_a_note_still_warns():
    # the match must not start on the line after the ERROR: the whole run counts
    st = _cov(_cov_run(1) + _COV_CALENDAR + _COV_WARN + _COV_UNCERTAIN + _cov_run(2))
    assert st.level == "warn" and "ERROR calendar" in st.evidence


def test_coverage_notes_inside_a_finished_run_are_ok():
    assert _cov(_cov_run(1) + _COV_UNCERTAIN + _COV_WARN + _cov_run(2, alerts=1, sent=1)).level == "ok"


def test_coverage_lock_error_warns_and_clears_on_the_second_good_run():
    # Known limit: the lock path prints its ERROR and no summary, so the next run's
    # text still holds that line. It clears one run later.
    assert _cov(_cov_run(1) + _COV_LOCK).level == "warn"
    assert _cov(_cov_run(1) + _COV_LOCK + _cov_run(2)).level == "warn"
    assert _cov(_cov_run(1) + _COV_LOCK + _cov_run(2) + _cov_run(3)).level == "ok"


def test_coverage_shell_error_with_no_error_word_warns():
    st = _cov(_cov_run(1) + "/bin/sh: 1: /usr/bin/python3: not found\n")
    assert st.level == "warn" and "after its last finished run" in st.summary


def test_coverage_summary_line_that_changed_warns_until_run_end_is_updated():
    def changed(day):
        return _cov_run(day).replace("failed=0", "failed=0 skipped=0")
    assert _cov(_cov_run(1) + changed(2)).level == "warn"
    st = _cov(_cov_run(1) + changed(2) + changed(3) + changed(4))
    assert st.level == "warn" and "3 line(s)" in st.summary
    # after a rotation or truncation no known summary is left: still a warning, never ok
    for log in (changed(2), changed(2) * 60, changed(2) + "\n"):
        assert _cov(log).level == "warn"


def test_coverage_a_dry_run_appended_by_hand_starts_the_next_run():
    dry = ("[dry-run] would send (alert, x):\nMeetTrack: a meet is not tracked yet.\n\n"
           + _cov_run(2, alerts=1).replace("failed=0", "failed=0 dry_run=1"))
    assert _cov(_cov_run(1) + dry).level == "warn"
    assert _cov(_cov_run(1) + dry + _cov_run(3)).level == "ok"


# --- coverage freshness: a daily cron that stops (or a flock -n skip) writes nothing

def _cov_age(log, now_iso):
    from watchdog.run import cron_status
    return cron_status("meettrack-coverage", log, _epoch(now_iso))


def test_coverage_last_finished_run_older_than_26_hours_warns():
    log = _cov_run(1) + _cov_run(2)                       # last run finished 2026-11-02T07:00:02
    assert _cov_age(log, "2026-11-03T07:00:00+00:00").level == "ok"   # the next run is still writing
    assert _cov_age(log, "2026-11-03T09:00:02+00:00").level == "ok"
    st = _cov_age(log, "2026-11-03T09:00:03+00:00")
    assert st.level == "warn" and st.name == "cron:meettrack-coverage"
    assert "no meettrack-coverage run has finished for 26.0h" in st.summary
    assert "2026-11-02T07:00:02+00:00" in st.summary
    assert st.evidence.startswith("[2026-11-02T07:00:02+00:00] [coverage] season=")


def test_coverage_age_survives_the_autumn_clock_change():
    # the cron runs on Lisbon time: 06:00 UTC on the last summer day, 07:00 UTC the
    # day after the change, a 25h gap. Every poll in between stays ok, even one that
    # reads the log a minute late, before the new run's line is in.
    summer = _cov_run(24).replace("2026-11-24T07:00:02", "2026-10-24T06:00:02")
    winter = _cov_run(25).replace("2026-11-25T07:00:02", "2026-10-25T07:00:02")
    start = _epoch("2026-10-24T06:30:59+00:00")
    from watchdog.run import cron_status
    for poll in range(50):                                # 06:30 Sat .. 07:00 Sun, every 30 min
        now = start + poll * 1800
        assert cron_status("meettrack-coverage", summer, now).level == "ok", poll
    assert _cov_age(summer + winter, "2026-10-25T07:30:00+00:00").level == "ok"
    assert _cov_age(summer, "2026-10-25T08:30:00+00:00").level == "warn"  # that run missed


def test_coverage_age_counts_from_the_last_finished_run():
    # a refused send or a calendar ERROR is not a finished run, so the age keeps growing
    for bad in (_COV_REFUSED + _cov_run(2, failed=1, alerts=1), _COV_CALENDAR + _cov_run(2)):
        st = _cov_age(_cov_run(1) + bad, "2026-11-02T09:30:00+00:00")
        assert st.level == "warn" and "has finished for 26.5h" in st.summary
        assert "2026-11-01T07:00:02+00:00" in st.summary
        assert "ERROR" in st.evidence                     # the failed run's lines stay as evidence


def test_coverage_crash_then_stale_is_one_alert_stream():
    from watchdog.triage import triage
    log = _cov_run(1) + _COV_CRASH                        # the 2026-11-02 run crashed
    first = _cov_age(log, "2026-11-02T07:30:00+00:00")
    assert first.level == "warn" and "error marker" in first.summary
    out = triage([first], {}, int(_epoch("2026-11-02T07:30:00+00:00")))
    assert out["escalate"]
    later = _cov_age(log, "2026-11-02T09:30:00+00:00")
    assert later.level == "warn" and "has finished for 26.5h" in later.summary
    assert "522 Server Error" in later.evidence
    assert not triage([later], out["state"], int(_epoch("2026-11-02T09:30:00+00:00")))["escalate"]


def test_coverage_unreadable_end_time_warns():
    log = _cov_run(1).replace("2026-11-01T07:00:02+00:00", "sometime")
    assert _cov(log).level == "ok"                        # the log rule alone is happy
    st = _cov_age(log, "2026-11-01T08:00:00+00:00")
    assert st.level == "warn" and "cannot read the time" in st.summary


def test_coverage_log_with_no_finished_run_is_left_to_the_log_rule():
    from watchdog.run import cron_log_status
    now = "2030-01-01T00:00:00+00:00"
    assert _cov_age("", now).level == "ok"                 # an empty log is not timed
    assert _cov_age(_COV_CRASH, now) == cron_log_status("meettrack-coverage", _COV_CRASH)


def test_collect_reports_log_coverage(monkeypatch, tmp_path):
    import watchdog.run as run
    monkeypatch.setattr(run, "CRON_LOGS", [("gone", tmp_path / "gone.log")])
    monkeypatch.setattr(run, "_cmd", lambda args: "")
    monkeypatch.setattr(run, "collect_metrics", lambda now, prior: ([], {}))
    statuses, _ = run.collect(1_800_000_000, {})
    coverage = [s for s in statuses if s.name == "cron-logs:coverage"]
    assert len(coverage) == 1 and coverage[0].level == "warn"


# --- Jev shadow wiring: it may watch, it may never steer ---------------------

def _main_env(monkeypatch, tmp_path, run):
    for var, name in (("WATCHDOG_STATE", "state.json"), ("WATCHDOG_PENDING", "pending.json"),
                      ("WATCHDOG_DELIVERY_LAST", "last.json"), ("WATCHDOG_METRICS", "metrics.json")):
        monkeypatch.setenv(var, str(tmp_path / name))
    monkeypatch.delenv("WATCHDOG_JEV_SHADOW", raising=False)
    monkeypatch.setattr(run, "collect", lambda now, prior: ([CheckStatus("disk", "ok", "fine")], {}))


def test_shadow_logs_cover_cron_logs_and_extra_logs_with_the_rules_verdict(monkeypatch, tmp_path):
    import watchdog.run as run
    (tmp_path / "a.log").write_text("run FAILED rc=1\n")
    (tmp_path / "b.log").write_text("all good\n")
    monkeypatch.setattr(run, "CRON_LOGS", [("a", tmp_path / "a.log"), ("gone", tmp_path / "gone.log")])
    monitors = {"jev_shadow": {"logs": [{"name": "b", "log": str(tmp_path / "b.log")}]}}
    assert run.shadow_logs(monitors) == [("a", "run FAILED rc=1\n", "warn"), ("b", "all good\n", "ok")]


def test_main_runs_the_shadow_pass_and_output_is_unchanged(monkeypatch, tmp_path, capsys):
    import watchdog.run as run
    _main_env(monkeypatch, tmp_path, run)
    monkeypatch.setattr(run, "_load_monitors", lambda: {"jev_shadow": {"enabled": False}})
    run.main([]); without = capsys.readouterr().out

    calls = []
    monkeypatch.setattr(run, "_load_monitors", lambda: {"jev_shadow": {"enabled": True}})
    monkeypatch.setattr(run.jev_shadow, "load_key", lambda: "k")
    monkeypatch.setattr(run.jev_shadow, "shadow_pass", lambda logs, **kw: calls.append(kw) or 1)
    run.main([]); with_shadow = capsys.readouterr().out
    assert len(calls) == 1
    assert with_shadow == without


def test_main_dry_run_never_calls_jev(monkeypatch, tmp_path, capsys):
    import watchdog.run as run
    _main_env(monkeypatch, tmp_path, run)
    calls = []
    monkeypatch.setattr(run, "_load_monitors", lambda: {"jev_shadow": {"enabled": True}})
    monkeypatch.setattr(run.jev_shadow, "shadow_pass", lambda logs, **kw: calls.append(1))
    run.main(["--dry-run"])
    assert calls == []


def test_main_survives_a_shadow_pass_that_raises(monkeypatch, tmp_path, capsys):
    import watchdog.run as run
    _main_env(monkeypatch, tmp_path, run)
    monkeypatch.setattr(run, "_load_monitors", lambda: {"jev_shadow": {"enabled": True}})
    def boom(logs, **kw):
        raise RuntimeError("anything")
    monkeypatch.setattr(run.jev_shadow, "shadow_pass", boom)
    assert run.main([]) == 0
    assert "WATCHDOG_JSON:" in capsys.readouterr().out


def test_shadow_is_off_unless_the_config_turns_it_on(monkeypatch, tmp_path, capsys):
    import watchdog.run as run
    _main_env(monkeypatch, tmp_path, run)
    calls = []
    monkeypatch.setattr(run, "_load_monitors", lambda: {})
    monkeypatch.setattr(run.jev_shadow, "shadow_pass", lambda logs, **kw: calls.append(1))
    run.main([])
    assert calls == []


def test_env_kill_switch_beats_the_config(monkeypatch, tmp_path, capsys):
    import watchdog.run as run
    _main_env(monkeypatch, tmp_path, run)
    monkeypatch.setenv("WATCHDOG_JEV_SHADOW", "0")
    calls = []
    monkeypatch.setattr(run, "_load_monitors", lambda: {"jev_shadow": {"enabled": True}})
    monkeypatch.setattr(run.jev_shadow, "shadow_pass", lambda logs, **kw: calls.append(1))
    run.main([])
    assert calls == []


def test_shadow_logs_drops_a_second_source_that_reuses_a_label(monkeypatch, tmp_path):
    # Shadow state is keyed by label: two files under one label would mask each other.
    import watchdog.run as run
    (tmp_path / "a.log").write_text("first\n")
    (tmp_path / "other.log").write_text("second\n")
    monkeypatch.setattr(run, "CRON_LOGS", [("a", tmp_path / "a.log")])
    monitors = {"jev_shadow": {"logs": [{"name": "a", "log": str(tmp_path / "other.log")}]}}
    assert run.shadow_logs(monitors) == [("a", "first\n", "ok")]


def test_shadow_logs_refuses_an_out_of_scope_log_even_if_the_config_lists_it(monkeypatch, tmp_path):
    import watchdog.run as run
    bad = tmp_path / "sat-prep" / "bento-sync.log"; bad.parent.mkdir(); bad.write_text("rows\n")
    monkeypatch.setattr(run, "CRON_LOGS", [])
    assert run.shadow_logs({"jev_shadow": {"logs": [{"name": "bento", "log": str(bad)}]}}) == []


def test_no_log_is_read_for_the_shadow_when_there_is_no_key(monkeypatch, tmp_path, capsys):
    import watchdog.run as run
    _main_env(monkeypatch, tmp_path, run)
    reads = []
    monkeypatch.setattr(run, "_load_monitors", lambda: {"jev_shadow": {"enabled": True}})
    monkeypatch.setattr(run.jev_shadow, "load_key", lambda: None)
    monkeypatch.setattr(run, "shadow_logs", lambda monitors: reads.append(1) or [])
    run.main([])
    assert reads == []
