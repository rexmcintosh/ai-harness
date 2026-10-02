from __future__ import annotations
import json
import os
from pathlib import Path

_ALPHA = 0.3
_FALLBACK = (1.0, 300.0)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


class Estimates:
    def __init__(self, path: Path, seeds: dict):
        self.path = Path(path)
        self.seeds = seeds
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def estimate(self, type_: str) -> tuple[float, float]:
        if type_ in self.data:
            d = self.data[type_]
            return (d["cost"], d["duration_s"])
        if type_ in self.seeds:
            s = self.seeds[type_]
            return (float(s["cost"]), float(s["duration_s"]))
        return _FALLBACK

    def record(self, type_: str, *, cost: float, duration_s: float) -> None:
        prev_cost, prev_dur = self.estimate(type_)
        self.data[type_] = {
            "cost": prev_cost + _ALPHA * (cost - prev_cost),
            "duration_s": prev_dur + _ALPHA * (duration_s - prev_dur),
        }
        _atomic_write(self.path, json.dumps(self.data, indent=1))


class Reviewed:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def get(self, repo: str) -> str | None:
        return self.data.get(repo)

    def set(self, repo: str, sha: str) -> None:
        self.data[repo] = sha
        _atomic_write(self.path, json.dumps(self.data, indent=1))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True
    return True


class Lock:
    def __init__(self, path: Path):
        self.path = Path(path)

    def acquire(self) -> bool:
        if self.path.exists():
            try:
                pid = int(self.path.read_text().strip())
            except (OSError, ValueError):
                pid = -1
            if pid > 0 and _pid_alive(pid):
                return False
            self.path.unlink(missing_ok=True)  # stale
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        return True

    def release(self) -> None:
        self.path.unlink(missing_ok=True)


def _pause_path(state_dir: Path) -> Path:
    return Path(state_dir) / "pause"


def pause_until(state_dir: Path) -> str | None:
    try:
        return _pause_path(state_dir).read_text().strip() or None
    except OSError:
        return None


def set_pause(state_dir: Path, until_iso: str) -> None:
    _atomic_write(_pause_path(state_dir), until_iso)


def clear_pause(state_dir: Path) -> None:
    _pause_path(state_dir).unlink(missing_ok=True)
