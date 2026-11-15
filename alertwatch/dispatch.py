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
import re
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
CHECK_MAX_DELAY_S = 24 * 3600
CHECK_LOOKBACK_H = 30 * 24
PIPE_DRAIN_S = 10
VERDICTS = ("fixed", "live_step", "needs_rex", "not_a_failure", "same_as_run")

# Always denied, in both modes. backlog-run's DENY_RULES (pushes, remotes, gh,
# deploy CLIs, curl, wget, ssh, crontab, systemctl, sudo, docker, tg-send, mail,
# pipx) come first. A run never pushes, merges, reverts or kills by itself: the
# checked helpers (alertwatch.merge, alertwatch.restart, alertwatch.rerun) do.
_ALWAYS_DENY = [
    "Bash(git push*)", "Bash(git -C * push*)",
    "Bash(*supervise_meets.py*)",
    "Bash(kill*)", "Bash(pkill*)", "Bash(killall*)",
    "Bash(git merge*)", "Bash(git * merge*)", "Bash(git revert*)", "Bash(git * revert*)",
    "Bash(git reset --hard*)", "Bash(git * reset --hard*)",
    "Read(**/.env)", "Read(**/.env.*)", "Bash(*/.env*)", "Bash(* .env*)",
]
# act mode: the run edits only in its worktrees under the state dir, never in a
# main checkout (cron runs from those).
# Neither mode may touch alert-watch's own records (the merge cap, run records,
# its settings and switches) except through the helpers.
_OWN_RECORDS = ["actions.jsonl", "state.json", "reruns.json", "session-settings.json", "mcp-empty.json",
                "PAUSED", "ENABLED", "runs/"]


def _rule_path(path: Path) -> str:
    """A path in Claude Code permission-rule form: ~/ for home, // for absolute
    (a bare /path would be read relative to the settings file)."""
    path = Path(path).expanduser().resolve()
    try:
        return "~/" + str(path.relative_to(Path.home().resolve()))
    except ValueError:
        return "/" + str(path)


def _path_rules(cfg: Config) -> tuple[list[str], list[str]]:
    """(record rules for both modes, act-mode rules), from the configured folders."""
    state = _rule_path(cfg.state_dir)
    records = []
    for name in _OWN_RECORDS:
        tail = f"{name}**" if name.endswith("/") else name
        records += [f"Edit({state}/{tail})", f"Write({state}/{tail})",
                    f"Bash(*{cfg.state_dir}/{name}*)", f"Bash(*{cfg.state_dir.name}/{name}*)"]
    projects = _rule_path(cfg.projects_dir)
    act = [f"Edit({projects}/**)", f"Write({projects}/**)", f"NotebookEdit({projects}/**)"]
    return records, act


# diagnose mode runs without --dangerously-skip-permissions: a headless run is
# refused every tool call these rules do not allow. Read-only tools only.
# Left out on purpose, because one of them alone can write a file or run another
# command: sort (-o), uniq (an output file argument), sed (w and e), rg (--pre),
# find (-exec, -delete), awk, tee, xargs.
DIAGNOSE_ALLOW = [
    "Read", "Grep", "Glob",
    "Bash(ls*)", "Bash(cat *)", "Bash(head *)", "Bash(tail *)", "Bash(grep *)",
    "Bash(wc *)", "Bash(cut *)", "Bash(jq *)",
    "Bash(ps *)", "Bash(date)", "Bash(date -u*)", "Bash(date +*)", "Bash(stat *)",
    "Bash(git log*)", "Bash(git status*)", "Bash(git diff*)", "Bash(git show*)",
    "Bash(git -C * log*)", "Bash(git -C * status*)", "Bash(git -C * diff*)", "Bash(git -C * show*)",
    "Bash(python3 -m alertwatch.dbread*)", "Bash(python3 -m alertwatch.restart history*)",
]
_DIAGNOSE_DENY = ["Edit", "Write", "NotebookEdit", "Bash(git commit*)", "Bash(git * commit*)",
                  # git log, diff and show can write a file or run a configured program
                  "Bash(*--output*)", "Bash(*--ext-diff*)", "Bash(*--textconv*)",
                  "Bash(*alertwatch.rerun*)", "Bash(*alertwatch.merge*)",
                  "Bash(*alertwatch.restart restart*)"]


