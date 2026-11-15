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
from alertwatch.result import parse_result, proved_idle, started, usage_limit
from alertwatch.rules import classify, load_rules
from backlogrun.cli import DENY_RULES

HERE = Path(__file__).resolve().parent
REPORT_MAX = 4000
CHECK_SOURCE = "alert-watch-check"
CHECK_MAX_DELAY_S = 24 * 3600
CHECK_LOOKBACK_H = 30 * 24
PIPE_DRAIN_S = 10
BATCH_MAX = 20  # reports per run (each cut to REPORT_MAX); the rest wait for the next pass
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
    "Read(~/.ssh/**)", "Read(~/.gnupg/**)", "Read(~/.claude/channels/**)", "Read(~/.claude/.credentials*)",
    "Read(~/.config/gh/**)", "Read(~/.git-credentials)", "Read(**/*.pem)", "Read(**/*.key)",
    "Read(**/*credentials*)", "Read(**/*secret*)",
    "Bash(*.ssh/*)", "Bash(*.gnupg*)", "Bash(*channels/telegram*)", "Bash(*.credentials*)",
    "Bash(*git-credentials*)", "Bash(*.pem*)", "Bash(*.key*)", "Bash(*id_rsa*)", "Bash(*id_ed25519*)",
    "Bash(*config/gh*)",
    # another process's environment (and this one's) can hold credentials
    "Read(//proc/**)", "Bash(*environ*)", "Bash(ps *e*)",
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
# Left out on purpose: cat, head, tail, grep, jq and ps can read any file or another
# process's environment without naming a denied path (grep -r, ps e); sort (-o),
# uniq (an output file), sed (w and e), rg (--pre), find (-exec, -delete), awk, tee
# and xargs can write a file or run another command by themselves.
DIAGNOSE_ALLOW = [
    # File contents only through these tools: they honor the Read deny rules (the
    # credential and .env rules), which shell commands such as cat or grep -r do not.
    "Read", "Grep", "Glob",
    # No git: log, show and diff print file contents (a credential committed in the
    # past included), which the Read deny rules cannot filter. gitinfo prints commit
    # lines and changed file names only.
    # No wc or date either: wc --files0-from and date -f print a file's lines in their
    # error messages. The prompt gives the time.
    "Bash(ls*)", "Bash(stat *)",
    "Bash(python3 -m alertwatch.dbread*)", "Bash(python3 -m alertwatch.restart history*)",
    "Bash(python3 -m alertwatch.gitinfo*)",
]
_DIAGNOSE_DENY = ["Edit", "Write", "NotebookEdit", "Bash(git commit*)", "Bash(git * commit*)",
                  # git log, diff and show can write a file or run a configured program,
                  # and -c or --config-env can make any git command run one (core.fsmonitor)
                  "Bash(*--output*)", "Bash(*--ext-diff*)", "Bash(*--textconv*)",
                  "Bash(git* -c*)", "Bash(git*--config-env*)", "Bash(*GIT_CONFIG*)",
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


# The run gets only these variables from the dispatcher (cron's environment, or a
# shell's when run by hand), so no credential in it reaches the run.
_ENV_KEEP = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "SHELL", "TZ",
             "PATH", "TMPDIR", "XDG_RUNTIME_DIR", "SSH_AUTH_SOCK")


