"""Config + key loading. Cron has no shell env, so the Venice key is read
straight from ~/.env (accepts VENICE_API_KEY or VENICE_KEY)."""
from __future__ import annotations
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".config" / "diem" / "config.toml"

# The deadline is the hard cutoff shortly before the reset (epoch). It may sit on
# the previous evening when reset is at/after midnight (23:50 before a 00:00 UTC
# reset), so its lead over reset — not a raw HH:MM ordering — is what we validate.
_DEADLINE_MAX_LEAD_S = 6 * 3600


def _config_die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _parse_hhmm(value: str, label: str) -> tuple[int, int]:
    """Two-part int split of an HH:MM string; dies with SystemExit(2) on
    anything else (wrong shape, non-int parts, or out-of-range 0-23/0-59)."""
    parts = str(value).split(":")
    if len(parts) != 2:
        _config_die(f"{label} must be HH:MM")
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        _config_die(f"{label} must be HH:MM")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        _config_die(f"{label} must be a valid time (00-23:00-59)")
    return h, m


@dataclass(frozen=True)
class Checkpoint:
    time: str   # "HH:MM" local
    floor: float  # fraction of daily_diem


_DEFAULT_CHECKPOINTS = [Checkpoint("21:00", 0.40), Checkpoint("23:00", 0.15),
                        Checkpoint("00:15", 0.0)]
_DEFAULT_SEEDS = {
    "ask": {"cost": 0.5, "duration_s": 120},
    "review": {"cost": 1.0, "duration_s": 180},
    "images": {"cost": 2.0, "duration_s": 180},
    "backfill": {"cost": 1.0, "duration_s": 300},
    "cmd": {"cost": 1.0, "duration_s": 300},
}


@dataclass
class DiemConfig:
    daily_diem: float
    repos: list[Path]
    checkpoints: list[Checkpoint] = field(default_factory=lambda: list(_DEFAULT_CHECKPOINTS))
    deadline: str = "00:50"
    reset: str = "01:00"
    state_dir: Path = Path.home() / ".local/state/diem"
    outputs_dir: Path = Path.home() / ".local/state/diem/outputs"
    loom_repo: Path = Path.home() / "projects/ai-harness"
    loom_cmd: list[str] = field(default_factory=lambda: [
        str(Path.home() / "projects/ai-harness/.venv/bin/python"),
        "-m", "loom.cli", "backfill"])
    seeds: dict = field(default_factory=lambda: dict(_DEFAULT_SEEDS))
    telegram: dict | None = None
    cmd_whitelist: dict = field(default_factory=dict)
    backfill_max_per_night: int = 4
    backfill_chunk: int = 2

    @classmethod
    def load(cls, path: Path | None = None) -> "DiemConfig":
        path = path or DEFAULT_CONFIG
        try:
            raw = tomllib.loads(Path(path).read_text())
        except FileNotFoundError:
            print(f"error: no config at {path}", file=sys.stderr)
            raise SystemExit(2)
        if "daily_diem" not in raw:
            print("error: config needs daily_diem (your daily DIEM allowance)",
                  file=sys.stderr)
            raise SystemExit(2)
        daily_diem = float(raw["daily_diem"])
        if daily_diem <= 0:
            _config_die("daily_diem must be > 0 (drain floor math and evening "
                        "ping percent depend on it)")
        kw = {"daily_diem": daily_diem,
              "repos": [Path(r).expanduser() for r in raw.get("repos", [])]}
        if "checkpoints" in raw:
            kw["checkpoints"] = [Checkpoint(c["time"], float(c["floor"]))
                                 for c in raw["checkpoints"]]
        for key in ("deadline", "reset", "backfill_max_per_night", "backfill_chunk"):
            if key in raw:
                kw[key] = raw[key]
        for key in ("state_dir", "outputs_dir", "loom_repo"):
            if key in raw:
                kw[key] = Path(raw[key]).expanduser()
        if "loom_cmd" in raw:
            kw["loom_cmd"] = list(raw["loom_cmd"])
        seeds = {k: dict(v) for k, v in _DEFAULT_SEEDS.items()}
        for k, v in raw.get("seeds", {}).items():
            seeds[k] = {**seeds.get(k, {}), **v}
        kw["seeds"] = seeds
        kw["telegram"] = raw.get("telegram")
        kw["cmd_whitelist"] = raw.get("cmd_whitelist", {})

        checkpoints = kw.get("checkpoints", _DEFAULT_CHECKPOINTS)
        if not checkpoints:
            _config_die("checkpoints must not be empty")
        for cp in checkpoints:
            _parse_hhmm(cp.time, f"checkpoint time {cp.time!r}")
        deadline = kw.get("deadline", "00:50")
        reset = kw.get("reset", "01:00")
        dh, dm = _parse_hhmm(deadline, f"deadline {deadline!r}")
        rh, rm = _parse_hhmm(reset, f"reset {reset!r}")
        # Anchor both to a reference day (mirroring drain.next_reset/next_deadline)
        # and require the deadline to land shortly BEFORE the reset. This accepts
        # 23:50-before-00:00 (10-min lead across midnight) while still rejecting a
        # deadline that falls after the reset (e.g. 01:30 vs 01:00 → ~23.5h lead).
        ref = datetime(2000, 1, 1, 12, 0)
        r = ref.replace(hour=rh, minute=rm, second=0, microsecond=0)
        if ref > r:
            r += timedelta(days=1)
        base = r if (dh, dm) < (rh, rm) else r - timedelta(days=1)
        d = base.replace(hour=dh, minute=dm, second=0, microsecond=0)
        lead = (r - d).total_seconds()
        if not (0 < lead <= _DEADLINE_MAX_LEAD_S):
            _config_die(f"deadline ({deadline}) must fall shortly before reset "
                        f"({reset}) — the drain's day-anchoring contract")

        return cls(**kw)


def _read_env(env_path: Path) -> dict[str, str]:
    try:
        lines = Path(env_path).read_text().splitlines()
    except OSError:
        return {}
    found = {}
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line or line.startswith("#"):
            continue
        name, _, val = line.partition("=")
        found[name.strip()] = val.strip().strip("'\"")
    return found


def load_venice_key(env_path: Path = Path.home() / ".env") -> str:
    found = _read_env(env_path)
    for name in ("VENICE_API_KEY", "VENICE_KEY"):
        if found.get(name):
            return found[name]
    print(f"error: neither VENICE_API_KEY nor VENICE_KEY found in {env_path}",
          file=sys.stderr)
    raise SystemExit(2)


def load_venice_admin_key(env_path: Path = Path.home() / ".env") -> str:
    found = _read_env(env_path)
    if found.get("VENICE_ADMIN_KEY"):
        return found["VENICE_ADMIN_KEY"]
    print(f"error: VENICE_ADMIN_KEY not found in {env_path}", file=sys.stderr)
    raise SystemExit(2)
