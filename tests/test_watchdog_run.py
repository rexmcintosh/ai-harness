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
    # a fresh log whose runs all crashed, or a summary line that no longer fits RUN_END
    from watchdog.run import cron_log_status
    drifted = "[2026-11-20T16:17:06+00:00] [discover] done: 44 meets, 1 probed\n" * 30
    for log in (_CRASH_522, drifted):
        st = cron_log_status("meettrack-discover", log)
        assert st.level == "warn"
        assert "no finished run" in st.summary


def test_discover_probe_warn_inside_a_finished_run_is_ok():
    # One meet's probe failing is skipped by discovery itself; the run still finishes.
    from watchdog.run import cron_log_status
    ts = "2026-09-22T03:17:06+00:00"
    log = (f"[{ts}] [discover] scraped=10 inserted=0 updated=10 delisted=0\n"
           "  [probe warn] 123: HTTPSConnectionPool(host='x.example', port=443): "
           "Max retries exceeded (Caused by ConnectTimeoutError)\n"
           f"[{ts}] [discover] probed=0/1 candidates\n")
    assert cron_log_status("meettrack-discover", log).level == "ok"


def test_discover_probe_warn_with_no_end_line_after_it_counts():
    # A run that never printed probed= (crashed, or not yet flushed) is judged in full.
    from watchdog.run import cron_log_status
    ts = "2026-09-22T03:17:06+00:00"
    log = (_good_run(2)
           + f"[{ts}] [discover] scraped=10 inserted=0 updated=10 delisted=0\n"
           "  [probe warn] 123: 503 Server Error: Service Unavailable for url: https://x.example/\n")
    assert cron_log_status("meettrack-discover", log).level == "warn"


def test_other_logs_keep_the_plain_tail_rule():
    from watchdog.run import cron_log_status
    from watchdog.triage import check_cron_log
    log = _good_run(1) + _CRASH_522 + _good_run(3)
    for label in ("meettrack-ingest", "meettrack-supervise", "meettrack-coverage"):
        assert cron_log_status(label, log) == check_cron_log(label, log)
        assert cron_log_status(label, log).level == "warn"


def test_collect_and_the_shadow_use_the_same_discover_rule(monkeypatch, tmp_path):
    import watchdog.run as run
    log = tmp_path / "discover.cron.log"
    log.write_text(_good_run(1) + _CRASH_522 + _good_run(3))
    monkeypatch.setattr(run, "CRON_LOGS", [("meettrack-discover", log)])
    monkeypatch.setattr(run, "_cmd", lambda args: "")
    monkeypatch.setattr(run, "collect_metrics", lambda now, prior: ([], {}))
    monkeypatch.setattr(run, "alertwatch_status", lambda now: None)
    statuses, _ = run.collect(1_800_000_000, {})
    assert [s.level for s in statuses if s.name == "cron:meettrack-discover"] == ["ok"]
    assert [lvl for label, _, lvl in run.shadow_logs({}) if label == "meettrack-discover"] == ["ok"]


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
