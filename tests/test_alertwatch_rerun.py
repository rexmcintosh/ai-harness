import dataclasses
import json
import time
from pathlib import Path

from alertwatch import rerun
from alertwatch.config import RerunJob, load_config


def cfg_with(tmp_path, cmd, lock=""):
    cfg = load_config(env={"ALERT_WATCH_STATE": str(tmp_path / "state")})
    job = RerunJob(cmd=cmd, cwd=str(tmp_path), lock=lock, log=str(tmp_path / "job.log"), timeout_s=30)
    return dataclasses.replace(cfg, mode="act", rerun={"demo": job})


def test_unknown_job_refused(tmp_path, capsys):
    assert rerun.main(["nope"], cfg=cfg_with(tmp_path, "true")) == 2
    assert "not on the rerun list" in capsys.readouterr().err


def test_runs_with_guard_removed_and_source_tagged(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETTRACK_DB_READ_ONLY", "1")
    out = tmp_path / "env.json"
    cmd = f"python3 -c 'import json, os; json.dump(dict(os.environ), open(\"{out}\", \"w\"))'"
    assert rerun.main(["demo"], cfg=cfg_with(tmp_path, cmd)) == 0
    env = json.loads(out.read_text())
    assert "MEETTRACK_DB_READ_ONLY" not in env
    assert env["ALERT_SOURCE"] == "alert-watch-rerun:demo"


def test_output_goes_to_the_job_log_and_rc_passes_through(tmp_path, capsys):
    assert rerun.main(["demo"], cfg=cfg_with(tmp_path, "echo hello-from-job; exit 4")) == 4
    assert "hello-from-job" in (tmp_path / "job.log").read_text()
    assert "rerun demo: rc=4" in capsys.readouterr().out


def test_second_rerun_within_window_refused(tmp_path, capsys):
    cfg = cfg_with(tmp_path, "true")
    assert rerun.main(["demo"], cfg=cfg) == 0
    assert rerun.main(["demo"], cfg=cfg) == 2
    assert "already rerun" in capsys.readouterr().err


def test_rerun_allowed_after_window(tmp_path):
    cfg = cfg_with(tmp_path, "true")
    t0 = time.time()
    assert rerun.main(["demo"], cfg=cfg, now=lambda: t0) == 0
    assert rerun.main(["demo"], cfg=cfg, now=lambda: t0 + cfg.rerun_window_s + 1) == 0


def test_busy_lock_reported(tmp_path, capsys):
    import fcntl
    lock = tmp_path / "job.lock"
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc = rerun.main(["demo"], cfg=cfg_with(tmp_path, "true", lock=str(lock)))
    assert rc == 1
    assert "lock busy" in capsys.readouterr().out


def test_job_timeout(tmp_path, capsys):
    cfg = cfg_with(tmp_path, "sleep 30")
    cfg = dataclasses.replace(cfg, rerun={"demo": dataclasses.replace(cfg.rerun["demo"], timeout_s=1)})
    t0 = time.time()
    assert rerun.main(["demo"], cfg=cfg) == 124
    assert time.time() - t0 < 10
    assert "timed out" in capsys.readouterr().out


def test_supervisor_never_listed():
    cfg = load_config(env={})
    assert "supervise_meets" not in cfg.rerun
    assert all("supervise_meets.py" not in job.cmd for job in cfg.rerun.values())


def test_busy_lock_gives_the_rerun_back(tmp_path):
    import fcntl
    lock = tmp_path / "job.lock"
    cfg = cfg_with(tmp_path, "true", lock=str(lock))
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert rerun.main(["demo"], cfg=cfg) == 1
    assert rerun.main(["demo"], cfg=cfg) == 0


def test_spawn_failure_gives_the_rerun_back(tmp_path):
    cfg = cfg_with(tmp_path, "true")
    cfg = dataclasses.replace(cfg, rerun={"demo": dataclasses.replace(cfg.rerun["demo"],
                                                                      cwd=str(tmp_path / "missing"))})
    assert rerun.main(["demo"], cfg=cfg) == 1
    good = dataclasses.replace(cfg, rerun={"demo": dataclasses.replace(cfg.rerun["demo"], cwd=str(tmp_path))})
    assert rerun.main(["demo"], cfg=good) == 0


def test_a_failed_job_keeps_its_reservation(tmp_path):
    cfg = cfg_with(tmp_path, "exit 3")
    assert rerun.main(["demo"], cfg=cfg) == 3
    assert rerun.main(["demo"], cfg=cfg) == 2


def test_a_job_that_exits_75_is_not_mistaken_for_a_busy_lock(tmp_path):
    cfg = cfg_with(tmp_path, "exit 75", lock=str(tmp_path / "job.lock"))
    assert rerun.main(["demo"], cfg=cfg) == 75
    assert rerun.main(["demo"], cfg=cfg) == 2  # it ran, so the window holds


def test_the_lock_is_held_while_the_job_runs(tmp_path):
    lock = tmp_path / "job.lock"
    probe = tmp_path / "probe.txt"
    cmd = f"/usr/bin/flock -n {lock} true; echo $? > {probe}"
    assert rerun.main(["demo"], cfg=cfg_with(tmp_path, cmd, lock=str(lock))) == 0
    assert probe.read_text().strip() == "1"  # a cron copy started meanwhile would find it busy


def test_every_failure_before_the_start_gives_the_rerun_back(tmp_path):
    base = cfg_with(tmp_path, "true", lock=str(tmp_path / "job.lock"))
    job = base.rerun["demo"]
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    for bad in (dataclasses.replace(job, lock=str(tmp_path / "no-such-dir" / "job.lock")),  # lock open fails
                dataclasses.replace(job, log=str(blocker / "job.log")),                     # log folder fails
                dataclasses.replace(job, cwd=str(tmp_path / "missing"))):                   # spawn fails
        cfg = dataclasses.replace(base, rerun={"demo": bad})
        assert rerun.main(["demo"], cfg=cfg) == 1, bad
    assert rerun.main(["demo"], cfg=base) == 0


def test_refused_in_diagnose_mode(tmp_path, capsys):
    cfg = dataclasses.replace(cfg_with(tmp_path, "true"), mode="diagnose")
    assert rerun.main(["demo"], cfg=cfg) == 2
    assert "reruns need mode = act" in capsys.readouterr().err


def test_the_job_inherits_the_cron_lock(tmp_path, monkeypatch):
    import subprocess as _sp
    seen = {}
    real = _sp.Popen

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)
    monkeypatch.setattr(rerun.subprocess, "Popen", spy)
    assert rerun.main(["demo"], cfg=cfg_with(tmp_path, "true", lock=str(tmp_path / "job.lock"))) == 0
    assert seen["pass_fds"] and len(seen["pass_fds"]) == 1


