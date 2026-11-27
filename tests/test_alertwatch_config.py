from pathlib import Path

import pytest

from alertwatch.config import RerunJob, load_config


def test_load_config_defaults_from_repo_file():
    cfg = load_config(env={})
    assert cfg.mode == "act"  # switched 2026-11-27 on Rex's go
    assert cfg.chat_id == "7735693897"
    assert cfg.max_runs_day == 30 and cfg.max_runs_hour == 6 and cfg.max_merges_day == 3
    assert cfg.run_timeout_s == 3600 and cfg.kill_after_s == 30
    assert cfg.fail_streak_pause == 3 and cfg.limit_backoff_s == 3600
    assert cfg.rerun_window_s == 6 * 3600
    assert cfg.repos == ("splash_poller", "ai-harness")
    assert "web/" in cfg.denied_paths and "watchdog/" in cfg.denied_paths
    assert cfg.state_dir == Path.home() / ".local" / "state" / "alert-watch"
    assert "supervise_meets" not in cfg.rerun
    assert all("supervise_meets.py" not in job.cmd for job in cfg.rerun.values())
    assert isinstance(cfg.rerun["coverage_check"], RerunJob)


def test_state_dir_env_override(tmp_path):
    cfg = load_config(env={"ALERT_WATCH_STATE": str(tmp_path)})
    assert cfg.state_dir == tmp_path


def test_tool_paths_env_override(tmp_path):
    cfg = load_config(env={"ALERT_WATCH_TG_SEND": "/x/tg", "ALERT_WATCH_CLAUDE": "/x/claude"})
    assert cfg.tg_send == "/x/tg" and cfg.claude_bin == "/x/claude"


def test_bad_mode_is_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(Path("alertwatch/config.toml").read_text().replace('mode = "act"', 'mode = "yolo"'))
    with pytest.raises(ValueError):
        load_config(p, env={})


def test_rerun_jobs_have_command_cwd_and_log():
    cfg = load_config(env={})
    for name, job in cfg.rerun.items():
        assert job.cmd and job.cwd and job.log and job.timeout_s > 0, name