def _iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(now: float, msg: str) -> None:
    print(f"[{_iso(now)}] {msg}", flush=True)


def session_settings(cfg: Config) -> dict:
    """Deny rules for the run (deny always wins). backlog-run verified on 2026-09-03,
    and alert-watch on 2026-11-15, that `--settings` deny rules hold under
    --dangerously-skip-permissions."""
    deny = []
    records, act = _path_rules(cfg)
    extra = act if cfg.mode == "act" else _DIAGNOSE_DENY
    for rule in [*DENY_RULES, *_ALWAYS_DENY, *records, *extra]:
        if rule not in deny:
            deny.append(rule)
    return {"permissions": {"deny": deny}}


def permission_argv(cfg: Config) -> list[str]:
    """act: every tool except the deny rules. diagnose: only DIAGNOSE_ALLOW."""
    if cfg.mode == "act":
        return ["--dangerously-skip-permissions"]
    # Reads outside the working dir (~/projects) need --add-dir: the writer PID files
    # and alert-watch's own state.
    return ["--permission-mode", "default",
            "--add-dir", str(cfg.meettrack_run_dir.parent), "--add-dir", str(cfg.state_dir),
            "--allowedTools", *DIAGNOSE_ALLOW]


def run_env(cfg: Config, run_id: str = "") -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    local_bin = str(Path.home() / ".local" / "bin")
    parts = [local_bin] + [p for p in env.get("PATH", "/usr/bin:/bin").split(":") if p and p != local_bin]
    env.update({"PATH": ":".join(parts), "ALERT_WATCH_RUN": "1", "ALERT_SOURCE": "alert-watch",
                "MEETTRACK_DB_READ_ONLY": "1", "ALERT_WATCH_RUN_ID": run_id,
                "ALERT_WATCH_STATE": str(cfg.state_dir), "ALERT_WATCH_PROJECTS": str(cfg.projects_dir),
                "PYTHONPATH": ":".join(p for p in (str(HERE.parent), env.get("PYTHONPATH", "")) if p)})
    return env


def _kill_marked(run_id: str) -> int:
    """SIGKILL every process left over from this run, found by the run id in its
    environment. A process the run started in a new session escapes the process
    group kill; it cannot escape its inherited environment this way. Writers the
    supervisor relaunches come from cron and never carry the marker."""
    if not run_id:
        return 0
    marker = f"ALERT_WATCH_RUN_ID={run_id}".encode()
    killed = 0
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            pidfd = os.pidfd_open(int(proc.name))
        except OSError:
            continue
        try:  # read the marker after pinning, so the signal reaches this same process
            if marker in (proc / "environ").read_bytes().split(b"\0"):
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                killed += 1
        except OSError:
            pass
        finally:
            os.close(pidfd)
    return killed


def _first_line(text: str, limit: int = 120) -> str:
    line = next((l.strip() for l in str(text).splitlines() if l.strip()), "")
    return line[:limit]


def _helper_line(a: dict) -> str:
    kind = a.get("kind")
    if kind == "restart":
        return f"restart {a.get('key')} (attempts {a.get('attempts')})"
    if kind == "revert":
        return f"revert in {a.get('repo')} of {str(a.get('reverts', ''))[:10]}"
    return f"merge in {a.get('repo')} {str(a.get('from_sha', ''))[:10]}..{str(a.get('to_sha', ''))[:10]}"


def _safe(text) -> str:
    """Report text, and anything derived from it, cannot fake the prompt's markers."""
    return str(text).replace("<<<", "< < <").replace(">>>", "> > >")


