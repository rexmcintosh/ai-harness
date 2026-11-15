import re
import stat

import pytest

from alertwatch import inbox, ledger

NOW = 1794738000.0  # 2026-11-15T10:20:00Z


def rec(run_id, started, status="ok", merges=()):
    return {"run_id": run_id, "started_at": started, "status": status,
            "result": {"merges": list(merges)} if merges else None}


def test_new_run_id_shape():
    assert re.fullmatch(r"20261115T102000Z-[0-9a-f]{6}", ledger.new_run_id(NOW))


def test_write_and_update_run(tmp_path):
    inbox.ensure_dirs(tmp_path)
    p = ledger.write_run(tmp_path, rec("r1", NOW))
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    ledger.update_run(tmp_path, "r1", status="failed", reason="x")
    (got,) = ledger.recent_runs(tmp_path, NOW, hours=1)
    assert got["status"] == "failed" and got["reason"] == "x"
    with pytest.raises(FileExistsError):
        ledger.write_run(tmp_path, rec("r1", NOW))


def test_recent_runs_newest_first_and_windowed(tmp_path):
    inbox.ensure_dirs(tmp_path)
    ledger.write_run(tmp_path, rec("a", NOW - 7200))
    ledger.write_run(tmp_path, rec("b", NOW - 60))
    ledger.write_run(tmp_path, rec("c", NOW - 90000))
    assert [r["run_id"] for r in ledger.recent_runs(tmp_path, NOW, hours=24)] == ["b", "a"]


def test_counts_use_rolling_windows(tmp_path):
    inbox.ensure_dirs(tmp_path)
    ledger.write_run(tmp_path, rec("a", NOW - 1800))
    ledger.write_run(tmp_path, rec("b", NOW - 7200))
    ledger.write_run(tmp_path, rec("c", NOW - 90000))
    c = ledger.counts(tmp_path, NOW)
    assert c["runs_hour"] == 1 and c["runs_day"] == 2


def test_skipped_runs_do_not_count_toward_caps(tmp_path):
    inbox.ensure_dirs(tmp_path)
    ledger.write_run(tmp_path, rec("a", NOW - 60, status="skipped"))
    assert ledger.counts(tmp_path, NOW)["runs_hour"] == 0


def test_merges_day_ignores_reverted(tmp_path):
    inbox.ensure_dirs(tmp_path)
    ledger.write_run(tmp_path, rec("a", NOW - 60, merges=[{"repo": "x", "reverted": False},
                                                          {"repo": "y", "reverted": True}]))
    ledger.write_run(tmp_path, rec("b", NOW - 90000, merges=[{"repo": "x", "reverted": False}]))
    assert ledger.counts(tmp_path, NOW)["merges_day"] == 1


def test_fail_streak_skips_limit_runs(tmp_path):
    inbox.ensure_dirs(tmp_path)
    for i, status in enumerate(["failed", "ok", "failed", "limit", "failed"]):  # oldest first
        ledger.write_run(tmp_path, rec(f"r{i}", NOW - 1000 + i, status=status))
    assert ledger.fail_streak(tmp_path) == 2


def test_fail_streak_ignores_skipped(tmp_path):
    inbox.ensure_dirs(tmp_path)
    for i, status in enumerate(["failed", "skipped", "failed"]):
        ledger.write_run(tmp_path, rec(f"r{i}", NOW - 1000 + i, status=status))
    assert ledger.fail_streak(tmp_path) == 2


def test_unreadable_run_file_is_skipped(tmp_path):
    inbox.ensure_dirs(tmp_path)
    (tmp_path / "runs" / "bad.json").write_text("{")
    ledger.write_run(tmp_path, rec("a", NOW - 60))
    assert [r["run_id"] for r in ledger.recent_runs(tmp_path, NOW, hours=1)] == ["a"]


def test_write_run_is_whole_or_absent(tmp_path, monkeypatch):
    inbox.ensure_dirs(tmp_path)

    def crash(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(ledger.os, "link", crash)
    with pytest.raises(OSError):
        ledger.write_run(tmp_path, rec("r1", NOW))
    assert not (tmp_path / "runs" / "r1.json").exists()
    assert not list((tmp_path / "runs").glob(".tmp-*"))


def test_replace_run_repairs_a_broken_record(tmp_path):
    inbox.ensure_dirs(tmp_path)
    (tmp_path / "runs" / "r1.json").write_text("{broken")
    ledger.replace_run(tmp_path, rec("r1", NOW, status="failed"))
    assert ledger.recent_runs(tmp_path, NOW, 1)[0]["status"] == "failed"
