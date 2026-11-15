"""alert-watch dispatcher: turn new inbox entries into one bounded Claude run.

Cron runs it every 2 minutes (`python3 -m alertwatch.dispatch`). It holds
<state>/dispatch.lock for the whole run, so a second copy exits at once.
Design: docs/superpowers/specs/2026-11-15-alert-watch-design.md
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from alertwatch import inbox, ledger, notes
from alertwatch.config import Config, load_config
from alertwatch.result import parse_result, started, usage_limit
from alertwatch.rules import classify, load_rules
from backlogrun.cli import DENY_RULES

HERE = Path(__file__).resolve().parent
REPORT_MAX = 4000
CHECK_SOURCE = "alert-watch-check"

_PUSH_RULES = {r for r in DENY_RULES if "push" in r}
_ALWAYS_DENY = [
    "Bash(git push --force*)", "Bash(git push -f*)", "Bash(git push * --force*)",
    "Bash(git push * -f*)", "Bash(git * push --force*)", "Bash(git * push -f*)",
    "Bash(git * push * --force*)", "Bash(git * push * -f*)",
    "Bash(git push * --delete*)", "Bash(git * push * --delete*)",
    "Bash(git push * :*)", "Bash(git * push * :*)",
    "Bash(*supervise_meets.py*)",
    "Read(**/.env)", "Read(**/.env.*)", "Bash(*/.env*)", "Bash(* .env*)",
]
_DIAGNOSE_DENY = [
    "Edit", "Write", "NotebookEdit",
    "Bash(git commit*)", "Bash(git * commit*)", "Bash(git merge*)", "Bash(git * merge*)",
    "Bash(git revert*)", "Bash(git * revert*)", "Bash(git rebase*)", "Bash(git * rebase*)",
    "Bash(kill*)", "Bash(pkill*)", "Bash(*alertwatch.rerun*)",
]


def _iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(now: float, msg: str) -> None:
    print(f"[{_iso(now)}] {msg}", flush=True)


def session_settings(cfg: Config) -> dict:
    """Deny rules for the run. backlog-run verified on 2026-09-03 that `--settings`
    deny rules hold under --dangerously-skip-permissions. In "act" mode a plain push
    is allowed (the run merges and pushes main); force and delete pushes never are."""
    if cfg.mode == "act":
        base = [r for r in DENY_RULES if r not in _PUSH_RULES]
        extra = []
    else:
        base = list(DENY_RULES)
        extra = _DIAGNOSE_DENY
    deny = []
    for rule in base + _ALWAYS_DENY + extra:
        if rule not in deny:
            deny.append(rule)
    return {"permissions": {"deny": deny}}


def run_env(cfg: Config) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    local_bin = str(Path.home() / ".local" / "bin")
    parts = [local_bin] + [p for p in env.get("PATH", "/usr/bin:/bin").split(":") if p and p != local_bin]
    env.update({"PATH": ":".join(parts), "ALERT_WATCH_RUN": "1", "ALERT_SOURCE": "alert-watch",
                "MEETTRACK_DB_READ_ONLY": "1"})
    return env


def _first_line(text: str, limit: int = 120) -> str:
    line = next((l.strip() for l in str(text).splitlines() if l.strip()), "")
    return line[:limit]


def _recent_lines(recent: list[dict]) -> str:
    if not recent:
        return "(none)"
    out = []
    for r in recent:
        result = r.get("result") if isinstance(r.get("result"), dict) else {}
        firsts = "; ".join(e.get("first_line", "") for e in r.get("entries") or [])[:300]
        verdicts = ", ".join(f"{e.get('id')}={e.get('verdict')}" for e in result.get("entries") or []
                             if isinstance(e, dict))
        merges = ", ".join(f"{m.get('repo')} {str(m.get('from_sha', ''))[:10]}..{str(m.get('to_sha', ''))[:10]}"
                           f"{' (reverted)' if m.get('reverted') else ''}"
                           for m in result.get("merges") or [] if isinstance(m, dict))
        needs = " | ".join(str(n) for n in result.get("needs_rex") or [])
        out.append(f"- {r.get('run_id')} {_iso(float(r['started_at']))} status={r.get('status')}"
                   f" reports: {firsts or '-'}; verdicts: {verdicts or '-'}; merges: {merges or '-'};"
                   f" needs_rex: {needs or '-'}")
    return "\n".join(out)


def build_prompt(cfg: Config, entries: list[dict], recent: list[dict], counts: dict, now: float) -> str:
    template = (HERE / "prompt.md").read_text(encoding="utf-8")
    reports = "\n\n".join(
        f"<<<REPORT id={e['id']} source={str(e.get('source') or '')[:300]} "
        f"captured_at={e.get('captured_at', '')} attempts={e.get('attempts', 0)}>>>\n"
        f"{str(e['text'])[:REPORT_MAX]}\n<<<END REPORT>>>"
        for e in entries)
    fills = {
        "{{POLICY}}": (HERE / "POLICY.md").read_text(encoding="utf-8"),
        "{{NOW}}": _iso(now),
        "{{MODE}}": cfg.mode,
        "{{REPOS}}": ", ".join(cfg.repos),
        "{{DENIED}}": ", ".join(cfg.denied_paths),
        "{{RERUN_JOBS}}": ", ".join(sorted(cfg.rerun)),
        "{{MERGES_DAY}}": str(counts.get("merges_day", 0)),
        "{{MAX_MERGES_DAY}}": str(cfg.max_merges_day),
        "{{RECENT}}": _recent_lines(recent),
    }
    for key, value in fills.items():
        template = template.replace(key, value)
    return template.replace("{{ENTRIES}}", reports)  # last: report text is never expanded


def _claude_path(cfg: Config, env: dict) -> str:
    return shutil.which(cfg.claude_bin, path=env.get("PATH")) or cfg.claude_bin


def run_claude(cfg: Config, prompt: str, env: dict) -> dict:
    state = cfg.state_dir
    settings, mcp = state / "session-settings.json", state / "mcp-empty.json"
    settings.write_text(json.dumps(session_settings(cfg), indent=1), encoding="utf-8")
    mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    argv = [_claude_path(cfg, env), "-p", "-", "--output-format", "json",
            "--dangerously-skip-permissions", "--settings", str(settings),
            "--strict-mcp-config", "--mcp-config", str(mcp), "--model", cfg.model]
    proc = subprocess.Popen(argv, cwd=str(cfg.projects_dir), env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    timed_out = False
    try:
        out, err = proc.communicate(prompt, timeout=cfg.run_timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            out, err = proc.communicate(timeout=cfg.kill_after_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, err = proc.communicate()
    data = None
    for candidate in (out or "", *reversed((out or "").splitlines())):
        candidate = candidate.strip()
        if candidate.startswith("{"):
            try:
                parsed = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                data = parsed
                break
    return {"rc": proc.returncode, "timed_out": timed_out, "stdout": out or "",
            "stderr": err or "", "data": data}


def _load_state(state: Path) -> dict:
    try:
        return json.loads((state / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: Path, st: dict) -> None:
    path = state / "state.json"
    tmp = path.with_name(".tmp-state.json")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1)
    os.replace(tmp, path)


def _parse_due(value) -> float | None:
    try:
        when = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return when.timestamp()


def enqueue_due_checks(cfg: Config, st: dict, now: float) -> int:
    done = st.setdefault("checks_enqueued", [])
    added = 0
    for rec in ledger.recent_runs(cfg.state_dir, now, 48):
        result = rec.get("result") if isinstance(rec.get("result"), dict) else {}
        for i, chk in enumerate(result.get("pending_checks") or []):
            key = f"{rec.get('run_id')}:{i}"
            if key in done or not isinstance(chk, dict):
                continue
            due = _parse_due(chk.get("due_at"))
            if due is None:
                _log(now, f"pending check {key}: unreadable due_at {chk.get('due_at')!r}; skipped")
                done.append(key)
                continue
            if due > now:
                continue
            text = (f"Check after alert-watch merge in {chk.get('repo')} "
                    f"{chk.get('from_sha', '?')}..{chk.get('to_sha', '?')} (run {rec.get('run_id')}): "
                    f"{chk.get('what', '')}")
            inbox.add_entry(cfg.state_dir, text, CHECK_SOURCE)
            done.append(key)
            added += 1
    st["checks_enqueued"] = done[-500:]
    return added


def _cap_reached(cfg: Config, st: dict, now: float) -> bool:
    c = ledger.counts(cfg.state_dir, now)
    for cap, limit, window in (("runs_hour", cfg.max_runs_hour, 3600),
                               ("runs_day", cfg.max_runs_day, 86400)):
        if c[cap] >= limit:
            noted = st.setdefault("cap_noted", {})
            if now - float(noted.get(cap, 0)) >= window:
                notes.send(cfg, notes.cap_note(cap, c[cap], limit))
                noted[cap] = now
            _log(now, f"cap {cap} reached ({c[cap]} of {limit}); waiting")
            return True
    return False


def _summary(entry: dict) -> dict:
    return {"id": entry["id"], "source": str(entry.get("source") or "")[:300],
            "first_line": _first_line(entry.get("text", "")), "rule": entry.get("_rule"),
            "action": entry.get("_action")}


def _pause(cfg: Config, reason: str, now: float) -> None:
    (cfg.state_dir / "PAUSED").write_text(f"{_iso(now)} {reason}\n", encoding="utf-8")
    notes.send(cfg, notes.pause_note(reason))
    _log(now, f"paused: {reason}")


def main(argv: list[str] | None = None, *, now: Callable[[], float] = time.time,
         cfg: Config | None = None) -> int:
    ap = argparse.ArgumentParser(prog="alertwatch.dispatch")
    ap.add_argument("--dry-run", action="store_true", help="show what would run; claim nothing")
    args = ap.parse_args(argv)
    cfg = cfg or load_config()
    state = cfg.state_dir
    inbox.ensure_dirs(state)
    lock = open(state / "dispatch.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    try:
        return _dispatch(cfg, args.dry_run, now)
    finally:
        lock.close()


def _dispatch(cfg: Config, dry_run: bool, now: Callable[[], float]) -> int:
    state = cfg.state_dir
    t = now()
    if (state / "PAUSED").exists():
        return 0
    st = _load_state(state)
    if float(st.get("limit_until") or 0) > t:
        return 0
    rules = load_rules()
    if dry_run:
        for p in sorted((state / "inbox").glob("*.json")):
            if p.name.startswith("."):
                continue
            try:
                entry = json.loads(p.read_text(encoding="utf-8"))
                print(p.name, *classify(entry, rules), _first_line(entry.get("text", "")))
            except (OSError, ValueError):
                print(p.name, "unreadable")
        return 0

    for dead in inbox.recover_stale(state):
        _log(t, f"batch {dead} was left by a dispatcher that stopped; moved to failed/")
        try:
            ledger.update_run(state, dead, status="failed", reason="the dispatcher stopped during this run")
        except (OSError, ValueError):
            pass
        notes.send(cfg, notes.failure_note(
            {"run_id": dead, "reason": "the dispatcher stopped during this run"}, []))

    enqueue_due_checks(cfg, st, t)
    if _cap_reached(cfg, st, t):
        _save_state(state, st)
        return 0

    run_id = ledger.new_run_id(t)
    entries, broken = inbox.claim_all(state, run_id)
    for name, reason in broken:
        _log(t, f"{run_id}: broken inbox file {name}: {reason}")
    if not entries and not broken:
        _save_state(state, st)
        return 0
    looks, quiet = [], []
    for entry in entries:
        entry["_action"], entry["_rule"] = classify(entry, rules)
        (looks if entry["_action"] == "look" else quiet).append(entry)
    record = {"run_id": run_id, "started_at": t, "ended_at": None, "status": "running",
              "reason": "", "mode": cfg.mode, "entries": [_summary(e) for e in entries],
              "broken": broken, "session_id": "", "rc": None, "timed_out": False,
              "result": None, "note_status": None}
    inbox.file_away(state, run_id, quiet, "done")
    if not looks:
        record.update(status="skipped", reason="nothing to look at", ended_at=t)
        ledger.write_run(state, record)
        inbox.finish(state, run_id, "done")
        _log(t, f"{run_id}: skipped ({len(entries)} entries, none to look at)")
        _save_state(state, st)
        return 0

    ledger.write_run(state, record)
    counts = ledger.counts(state, t)
    recent = [r for r in ledger.recent_runs(state, t, 24) if r.get("run_id") != run_id]
    prompt = build_prompt(cfg, looks, recent, counts, t)
    _log(t, f"{run_id}: starting a {cfg.mode} run for {len(looks)} report(s)")
    run = run_claude(cfg, prompt, run_env(cfg))
    data = run["data"]
    ended = now()
    record.update(ended_at=ended, rc=run["rc"], timed_out=run["timed_out"],
                  session_id=str((data or {}).get("session_id") or ""))
    parsed = parse_result(str((data or {}).get("result") or "")) if data else None
    hit, until = (False, None) if parsed else usage_limit(data, run["stdout"], run["stderr"])

    if parsed:
        record.update(status="ok", result=parsed)
        text = notes.run_note(record)
        record["note_status"] = notes.send(cfg, text) if text else "none"
        inbox.finish(state, run_id, "done")
    elif hit:
        until = until or ended + cfg.limit_backoff_s
        record.update(status="limit", reason="Claude usage limit")
        st["limit_until"] = until
        if started(data):
            inbox.finish(state, run_id, "failed")  # it may have acted: never replayed
            record["reason"] = "Claude usage limit, part way through the run"
        else:
            inbox.release(state, run_id, looks, count_attempt=False)
        if float(st.get("limit_noted_until") or 0) != until:
            record["note_status"] = notes.send(cfg, notes.limit_note(until))
            st["limit_noted_until"] = until
    else:
        if run["timed_out"]:
            reason = f"timed out after {cfg.run_timeout_s} s"
        elif data is None:
            reason = f"exited with rc={run['rc']} and no JSON output"
        else:
            reason = f"no readable result (rc={run['rc']})"
        record.update(status="failed", reason=reason,
                      stderr_tail=run["stderr"][-4000:], stdout_tail=run["stdout"][-4000:])
        if not started(data) and not run["timed_out"]:
            failed_ids = inbox.release(state, run_id, looks, count_attempt=True)
        else:
            inbox.finish(state, run_id, "failed")
            failed_ids = [e["id"] for e in looks]
        record["note_status"] = notes.send(cfg, notes.failure_note(record, failed_ids))

    ledger.update_run(state, run_id, **{k: v for k, v in record.items() if k != "run_id"})
    _log(ended, f"{run_id}: {record['status']} {record['reason']}".rstrip())
    if record["status"] == "failed" and ledger.fail_streak(state) >= cfg.fail_streak_pause:
        _pause(cfg, f"{cfg.fail_streak_pause} failed runs in a row", ended)
    inbox.prune(state, ended)
    _save_state(state, st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