def _recent_lines(recent: list[dict], acts: list[dict]) -> str:
    if not recent:
        return "(none)"
    out = []
    for r in recent:
        result = r.get("result") if isinstance(r.get("result"), dict) else {}
        firsts = "; ".join(e.get("first_line", "") for e in r.get("entries") or [])[:300]
        verdicts = ", ".join(f"{e.get('id')}={e.get('verdict')}" for e in result.get("entries") or []
                             if isinstance(e, dict))
        done = ", ".join(_helper_line(a) for a in acts if a.get("run_id") == r.get("run_id"))
        needs = " | ".join(str(n) for n in result.get("needs_rex") or [])
        out.append(_safe(f"- {r.get('run_id')} {_iso(float(r['started_at']))} status={r.get('status')}"
                         f" reports (data): {firsts or '-'}; verdicts: {verdicts or '-'};"
                         f" helper actions: {done or '-'}; needs_rex: {needs or '-'}"))
    return "\n".join(out)


def build_prompt(cfg: Config, entries: list[dict], recent: list[dict], counts: dict, now: float) -> str:
    template = (HERE / "prompt.md").read_text(encoding="utf-8")
    reports = "\n\n".join(
        f"<<<REPORT id={e['id']} source={_safe(str(e.get('source') or '')[:300])} "
        f"captured_at={e.get('captured_at', '')} attempts={e.get('attempts', 0)}>>>\n"
        f"{_safe(str(e['text'])[:REPORT_MAX])}\n<<<END REPORT>>>"
        for e in entries)
    fills = {
        "POLICY": (HERE / "POLICY.md").read_text(encoding="utf-8"),
        "NOW": _iso(now),
        "MODE": cfg.mode,
        "REPOS": ", ".join(cfg.repos),
        "DENIED": ", ".join(cfg.denied_paths),
        "RERUN_JOBS": ", ".join(sorted(cfg.rerun)),
        "MERGES_DAY": str(counts.get("merges_day", 0)),
        "MAX_MERGES_DAY": str(cfg.max_merges_day),
        "RECENT": _recent_lines(recent, ledger.actions(cfg.state_dir)),
        "ENTRIES": reports,
    }
    # One pass: text placed by one fill is never read as another placeholder.
    return re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: fills.get(m.group(1), m.group(0)), template)


def _claude_path(cfg: Config, env: dict) -> str:
    return shutil.which(cfg.claude_bin, path=env.get("PATH")) or cfg.claude_bin


def run_claude(cfg: Config, prompt: str, env: dict) -> dict:
    state = cfg.state_dir
    settings, mcp = state / "session-settings.json", state / "mcp-empty.json"
    settings.write_text(json.dumps(session_settings(cfg), indent=1), encoding="utf-8")
    mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    argv = [_claude_path(cfg, env), "-p", "-", "--output-format", "json", *permission_argv(cfg),
            "--settings", str(settings), "--strict-mcp-config", "--mcp-config", str(mcp),
            "--model", cfg.model]
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
            # A child that left the process group may still hold the pipes open:
            # kill it by its run id first, and never wait for the pipes forever.
            _kill_marked(env.get("ALERT_WATCH_RUN_ID", ""))
            try:
                out, err = proc.communicate(timeout=PIPE_DRAIN_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                for pipe in (proc.stdout, proc.stderr):
                    if pipe:
                        pipe.close()
                proc.wait()
                out, err = "", "alert-watch: output pipes stayed open after the kill; output lost"
    leftovers = _kill_marked(env.get("ALERT_WATCH_RUN_ID", ""))
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
            "stderr": err or "", "data": data, "leftovers_killed": leftovers}


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
    ledger.fsync_dir(state)


def _parse_due(value) -> float | None:
    try:
        when = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return when.timestamp()


