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


def test_loom_cron_log_points_at_the_file_loom_writes():
    # run-absorb.sh writes loom/logs/runs.log; "absorb.log" never existed, so the check read nothing.
    from watchdog.run import CRON_LOGS
    paths = {label: str(path) for label, path in CRON_LOGS}
    assert paths["loom"].endswith("loom/logs/runs.log")


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
