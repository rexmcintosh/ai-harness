import time

from alertwatch import inbox, ledger
from watchdog import run
from watchdog.triage import check_alertwatch


def test_stale_inbox_warns():
    s = check_alertwatch(1500.0, paused=False, newest_run_status="ok")
    assert s.name == "alertwatch" and s.level == "warn" and "25 min" in s.summary


def test_fresh_inbox_is_ok():
    assert check_alertwatch(300.0, paused=False, newest_run_status="ok").level == "ok"
    assert check_alertwatch(None, paused=False, newest_run_status=None).level == "ok"


def test_paused_with_stale_inbox_is_ok():
    s = check_alertwatch(99999.0, paused=True, newest_run_status="failed")
    assert s.level == "ok" and "paused" in s.summary


def test_failed_newest_run_warns():
    s = check_alertwatch(None, paused=False, newest_run_status="failed")
    assert s.level == "warn" and "failed" in s.summary


def test_limit_newest_run_is_ok():
    assert check_alertwatch(None, paused=False, newest_run_status="limit").level == "ok"


def test_missing_state_dir_adds_no_check(tmp_path, monkeypatch):
    monkeypatch.setenv("ALERT_WATCH_STATE", str(tmp_path / "missing"))
    assert run.alertwatch_status(time.time()) is None


def test_status_reads_inbox_and_runs(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("ALERT_WATCH_STATE", str(state))
    inbox.ensure_dirs(state)
    (state / "ENABLED").write_text("")
    now = time.time()
    ledger.write_run(state, {"run_id": "a", "started_at": now - 60, "status": "failed"})
    ledger.write_run(state, {"run_id": "b", "started_at": now - 30, "status": "skipped"})
    s = run.alertwatch_status(now)
    assert s.level == "warn" and "failed" in s.summary


def test_status_with_paused_file(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("ALERT_WATCH_STATE", str(state))
    inbox.ensure_dirs(state)
    (state / "ENABLED").write_text("")
    (state / "PAUSED").write_text("x")
    assert run.alertwatch_status(time.time()).level == "ok"


def test_capture_only_stage_adds_no_check(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("ALERT_WATCH_STATE", str(state))
    inbox.ensure_dirs(state)
    inbox.add_entry(state, "old report", "job")
    assert run.alertwatch_status(time.time() + 99999) is None


def test_a_note_that_may_not_have_arrived_warns():
    for status in ("failed", "uncertain"):
        s = check_alertwatch(None, paused=False, newest_run_status="ok", note_status=status)
        assert s.level == "warn" and status in s.summary
    assert check_alertwatch(None, paused=False, newest_run_status="ok", note_status="accepted").level == "ok"


def test_a_dropped_note_warns_for_a_day(tmp_path, monkeypatch):
    import json
    state = tmp_path / "state"
    monkeypatch.setenv("ALERT_WATCH_STATE", str(state))
    inbox.ensure_dirs(state)
    (state / "ENABLED").write_text("")
    now = time.time()
    (state / "state.json").write_text(json.dumps({"note_dropped_at": now - 3600}))
    assert run.alertwatch_status(now).level == "warn"
    assert run.alertwatch_status(now + 90000).level == "ok"