def enqueue_due_checks(cfg: Config, st: dict, now: float) -> int:
    """A pending check becomes an inbox entry once it is due. Its id is fixed
    (check-<run>-<n>), so a crash before state.json is saved cannot enqueue it twice."""
    done = st.setdefault("checks_enqueued", [])
    added = 0
    # Every run kept (batches are pruned at 30 days), so a long pause loses no check.
    for rec in ledger.recent_runs(cfg.state_dir, now, CHECK_LOOKBACK_H):
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
            latest = float(rec.get("ended_at") or rec["started_at"]) + CHECK_MAX_DELAY_S
            if due > latest:
                due = latest  # the policy allows at most 24 hours
            if due > now:
                continue
            eid = f"check-{rec.get('run_id')}-{i}"
            if not inbox.seen(cfg.state_dir, eid):
                text = (f"Check after alert-watch merge in {chk.get('repo')} "
                        f"{chk.get('from_sha', '?')}..{chk.get('to_sha', '?')} (run {rec.get('run_id')}): "
                        f"{chk.get('what', '')}")
                inbox.add_entry(cfg.state_dir, text, CHECK_SOURCE, eid=eid)
                added += 1
            done.append(key)
    st["checks_enqueued"] = done[-5000:]
    return added


def check_result(result: dict, ids: list[str]) -> list[str]:
    """Problems with a run's result, as lines for Rex: a report with no valid verdict."""
    got = {e.get("id"): e.get("verdict") for e in result.get("entries") or [] if isinstance(e, dict)}
    return [f"report {i} got no valid verdict; check the run" for i in ids if got.get(i) not in VERDICTS]


def _cap_reached(cfg: Config, st: dict, now: float) -> bool:
    c = ledger.counts(cfg.state_dir, now)
    for cap, limit, window in (("runs_hour", cfg.max_runs_hour, 3600),
                               ("runs_day", cfg.max_runs_day, 86400)):
        if c[cap] >= limit:
            noted = st.setdefault("cap_noted", {})
            if now - float(noted.get(cap, 0)) >= window:
                _send_note(cfg, st, "", notes.cap_note(cap, c[cap], limit))
                noted[cap] = now
            _log(now, f"cap {cap} reached ({c[cap]} of {limit}); waiting")
            return True
    return False


def _summary(entry: dict) -> dict:
    return {"id": entry["id"], "source": str(entry.get("source") or "")[:300],
            "first_line": _first_line(entry.get("text", "")), "rule": entry.get("_rule"),
            "action": entry.get("_action")}


NOTE_TRIES = 5


def _send_note(cfg: Config, st: dict, run_id: str, text: str) -> str:
    """Send a note. A definite rejection is kept in state.json and retried on later
    passes; an uncertain send may have arrived, so it is never sent again."""
    status = notes.send(cfg, text)
    if status == "failed":
        st.setdefault("unsent_notes", []).append({"run_id": run_id, "text": text, "tries": 1})
    return status


def retry_notes(cfg: Config, st: dict, now: float) -> None:
    keep = []
    for item in st.get("unsent_notes") or []:
        status = notes.send(cfg, item["text"])
        if status == "failed" and item.get("tries", 1) + 1 < NOTE_TRIES:
            keep.append({**item, "tries": item.get("tries", 1) + 1})
            continue
        if status == "failed":
            _log(now, f"note for {item.get('run_id')} dropped after {NOTE_TRIES} failed sends")
            st["note_dropped_at"] = now  # the watchdog check reads this
        try:
            if item.get("run_id"):
                ledger.update_run(cfg.state_dir, item["run_id"], note_status=status)
        except (OSError, ValueError):
            pass
    st["unsent_notes"] = keep