def run_env(cfg: Config, run_id: str = "") -> dict:
    env = {k: os.environ[k] for k in _ENV_KEEP if os.environ.get(k)}
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
        firsts = "; ".join(e.get("first_line", "") for e in r.get("entries") or []
                           if e.get("rule") != "other-chat")[:300]
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
    def body(e: dict) -> str:
        text = str(e["text"])
        if len(text) <= REPORT_MAX:
            return _safe(text)
        where = cfg.state_dir / "batch" / str(e.get("_run_id", "")) / str(e.get("_file", ""))
        return (_safe(text[:REPORT_MAX]) + f"\n[report cut at {REPORT_MAX} of {len(text)} characters; "
                f"the whole report is the \"text\" field of {where}]")

    reports = "\n\n".join(
        f"<<<REPORT id={e['id']} source={_safe(str(e.get('source') or '')[:300])} "
        f"captured_at={e.get('captured_at', '')} attempts={e.get('attempts', 0)}>>>\n"
        f"{body(e)}\n<<<END REPORT>>>"
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
        fh.flush()
        os.fsync(fh.fileno())  # durable before the rename, so a power cut cannot empty it
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
    """Problems with a run's result, as lines for Rex: a report with no valid verdict,
    and a pending check whose due time cannot be read (it would never be enqueued)."""
    got = {e.get("id"): e.get("verdict") for e in result.get("entries") or [] if isinstance(e, dict)}
    out = [f"report {i} got no valid verdict; check the run" for i in ids if got.get(i) not in VERDICTS]
    for chk in result.get("pending_checks") or []:
        if not isinstance(chk, dict) or _parse_due(chk.get("due_at")) is None:
            what = chk.get("what", "") if isinstance(chk, dict) else str(chk)
            out.append(f"a follow-up check has no readable due time, so it will not run by itself; "
                       f"check it by hand: {str(what)[:200]}")
    return out


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
    if entry.get("_rule") == "other-chat":
        return {"id": entry["id"], "source": "", "first_line": "", "rule": "other-chat", "action": "drop"}
    return {"id": entry["id"], "source": str(entry.get("source") or "")[:300],
            "first_line": _first_line(entry.get("text", "")), "rule": entry.get("_rule"),
            "action": entry.get("_action")}


NOTE_TRIES = 5


def _queue_note(cfg: Config, st: dict, run_id: str, text: str) -> dict:
    """Put a note in the outbox in state.json, saved before anything else happens."""
    item = {"run_id": run_id, "text": text, "tries": 0, "state": "queued"}
    st.setdefault("unsent_notes", []).append(item)
    _save_state(cfg.state_dir, st)
    return item


def _deliver(cfg: Config, st: dict, item: dict, now: float) -> str:
    """Send one outbox note. It is marked `sending` (saved) before the send, so a crash
    during the send leaves it `sending`: that one may have arrived and is never sent
    again. A definite rejection stays for a retry; anything else leaves the outbox."""
    item["state"] = "sending"
    _save_state(cfg.state_dir, st)
    status = notes.send(cfg, item["text"])
    item["tries"] = item.get("tries", 0) + 1
    if status == "failed" and item["tries"] < NOTE_TRIES:
        item["state"] = "failed"
    else:
        if status == "failed":
            _log(now, f"note for {item.get('run_id')} dropped after {NOTE_TRIES} failed sends")
        if status != "accepted":
            st["note_dropped_at"] = now  # dropped or uncertain: the watchdog warns for a day
        st["unsent_notes"] = [n for n in st["unsent_notes"] if n is not item]
    _save_state(cfg.state_dir, st)
    if item.get("run_id"):
        try:
            ledger.update_run(cfg.state_dir, item["run_id"], note_status=status)
        except (OSError, ValueError):
            pass
    return status


def _send_note(cfg: Config, st: dict, run_id: str, text: str) -> str:
    return _deliver(cfg, st, _queue_note(cfg, st, run_id, text), time.time())


def retry_notes(cfg: Config, st: dict, now: float) -> None:
    for item in list(st.get("unsent_notes") or []):
        if item.get("state") == "sending":  # a send a crash interrupted: it may have arrived
            st["unsent_notes"] = [n for n in st["unsent_notes"] if n is not item]
            st["note_dropped_at"] = now
            _log(now, f"note for {item.get('run_id')} was being sent when the dispatcher stopped; "
                      "not sent again")
            _save_state(cfg.state_dir, st)
            if item.get("run_id"):
                try:
                    ledger.update_run(cfg.state_dir, item["run_id"], note_status="uncertain")
                except (OSError, ValueError):
                    pass
            continue
        _deliver(cfg, st, item, now)


def _kill_until_gone(run_id: str, tries: int = 5) -> bool:
    """Kill every process a run left behind and check that none is left."""
    for _ in range(tries):
        if _kill_marked(run_id) == 0:
            return True
        time.sleep(0.2)
    return _kill_marked(run_id) == 0


def _read_run(state: Path, run_id: str) -> dict | None:
    try:
        return json.loads((state / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _recover_batch(cfg: Config, st: dict, run_id: str, now: float) -> bool:
    """A batch a dead dispatcher left. If its run record already holds the decision
    (`dest`), carry it out; the result and pending checks are in the record. If not,
    the run may have acted: the batch goes to failed/ and Rex is told."""
    state = cfg.state_dir
    rec = _read_run(state, run_id) or {}
    dest = rec.get("dest")
    if dest and rec.get("note_status") == "queued" and rec.get("note_text") and not any(
            n.get("run_id") == run_id for n in st.get("unsent_notes") or []):
        _queue_note(cfg, st, run_id, rec["note_text"])  # retry_notes sends it this pass
    if dest in ("done", "failed"):
        inbox.finish(state, run_id, dest)
    elif dest in ("release", "release_count"):
        inbox.release(state, run_id, inbox.batch_entries(state, run_id), count_attempt=dest == "release_count")
    else:
        # Record the failed decision and its note first (replacing a missing or broken
        # record), then queue the note, then move the batch: a second crash at any point
        # is finished by the next pass from what is recorded.
        reason = "the dispatcher stopped during this run"
        text = notes.failure_note({"run_id": run_id, "reason": reason}, [])
        base = rec if rec.get("run_id") == run_id else {"run_id": run_id, "started_at": now, "entries": []}
        ledger.replace_run(state, {**base, "status": "failed", "reason": reason, "ended_at": now,
                                   "dest": "failed", "note_text": text, "note_status": "queued"})
        item = _queue_note(cfg, st, run_id, text)
        inbox.finish(state, run_id, "failed")
        _deliver(cfg, st, item, now)
        if _streak(cfg, st) >= cfg.fail_streak_pause and not (state / "PAUSED").exists():
            _pause(cfg, st, f"{cfg.fail_streak_pause} failed runs in a row", now)
    _log(now, f"batch {run_id} was left by a dispatcher that stopped; finished as {dest or 'failed'}")
    return True


def _streak(cfg: Config, st: dict) -> int:
    return ledger.fail_streak(cfg.state_dir, since=float(st.get("streak_reset_at") or 0))


def _pause(cfg: Config, st: dict, reason: str, now: float) -> None:
    """Pause as one recoverable step. 1: the reset point (the failures behind this
    pause never count again), the pause note in the outbox, and a pending-pause mark,
    in one state.json save. 2: write PAUSED. 3: clear the mark. 4: send the note.
    A crash anywhere is finished by _finish_pause on the next pass, and the note is
    never lost."""
    text = notes.pause_note(reason, cfg.state_dir)
    st["streak_reset_at"] = now
    st["pending_pause"] = {"reason": reason, "at": now}
    item = _queue_note(cfg, st, "", text)  # saves state.json with all of the above
    _write_paused(cfg, reason, now)
    st.pop("pending_pause", None)
    _save_state(cfg.state_dir, st)
    _deliver(cfg, st, item, now)
    _log(now, f"paused: {reason}")


def _write_paused(cfg: Config, reason: str, now: float) -> None:
    path = cfg.state_dir / "PAUSED"
    tmp = path.with_name(".tmp-PAUSED")
    tmp.write_text(f"{_iso(now)} {reason}\nstreak_reset_at={now}\n", encoding="utf-8")
    os.replace(tmp, path)
    ledger.fsync_dir(cfg.state_dir)


def _finish_pause(cfg: Config, st: dict, now: float) -> None:
    """A pause a crash interrupted. If PAUSED is missing, either it was never written
    or Rex deleted it before this pass; the two cannot be told apart, so it is written
    (fail closed). The note is already in the outbox, and the reset point is set, so
    deleting PAUSED again resumes."""
    pending = st.get("pending_pause")
    if not pending:
        return
    if not (cfg.state_dir / "PAUSED").exists():
        _write_paused(cfg, str(pending.get("reason") or "paused"), float(pending.get("at") or now))
    st.pop("pending_pause", None)
    _save_state(cfg.state_dir, st)
    _log(now, "finished a pause a crash interrupted")


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
    if not dry_run:
        # First an interrupted pause, so PAUSED exists before its note can reach Rex.
        _finish_pause(cfg, st, t)
        # Before the PAUSED and usage-limit gates: a dead dispatcher's run is never
        # left running, and Rex still gets every note, while paused.
        for dead in inbox.stale_batches(state):
            if not _kill_until_gone(dead):
                _log(t, f"batch {dead}: its run still has processes after SIGKILL; no new run this pass")
                _save_state(state, st)
                return 0
            _recover_batch(cfg, st, dead, t)
        if st.get("unsent_notes"):
            retry_notes(cfg, st, t)
    # Before any new run: a crash between a third failure and its pause must not let
    # the next pass carry on.
    if not dry_run:
        if not (state / "PAUSED").exists() and _streak(cfg, st) >= cfg.fail_streak_pause:
            _pause(cfg, st, f"{cfg.fail_streak_pause} failed runs in a row", t)
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

    inbox.clean_tmp(state, t)
    enqueue_due_checks(cfg, st, t)
    if _cap_reached(cfg, st, t):
        _save_state(state, st)
        return 0

    run_id = ledger.new_run_id(t)
    entries, broken = inbox.claim_all(state, run_id, limit=BATCH_MAX)
    for name, reason in broken:
        _log(t, f"{run_id}: broken inbox file {name}: {reason}")
    if broken:
        _send_note(cfg, st, run_id, notes.broken_note(run_id, broken, state))
    if not entries and not broken:
        _save_state(state, st)
        return 0
    looks, quiet = [], []
    for entry in entries:
        entry["_run_id"] = run_id
        entry["_action"], entry["_rule"] = classify(entry, rules)
        chat = str(entry.get("chat_id") or "")
        if chat and chat not in cfg.watch_chats:
            # A message to another chat is deleted at once: its text is never kept,
            # summarised, put in a prompt or a note, now or in a later run's history.
            entry["_action"], entry["_rule"] = "drop", "other-chat"
            entry["text"] = entry["source"] = ""
            try:
                (state / "batch" / run_id / entry["_file"]).unlink()
            except OSError:
                pass
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
    clean = data is not None and not run["timed_out"] and run["rc"] == 0 and not data.get("is_error")
    ignored = bool(parsed) and not clean
    if ignored:
        parsed = None  # a result block from a run that failed or timed out is not a success
    hit, until = (False, None) if parsed else usage_limit(data, run["stdout"], run["stderr"])
    idle = proved_idle(data) and not run["timed_out"]

    # 1. decide; 2. persist the decision and result (a crash after this is finished by
    # _recover_batch, and the pending checks are safe); 3. move the batch; 4. the note.
    if parsed:
        parsed["needs_rex"] = [*parsed["needs_rex"], *check_result(parsed, [e["id"] for e in looks])]
        record.update(status="ok", result=parsed, dest="done")
    elif hit:
        until = until or ended + cfg.limit_backoff_s
        st["limit_until"] = until
        _save_state(state, st)
        record.update(status="limit", dest="release" if idle else "failed",
                      reason="Claude usage limit" if idle else
                      "Claude usage limit, part way through the run (or no proof it was idle)")
    else:
        if run["timed_out"]:
            reason = f"timed out after {cfg.run_timeout_s} s"
        elif data is None:
            reason = f"exited with rc={run['rc']} and no JSON output"
        else:
            reason = f"no readable result (rc={run['rc']})"
        if ignored:
            reason += "; its result block was ignored because the run did not end cleanly"
        record.update(status="failed", reason=reason, dest="release_count" if idle else "failed",
                      stderr_tail=run["stderr"][-4000:], stdout_tail=run["stdout"][-4000:])
    # The note is decided and queued with the decision, before the batch moves, so a
    # crash at any later point cannot lose it.
    if record["dest"] == "failed" and record["status"] == "failed":
        failed_ids = [e["id"] for e in looks]
    elif record["dest"] == "release_count":
        failed_ids = [e["id"] for e in looks if int(e.get("attempts") or 0) + 1 >= 2]
    else:
        failed_ids = []
    if record["status"] == "ok":
        text = notes.run_note(record)
    elif record["status"] == "limit":
        part_way = record["dest"] == "failed"
        text = (notes.limit_note(until, part_way=part_way)
                if part_way or float(st.get("limit_noted_until") or 0) != until else None)
        if text:
            st["limit_noted_until"] = until
    else:
        text = notes.failure_note(record, failed_ids)
    # One atomic write holds the decision, the result and the note. The outbox entry
    # comes after it; a crash between them is repaired by _recover_batch, which
    # re-queues a note the record marks queued but the outbox lacks.
    record["note_text"] = text
    record["note_status"] = "queued" if text else "none"
    ledger.update_run(state, run_id, **{k: v for k, v in record.items() if k != "run_id"})
    item = _queue_note(cfg, st, run_id, text) if text else None

    if record["dest"] in ("done", "failed"):
        inbox.finish(state, run_id, record["dest"])
    else:
        inbox.release(state, run_id, looks, count_attempt=record["dest"] == "release_count")
    if item:
        record["note_status"] = _deliver(cfg, st, item, ended)

    ledger.update_run(state, run_id, **{k: v for k, v in record.items() if k != "run_id"})
    _log(ended, f"{run_id}: {record['status']} {record['reason']}".rstrip())
    if record["status"] == "failed" and _streak(cfg, st) >= cfg.fail_streak_pause:
        _pause(cfg, st, f"{cfg.fail_streak_pause} failed runs in a row", ended)
    inbox.prune(state, ended)
    _save_state(state, st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
