"""Usage ledger for every Jev call: counts and cost, never content.

One JSON line per call in ~/.local/state/jev/usage.jsonl (override: JEV_USAGE_LOG), so
"what is Jev costing, and which process is calling it" has one answer across every repo.
Writing the ledger can never break a call.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DEFAULT_LOG = Path.home() / ".local" / "state" / "jev" / "usage.jsonl"


def log_path() -> Path:
    return Path(os.environ.get("JEV_USAGE_LOG") or DEFAULT_LOG)


def record(project: str, task: str, model: str, *, ok: bool, input_tokens: int = 0,
           cost_usd: float = 0.0, seconds: float = 0.0) -> None:
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": int(time.time()), "project": project, "task": task, "model": model, "ok": ok,
               "input_tokens": input_tokens, "cost_usd": round(cost_usd, 8), "seconds": round(seconds, 3)}
        # O_APPEND and ONE write() per row: small appends are atomic, so two processes writing
        # at once cannot interleave their lines.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(row) + "\n").encode())
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001
        pass


def summarize(path=None, *, since_epoch: int = 0) -> dict:
    """{(project, task): {calls, errors, input_tokens, cost_usd}}. Bad lines are skipped."""
    out: dict = {}
    try:
        lines = Path(path or log_path()).read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
            if row["ts"] < since_epoch:
                continue
            slot = out.setdefault((row["project"], row["task"]),
                                  {"calls": 0, "errors": 0, "input_tokens": 0, "cost_usd": 0.0})
            slot["calls"] += 1
            slot["errors"] += 0 if row.get("ok") else 1
            slot["input_tokens"] += int(row.get("input_tokens") or 0)
            slot["cost_usd"] += float(row.get("cost_usd") or 0.0)
        except (ValueError, KeyError, TypeError):
            continue
    return out
