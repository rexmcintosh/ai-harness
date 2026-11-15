"""alert-watch settings from alertwatch/config.toml, with a few env overrides."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_PATH = Path(__file__).with_name("config.toml")
MODES = ("diagnose", "act")


@dataclass(frozen=True)
class RerunJob:
    cmd: str
    cwd: str
    lock: str
    log: str
    timeout_s: int


@dataclass(frozen=True)
class Config:
    state_dir: Path
    chat_id: str
    mode: str
    model: str
    run_timeout_s: int
    kill_after_s: int
    max_runs_day: int
    max_runs_hour: int
    max_merges_day: int
    fail_streak_pause: int
    limit_backoff_s: int
    rerun_window_s: int
    projects_dir: Path
    repos: tuple[str, ...]
    denied_paths: tuple[str, ...]
    rerun: dict[str, RerunJob]
    tg_send: str
    claude_bin: str
    worktrees_dir: Path
    meettrack_run_dir: Path
    restart_max_attempts: int
    watch_chats: tuple[str, ...]


def load_config(path: Path | None = None, env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    raw = tomllib.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    mode = str(raw["mode"])
    if mode not in MODES:
        raise ValueError(f"alert-watch: mode must be one of {MODES}, not {mode!r}")
    state = env.get("ALERT_WATCH_STATE") or str(Path.home() / ".local" / "state" / "alert-watch")
    rerun = {name: RerunJob(cmd=str(j["cmd"]), cwd=str(j["cwd"]), lock=str(j.get("lock") or ""),
                            log=str(j["log"]), timeout_s=int(j.get("timeout_s", 900)))
             for name, j in (raw.get("rerun") or {}).items()}
    return Config(
        state_dir=Path(state),
        chat_id=str(raw["chat_id"]),
        mode=mode,
        model=str(raw["model"]),
        run_timeout_s=int(raw["run_timeout_s"]),
        kill_after_s=int(raw["kill_after_s"]),
        max_runs_day=int(raw["max_runs_day"]),
        max_runs_hour=int(raw["max_runs_hour"]),
        max_merges_day=int(raw["max_merges_day"]),
        fail_streak_pause=int(raw["fail_streak_pause"]),
        limit_backoff_s=int(raw["limit_backoff_s"]),
        rerun_window_s=int(raw["rerun_window_s"]),
        projects_dir=Path(str(raw["projects_dir"])),
        repos=tuple(str(r) for r in raw["repos"]),
        denied_paths=tuple(str(p) for p in raw["denied_paths"]),
        rerun=rerun,
        tg_send=env.get("ALERT_WATCH_TG_SEND") or str(raw["tg_send"]),
        claude_bin=env.get("ALERT_WATCH_CLAUDE") or "claude",
        worktrees_dir=Path(state) / "worktrees",
        meettrack_run_dir=Path(str(raw["meettrack_run_dir"])),
        restart_max_attempts=int(raw["restart_max_attempts"]),
        watch_chats=tuple(str(c) for c in raw.get("watch_chats") or [raw["chat_id"]]),
    )
