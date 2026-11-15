import dataclasses
import json
import time

from alertwatch import rerun
from alertwatch.config import RerunJob, load_config


def cfg_with(tmp_path, cmd, lock=""):
    cfg = load_config(env={"ALERT_WATCH_STATE": str(tmp_path / "state")})
    job = RerunJob(cmd=cmd, cwd=str(tmp_path), lock=lock, log=str(tmp_path / "job.log"), timeout_s=30)
    return dataclasses.replace(cfg, rerun={"demo": job})


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