def test_the_lock_outlives_the_helper(tmp_path):
    """The job keeps the lock after the helper is gone: a cron copy would find it busy."""
    import fcntl, subprocess as _sp, sys as _sys, time as _t
    lock = tmp_path / "job.lock"
    marker = tmp_path / "started"
    code = (
        "import dataclasses, sys\n"
        "from alertwatch import rerun\n"
        "from alertwatch.config import RerunJob, load_config\n"
        f"cfg = load_config(env={{'ALERT_WATCH_STATE': {str(tmp_path / 'state')!r}}})\n"
        f"job = RerunJob(cmd='touch {marker}; sleep 5', cwd={str(tmp_path)!r}, lock={str(lock)!r}, "
        f"log={str(tmp_path / 'job.log')!r}, timeout_s=30)\n"
        "cfg = dataclasses.replace(cfg, mode='act', rerun={'demo': job})\n"
        "sys.exit(rerun.main(['demo'], cfg=cfg))\n")
    helper = _sp.Popen([_sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parents[1]))
    for _ in range(100):
        if marker.exists():
            break
        _t.sleep(0.05)
    helper.kill()          # the helper dies; the job (its own session) runs on
    helper.wait()
    with open(lock, "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            got = True
        except BlockingIOError:
            got = False
    assert got is False


def test_precheck_error_gives_the_reservation_and_lock_back(tmp_path):
    import fcntl
    import pytest
    lock = tmp_path / "job.lock"
    cfg = cfg_with(tmp_path, "true", lock=str(lock))

    def boom():
        raise ValueError("bad log")
    with pytest.raises(ValueError):
        rerun.rerun(cfg, "demo", precheck=boom)
    with open(lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)        # free again
    assert rerun.main(["demo"], cfg=cfg) == 0                  # the window was not used up
