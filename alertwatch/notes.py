"""The Telegram notes alert-watch sends to Rex. Plain short sentences, no em dashes.

Every note goes through bin/tg-send with capture off (ALERT_INBOX_DIR=off) and
ALERT_SOURCE=alert-watch, so the watcher never reacts to its own notes.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess

QUIET_VERDICTS = ("not_a_failure", "same_as_run")


def _footer(session_id: str | None) -> str:
    if not session_id:
        return "Open the run: no session was saved."
    return f"Open the run: claude --resume {session_id}  (in ~/projects)"


def run_note(record: dict) -> str | None:
    """None when the run found nothing real: every verdict quiet, no action, nothing for Rex."""
    result = record.get("result") or {}
    verdicts = [e.get("verdict") for e in result.get("entries") or [] if isinstance(e, dict)]
    needs = [str(n) for n in result.get("needs_rex") or []]
    if (all(v in QUIET_VERDICTS for v in verdicts) and not result.get("actions") and not needs):
        return None
    lines = [str(result.get("note") or "").strip()]
    lines += [f"Needs you: {n}" for n in needs]
    lines.append(_footer(record.get("session_id")))
    return "\n".join(l for l in lines if l)


def failure_note(record: dict, failed_ids: list[str]) -> str:
    lines = [f"alert-watch: run {record.get('run_id')} failed: {record.get('reason') or 'no reason'}."]
    if failed_ids:
        lines.append(f"These reports will not be tried again: {', '.join(failed_ids)}.")
    lines.append("Nothing was retried that may have acted already.")
    lines.append(_footer(record.get("session_id")))
    return "\n".join(lines)


def cap_note(cap: str, value: int, limit: int) -> str:
    what = {"runs_hour": "runs this hour", "runs_day": "runs in 24 hours",
            "merges_day": "merges in 24 hours"}.get(cap, cap)
    return (f"alert-watch: limit reached, {value} {what} (limit {limit}). "
            "New reports wait in the inbox until the limit resets.")


def limit_note(until_epoch: float, *, part_way: bool = False) -> str:
    when = dt.datetime.fromtimestamp(until_epoch, dt.timezone.utc).strftime("%H:%M UTC")
    if part_way:
        return (f"alert-watch: the Claude usage limit stopped a run part way. It may have acted, "
                f"so its reports will not be tried again; check the run. "
                f"New runs start again after {when}.")
    return (f"alert-watch: the Claude usage limit stopped a run. "
            f"Reports wait in the inbox. It starts again after {when}.")


def pause_note(reason: str) -> str:
    return (f"alert-watch paused itself: {reason}. "
            "Remove ~/.local/state/alert-watch/PAUSED to start it again.")


def send(cfg, text: str) -> str:
    """accepted | failed | uncertain, from tg-send's exit code (0, 1, anything else)."""
    # Not captured at all (the run record keeps the text); the tag is a second guard.
    env = {**os.environ, "ALERT_SOURCE": "alert-watch", "ALERT_INBOX_DIR": "off",
           "TG_SEND_RECEIPT_OUTPUT": "1"}
    try:
        proc = subprocess.run([cfg.tg_send, cfg.chat_id, "-"], input=text, text=True,
                              capture_output=True, env=env, timeout=60)
    except subprocess.TimeoutExpired:
        return "uncertain"
    except OSError:
        return "failed"
    return {0: "accepted", 1: "failed"}.get(proc.returncode, "uncertain")
