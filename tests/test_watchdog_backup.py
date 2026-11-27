"""The watchdog's off-site backup check."""
import json
import time
from datetime import datetime, timezone

from watchdog import run
from watchdog.triage import check_backup

NOW = 1_800_000_000.0


def record(hours_ago, verified=True):
    when = datetime.fromtimestamp(NOW - hours_ago * 3600, timezone.utc).isoformat()
    return json.dumps({"status": "ok", "verified": verified, "verified_at": when})


def test_fresh_backup_is_ok():
    s = check_backup(record(5), NOW)
    assert s.level == "ok" and "5 h" in s.summary


def test_missed_day_warns_then_crit():
    assert check_backup(record(31), NOW).level == "warn"
    assert check_backup(record(60), NOW).level == "crit"


def test_unverified_or_missing_or_bad_record_warns():
    assert check_backup(record(1, verified=False), NOW).level == "warn"
    assert check_backup(None, NOW).level == "warn"
    assert check_backup("{not json", NOW).level == "warn"
    assert check_backup(json.dumps({"verified": True}), NOW).level == "warn"


def test_collect_reads_the_backup_state(tmp_path, monkeypatch):
    state = tmp_path / "restic-offsite"
    state.mkdir()
    (state / "last-success.json").write_text(record(40))
    monkeypatch.setenv("RESTIC_OFFSITE_STATE", str(state))
    statuses, _ = run.collect(int(NOW))
    backup = [s for s in statuses if s.name == "backup"]
    assert len(backup) == 1 and backup[0].level == "warn"


def test_collect_skips_backup_where_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("RESTIC_OFFSITE_STATE", str(tmp_path / "absent"))
    statuses, _ = run.collect(int(time.time()))
    assert not [s for s in statuses if s.name == "backup"]
