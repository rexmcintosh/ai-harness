"""watchdog.fix_jobs: rerun a missed job once, with no AI, sharing alert-watch's rerun limit."""
import dataclasses
import fcntl
import time
from datetime import datetime, timezone

import pytest

from alertwatch import rerun as rerun_mod
from alertwatch.config import RerunJob, load_config
from watchdog import fix_jobs, fixkit, run


def line(epoch, failed=0):
    ts = datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")
    return (f"[{ts}] [coverage] season=2026-2027 expected=6 tracked=5 alerts=0 reminders=0 "
            f"blind=0 sent=0 failed={failed}\n")


@pytest.fixture
def world(tmp_path, monkeypatch):
    log = tmp_path / "coverage_check.cron.log"
    lock = tmp_path / "coverage.lock"
    script = tmp_path / "job.py"
    # The job appends a good summary line with the current time, like coverage_check.py.
    script.write_text(
        "import sys, datetime\n"
        "ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')\n"
        "print(f'[{ts}] [coverage] season=2026-2027 expected=6 tracked=5 alerts=0 "
        "reminders=0 blind=0 sent=0 failed=0')\n")
    job = RerunJob(cmd=f"python3 {script}", cwd=str(tmp_path), lock=str(lock), log=str(log), timeout_s=30)
    cfg = load_config(env={"ALERT_WATCH_STATE": str(tmp_path / "aw")})
    cfg = dataclasses.replace(cfg, mode="diagnose", rerun={"coverage_check": job})
    monkeypatch.setattr(run, "CRON_LOGS", [("meettrack-coverage", log)])
    monkeypatch.setattr(fixkit, "STATE", tmp_path / "heal")
    sent = []
    monkeypatch.setattr(fixkit, "tg_send", lambda text: sent.append(text) or True)
    return dataclasses.make_dataclass("W", ["log", "lock", "cfg", "sent", "script", "job"])(
        log, lock, cfg, sent, script, job)


def go(world, argv=()):
    return fix_jobs.main(list(argv), rerun=rerun_mod.rerun, cfg=world.cfg)


def test_missed_run_is_rerun_once_even_in_diagnose_mode(world):
    world.log.write_text(line(time.time() - 39 * 3600))
    go(world)
    assert len(world.sent) == 1 and world.sent[0].startswith("RERAN: no meettrack-coverage run")
    assert "--- fix-jobs-rerun coverage_check" in world.log.read_text()
    # Fixed now, and the 6-hour mark holds even if it goes stale again at once.
    go(world)
    assert len(world.sent) == 1


def test_failing_job_is_not_rerun(world):
    world.log.write_text(line(time.time() - 39 * 3600) + "Traceback (most recent call last):\n  boom\n")
    go(world)
    assert world.sent == [] and "fix-jobs-rerun" not in world.log.read_text()


def test_recent_run_does_nothing(world):
    world.log.write_text(line(time.time() - 3600))
    go(world)
    assert world.sent == [] and "fix-jobs-rerun" not in world.log.read_text()


def test_rerun_that_does_not_fix_it_is_a_problem(world):
    world.script.write_text("print('[x] [coverage] ERROR calendar: down')\n")
    world.log.write_text(line(time.time() - 39 * 3600))
    go(world)
    assert len(world.sent) == 1 and world.sent[0].startswith("PROBLEM: no meettrack-coverage run")


def test_job_running_now_is_left_alone(world):
    world.log.write_text(line(time.time() - 39 * 3600))
    with open(world.lock, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        go(world)
    assert world.sent == []
    go(world)                         # not counted: free again, so it runs now
    assert len(world.sent) == 1 and world.sent[0].startswith("RERAN")


def test_alert_watch_rerun_uses_up_the_same_window(world):
    world.log.write_text(line(time.time() - 39 * 3600))
    act = dataclasses.replace(world.cfg, mode="act", rerun={"coverage_check": dataclasses.replace(
        world.job, cmd="true")})
    assert rerun_mod.main(["coverage_check"], cfg=act) == 0     # the alert-watch run's rerun
    go(world)
    assert world.sent == []


def test_paused_and_dry_run_change_nothing(world, capsys):
    world.log.write_text(line(time.time() - 39 * 3600))
    (fixkit.STATE).mkdir(parents=True, exist_ok=True)
    (fixkit.STATE / "pause").write_text("")
    go(world)
    assert world.sent == []
    (fixkit.STATE / "pause").unlink()
    go(world, ["--dry-run"])
    assert world.sent == [] and "would rerun coverage_check" in capsys.readouterr().out
    assert "fix-jobs-rerun" not in world.log.read_text()


def test_every_listed_job_has_an_age_limit_and_a_rerun_entry():
    cfg = load_config()
    for label, job in fix_jobs.JOBS.items():
        assert label in run.RUN_MAX_AGE_H and label in dict(run.CRON_LOGS)
        assert job in cfg.rerun and cfg.rerun[job].log == str(dict(run.CRON_LOGS)[label])


def test_rerun_with_an_error_before_a_fresh_summary_is_a_problem(world):
    world.script.write_text(
        "import datetime\n"
        "ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')\n"
        "print(f'[{ts}] [coverage] ERROR calendar: down')\n"
        "print(f'[{ts}] [coverage] season=2026-2027 expected=6 tracked=5 alerts=0 "
        "reminders=0 blind=0 sent=0 failed=0')\n")
    world.log.write_text(line(time.time() - 39 * 3600))
    go(world)
    assert len(world.sent) == 1 and world.sent[0].startswith("PROBLEM")
