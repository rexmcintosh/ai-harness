"""The restart helper with a fake registry read and a real dummy 'writer' process."""
import dataclasses
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from alertwatch import ledger, restart
from alertwatch.config import load_config


def starttime(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("ALERT_WATCH_RUN_ID", "run-r")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    cfg = load_config(env={"ALERT_WATCH_STATE": str(state)})
    cfg = dataclasses.replace(cfg, mode="act", meettrack_run_dir=run_dir)
    procs = []

    def writer(key, script="poller.py", record_start=True, spoof=False, url=None, pid_key=None):
        path = tmp_path / script
        path.write_text("import time\ntime.sleep(60)\n")
        url = url or f"https://live.swimrankings.net/{key}/"
        argv = ([sys.executable, "-c", "import time; time.sleep(60)", script] if spoof
                else [sys.executable, str(path), "--meet-url", url, "--live"])
        p = subprocess.Popen(argv)
        procs.append(p)
        time.sleep(0.05)
        (run_dir / f"poller-{pid_key or key}.pid").write_text(
            f"{p.pid} {starttime(p.pid)}" if record_start else str(p.pid))
        return p

    yield {"cfg": cfg, "state": state, "run_dir": run_dir, "writer": writer}
    for p in procs:
        p.kill()
        p.wait()


def registry(attempts, status="polling", meet_url=None):
    def select(table, filters, cols):
        assert table == "meet_registry" and filters.startswith("sr_meet_id=eq.")
        return [{"sr_meet_id": filters.split(".", 1)[1], "poller_attempts": attempts, "ingest_status": status,
                 "meet_url": meet_url}]
    return select


def test_restart_sends_term_and_logs(world, capsys):
    p = world["writer"]("11539")
    assert restart.main(["restart", "11539"], cfg=world["cfg"], select=registry(2)) == 0
    assert p.wait(timeout=5) != 0
    (act,) = ledger.actions(world["state"])
    assert act["kind"] == "restart" and act["key"] == "11539" and act["pid"] == p.pid
    assert act["attempts"] == 2 and act["run_id"] == "run-r"
    assert json.loads(capsys.readouterr().out)["status"] == "restarted"


def test_refused_near_the_supervisor_cap(world):
    p = world["writer"]("11539")
    assert restart.main(["restart", "11539"], cfg=world["cfg"], select=registry(4)) == 2
    assert p.poll() is None
    assert ledger.actions(world["state"]) == []


def test_refused_in_diagnose_mode(world):
    p = world["writer"]("11539")
    cfg = dataclasses.replace(world["cfg"], mode="diagnose")
    assert restart.main(["restart", "11539"], cfg=cfg, select=registry(0)) == 2
    assert p.poll() is None


def test_refused_without_a_pid_file(world):
    assert restart.main(["restart", "11539"], cfg=world["cfg"], select=registry(0)) == 2


def test_refused_when_the_pid_was_reused(world):
    p = world["writer"]("11539")
    (world["run_dir"] / "poller-11539.pid").write_text(f"{p.pid} 1")
    assert restart.main(["restart", "11539"], cfg=world["cfg"], select=registry(0)) == 2
    assert p.poll() is None


def test_refused_when_the_process_is_not_a_writer(world):
    p = world["writer"]("11539", script="something_else.py")
    assert restart.main(["restart", "11539"], cfg=world["cfg"], select=registry(0)) == 2
    assert p.poll() is None


def test_refused_when_no_single_registry_row(world):
    world["writer"]("11539")
    assert restart.main(["restart", "11539"], cfg=world["cfg"], select=lambda *a: []) == 2


def test_refused_bad_key(world):
    assert restart.main(["restart", "../x"], cfg=world["cfg"], select=registry(0)) == 2


def test_each_writer_kind_is_accepted(world):
    for i, script in enumerate(("track_pdf_meet.py", "lastheat_ingest.py")):
        url = f"https://www.anlisboa.info/results/meet{i}/"
        p = world["writer"](f"sp-k{i}", script=script, url=url)
        assert restart.main(["restart", f"sp-k{i}"], cfg=world["cfg"],
                            select=registry(0, meet_url=url.rstrip("/"))) == 0
        p.wait(timeout=5)


def test_refused_when_the_pid_file_points_at_another_meets_writer(world):
    other = world["writer"]("11600", pid_key="11539")  # a crossed or stale PID file
    assert restart.main(["restart", "11539"], cfg=world["cfg"], select=registry(0)) == 2
    assert other.poll() is None
    assert ledger.actions(world["state"]) == []


def test_history_lists_restarts(world, capsys):
    world["writer"]("11539")
    restart.main(["restart", "11539"], cfg=world["cfg"], select=registry(1))
    capsys.readouterr()
    assert restart.main(["history"], cfg=world["cfg"], select=registry(1)) == 0
    (line,) = capsys.readouterr().out.strip().splitlines()
    assert json.loads(line)["key"] == "11539"


def test_refused_when_a_writer_name_is_only_an_argument(world):
    p = world["writer"]("11539", spoof=True)
    assert restart.main(["restart", "11539"], cfg=world["cfg"], select=registry(0)) == 2
    assert p.poll() is None


def test_refused_without_a_start_time_in_the_pid_file(world):
    p = world["writer"]("11539", record_start=False)
    assert restart.main(["restart", "11539"], cfg=world["cfg"], select=registry(0)) == 2
    assert p.poll() is None


def test_writer_that_exits_during_the_checks_is_refused_not_mistargeted(world):
    p = world["writer"]("11539")

    def select_then_writer_dies(*a):
        p.kill()
        p.wait()
        return registry(0)(*a)
    assert restart.main(["restart", "11539"], cfg=world["cfg"], select=select_then_writer_dies) == 2
    assert ledger.actions(world["state"]) == []


def test_reads_only_real_registry_columns(world):
    # meet_registry has no `status` column (a live read on 2026-11-15 returned 42703).
    seen = []

    def select(table, filters, cols):
        seen.append(cols)
        return registry(0)(table, filters, cols)
    world["writer"]("11539")
    restart.main(["restart", "11539"], cfg=world["cfg"], select=select)
    real = set("sr_meet_id,nation,feed_type,ingest_status,start_date,end_date,poller_attempts,"
               "poller_started_at,meet_url,dispatch_paused".split(","))
    assert set(seen[0].split(",")) <= real