def _pause(cfg: Config, st: dict, reason: str, now: float) -> None:
    (cfg.state_dir / "PAUSED").write_text(f"{_iso(now)} {reason}\n", encoding="utf-8")
    _send_note(cfg, st, "", notes.pause_note(reason, cfg.state_dir))
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
    st = _load_state(state)
    if st.get("unsent_notes") and not dry_run:
        retry_notes(cfg, st, t)  # even while paused: Rex must still get the notes
        _save_state(state, st)
    if (state / "PAUSED").exists():
        return 0
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
        _send_note(cfg, st, "", notes.failure_note(
            {"run_id": dead, "reason": "the dispatcher stopped during this run"}, []))

    inbox.clean_tmp(state, t)
    enqueue_due_checks(cfg, st, t)
    if _cap_reached(cfg, st, t):
        _save_state(state, st)
        return 0

    run_id = ledger.new_run_id(t)
    entries, broken = inbox.claim_all(state, run_id)
    for name, reason in broken:
        _log(t, f"{run_id}: broken inbox file {name}: {reason}")
    if broken:
        _send_note(cfg, st, run_id, notes.broken_note(run_id, broken, state))
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
    counts = {"merges_day": ledger.merges_in_window(state, t)}
    recent = [r for r in ledger.recent_runs(state, t, 24) if r.get("run_id") != run_id]
    prompt = build_prompt(cfg, looks, recent, counts, t)
    _log(t, f"{run_id}: starting a {cfg.mode} run for {len(looks)} report(s)")
    run = run_claude(cfg, prompt, run_env(cfg, run_id))
    data = run["data"]
    ended = now()
    record.update(ended_at=ended, rc=run["rc"], timed_out=run["timed_out"],
                  session_id=str((data or {}).get("session_id") or ""),
                  leftovers_killed=run.get("leftovers_killed", 0),
                  helper_actions=[a for a in ledger.actions(state) if a.get("run_id") == run_id])
    parsed = parse_result(str((data or {}).get("result") or "")) if data else None
    hit, until = (False, None) if parsed else usage_limit(data, run["stdout"], run["stderr"])

    if parsed:
        parsed["needs_rex"] = [*parsed["needs_rex"], *check_result(parsed, [e["id"] for e in looks])]
        record.update(status="ok", result=parsed)
        text = notes.run_note(record)
        record["note_text"] = text
        record["note_status"] = _send_note(cfg, st, run_id, text) if text else "none"
        inbox.finish(state, run_id, "done")
    elif hit:
        until = until or ended + cfg.limit_backoff_s
        record.update(status="limit", reason="Claude usage limit")
        st["limit_until"] = until
        _save_state(state, st)  # before the release, so a crash cannot retry at once
        # Only JSON that shows zero turns proves the run did nothing.
        part_way = data is None or started(data)
        if part_way:
            inbox.finish(state, run_id, "failed")  # it may have acted: never replayed
            record["reason"] = "Claude usage limit, part way through the run (or no JSON)"
        else:
            inbox.release(state, run_id, looks, count_attempt=False)
        if part_way or float(st.get("limit_noted_until") or 0) != until:
            record["note_text"] = notes.limit_note(until, part_way=part_way)
            record["note_status"] = _send_note(cfg, st, run_id, record["note_text"])
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
        # Only a run that shows zero turns provably did nothing; no JSON at all may
        # have acted before it died, so it is never replayed.
        if data is not None and not started(data) and not run["timed_out"]:
            failed_ids = inbox.release(state, run_id, looks, count_attempt=True)
        else:
            inbox.finish(state, run_id, "failed")
            failed_ids = [e["id"] for e in looks]
        record["note_text"] = notes.failure_note(record, failed_ids)
        record["note_status"] = _send_note(cfg, st, run_id, record["note_text"])

    ledger.update_run(state, run_id, **{k: v for k, v in record.items() if k != "run_id"})
    _log(ended, f"{run_id}: {record['status']} {record['reason']}".rstrip())
    if record["status"] == "failed" and ledger.fail_streak(state) >= cfg.fail_streak_pause:
        _pause(cfg, st, f"{cfg.fail_streak_pause} failed runs in a row", ended)
    inbox.prune(state, ended)
    _save_state(state, st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
