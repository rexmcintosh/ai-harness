"""Watchdog runner: collect signals -> triage -> emit an escalation report.

Invoked by ``run-watchdog.sh``. Prints a human-readable report of fired checks
to stdout and a single machine-readable JSON line (prefixed ``WATCHDOG_JSON:``)
the shell parses to decide whether to escalate to the investigator agent.

Healthy polls print nothing escalation-worthy and cost no tokens — the agent is
only invoked by the shell when ``escalate`` is true.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import tomllib

from .triage import (
    CheckStatus,
    LEVELS,
    check_alertwatch,
    check_backup,
    check_bebop_runs,
    check_cron_log,
    check_disk,
    check_log_coverage,
    check_mini_guard,
    check_meet_clock,
    check_meet_freshness,
    check_meet_liveness,
    check_orphan_processes,
    check_outside_alarm,
    check_run_age,
    check_service_active,
    triage,
)
from . import jev_shadow
from .metrics import sum_counter, count_matches, check_budget, parse_count_header, check_rate

# Default to the MAIN checkout so the installed cron job watches production, not
# a worktree. Override with WATCHDOG_BASE for testing/relocation.
BASE = Path(os.environ.get("WATCHDOG_BASE", "/home/dev/projects/ai-harness"))

# Cron logs to scan for error markers: (label, path).
CRON_LOGS = [
    ("meettrack-ingest", Path("/home/dev/projects/splash_poller/logs/ingest_entries.cron.log")),
    ("meettrack-supervise", Path("/home/dev/projects/splash_poller/logs/supervise.cron.log")),
    # the daily meet coverage check: a crash here would blind the gap alerts
    ("meettrack-coverage", Path("/home/dev/projects/splash_poller/logs/coverage_check.cron.log")),
    # hourly meet discovery: a crash here means new meets are not registered
    ("meettrack-discover", Path("/home/dev/projects/splash_poller/logs/discover.cron.log")),
]
# Logs whose good runs each end with a known pattern (label -> regex, searched in the
# whole text). Only what follows its last match is judged, so one failed run stops
# alerting when the next run finishes. Without this, discovery's 2 lines an hour
# would keep a crash in the 50-line tail for about a day and repeat the alert every
# 6 hours.
# A good discovery run prints its scraped= line, then its notes ([twin], [reuse],
# [annp note], [probe warn], indented; a note's error text may span lines), then
# its probed= line, and nothing after it. Between the two, any line counts as a
# note except another "[<time>] [discover]" line or a traceback header. The
# blind-index path prints probed= WITHOUT scraped= and then its ERROR, so it never
# ends a good run here, even if the step between those two lines crashes: its
# traceback (stderr, unbuffered) lands before the buffered probed= line and breaks
# the pair. The probed= line's time (`end_ts`) is when the run finished.
# The watchdog depends on this text; see the README.
_DISCOVER_LINE = r"\[[^\]\n]+\] \[discover\] "
class _CoverageRunEnd:
    """RUN_END for the daily coverage check log: a small line parser, not a regex.

    A coverage run prints one summary line, `[<time>] [coverage] season=...`, last,
    and nothing after it. The lines before it in the same run are its notes
    ([coverage warn], tg-send's stderr) and its errors: "[coverage] ERROR calendar:"
    still ends with a failed=0 summary, and a refused send ends with failed=1 or more.
    There is no start line, so a run is the text since the previous summary line of
    any shape (or the start of the log). It is finished when its summary has the
    known shape with failed=0 and no "[coverage] ERROR" line is in that text. A
    crashed run prints no summary, so its traceback joins the next run's text and is
    allowed there: that next good run clears it. finditer() yields the finished runs'
    summary-line matches in order, which is all check_cron_log reads. The age check
    reads the summary's time (`end_ts`, taken after the reads and before the sends,
    seconds before the run ends); `groupindex` tells cron_status the group is there."""
    _LINE = r"\[[^\]\n]+\] \[coverage\] "
    _SUMMARY = re.compile(r"^\[(?P<end_ts>[^\]\n]+)\] \[coverage\] season=[^\n]*$", re.MULTILINE)
    groupindex = _SUMMARY.groupindex
    _GOOD = re.compile(rf"{_LINE}season=\S+ expected=\d+ tracked=\d+ alerts=\d+ "
                       r"reminders=\d+ blind=\d+ sent=\d+ failed=0")
    _ERROR = "[coverage] ERROR"

    def finditer(self, text: str):
        start = 0
        for summary in self._SUMMARY.finditer(text):
            if (self._GOOD.fullmatch(summary.group(0))
                    and self._ERROR not in text[start:summary.start()]):
                yield summary
            start = summary.end()


RUN_END = {
    "meettrack-discover": re.compile(
        rf"^{_DISCOVER_LINE}scraped=[^\n]*\n"
        rf"(?:(?!{_DISCOVER_LINE}|Traceback \(most recent call last\):)[^\n]*\n)*"
        r"^\[(?P<end_ts>[^\]\n]+)\] \[discover\] probed=\d+/\d+[^\n]*$", re.MULTILINE),
    "meettrack-coverage": _CoverageRunEnd(),
}
# Logs whose finished runs must also be recent (label -> hours). A cron that stops
# running writes nothing, so the error-marker rule alone never sees it. The age is
# read from the RUN_END match's `end_ts` group.
RUN_MAX_AGE_H = {
    "meettrack-discover": 3,     # hourly at :17; two missed runs leave it at most 2h43m old at a poll
    # daily at 07:00 Lisbon time (06:00 UTC in summer): the autumn clock change makes
    # one 25h gap; a missed run warns at the first poll past 26h, about 2.5h later
    # (1.5h on the autumn change day, 3.5h on the spring one)
    "meettrack-coverage": 26,
}
SERVICES = ["tailscaled"]


def cron_log_status(label: str, text: str) -> CheckStatus:
    """The cron-log rule for one log: alerts and the Jev shadow both use it."""
    return check_cron_log(label, text, run_end=RUN_END.get(label))


def cron_status(label: str, text: str, now_epoch: float) -> CheckStatus:
    """The alerting status for one cron log: its log rule, then its run age. The
    age stays out of cron_log_status, so the Jev shadow's verdict is the log's alone."""
    status = cron_log_status(label, text)
    if label not in RUN_MAX_AGE_H:
        return status
    rule = RUN_END.get(label)
    if "end_ts" not in getattr(rule, "groupindex", {}):
        # A config edit must never stop the poll: say what is wrong instead.
        return CheckStatus(status.name, "crit" if status.level == "crit" else "warn",
                           f"{status.summary}; watchdog config: {label} has an age limit "
                           f"but no RUN_END time (end_ts) to read",
                           evidence=status.evidence)
    return check_run_age(status, text, rule, now_epoch, max_age_hours=RUN_MAX_AGE_H[label])


def load_state(path) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}  # missing or corrupt -> start clean; never crash a poll


def save_state(path, state: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with tmp.open('w') as handle:
        handle.write(json.dumps(state, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(target)
    fd = os.open(target.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def _read(path) -> str | None:
    try:
        return Path(path).read_text(errors="ignore")
    except OSError:
        return None


def _cmd(args) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _load_monitors() -> dict:
    """Load watchdog/monitors.toml (the spike-monitor config). Absent/broken -> {} so
    the watchdog still runs its failure checks without it."""
    try:
        with open(BASE / "watchdog" / "monitors.toml", "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _supabase_count(url: str, key: str, table: str) -> int | None:
    """Read-only row-count ESTIMATE via PostgREST (Prefer: count=planned): the
    planner's figure from pg_class, no table scan. None on any failure — a
    count we can't read must not crash or fake an alert.

    Until 10 Oct 2026 this was count=exact: a full scan of results, splits and
    swimmers every 30 minutes, the top two database users by total time on a
    0.5 GB machine (hq/swimtrack-db-outage-rca-2026-10-10.md). The estimate
    moves when autovacuum/analyze runs, which a real spike (thousands of rows)
    triggers within minutes, so rows/hour stays a usable spike budget."""
    try:
        import requests
        r = requests.head(f"{url}/rest/v1/{table}?select=*",
                          headers={"apikey": key, "Authorization": f"Bearer {key}",
                                   "Prefer": "count=planned", "Range": "0-0"},
                          timeout=10)
        return parse_count_header(r.headers.get("Content-Range"))
    except Exception:  # noqa: BLE001 — network/parse error -> skip this metric
        return None


def _note_failure(error: dict | None, exc: Exception) -> None:
    """Record a failed read as far as it is actually visible: the response
    status and body when there is one, None/'' when there is not. A caller must
    be able to tell a schema problem from a timeout — and must not guess when
    the failure carries nothing to read."""
    if error is None:
        return
    resp = getattr(exc, "response", None)
    error["status"] = getattr(resp, "status_code", None)
    error["body"] = (getattr(resp, "text", "") or "")[:300]


def _supabase_rows(url: str, key: str, path: str, error: dict | None = None) -> list | None:
    """Read-only PostgREST GET returning rows. None on any failure — a read we
    can't make must not crash a poll or fake an alert. `error`, when a dict is
    passed, is FILLED with what the failure looked like (see _note_failure)."""
    try:
        import requests
        r = requests.get(f"{url}/rest/v1/{path}",
                         headers={"apikey": key, "Authorization": f"Bearer {key}"},
                         timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001 — network/parse error -> skip this check
        _note_failure(error, exc)
        return None


# The registry slice the absence alert reads. The per-event coverage columns
# arrive with an owner-applied MeetTrack migration (splash_poller
# migrations/2026-09-18_meet_registry_tick_coverage.sql), so production may not
# have them yet.
_MEET_COLS = ("sr_meet_id,name,nation,feed_type,ingest_status,last_ingest_at,updated_at,"
              "start_date,end_date")
_MEET_COVERAGE_COLS = ("events_published,events_with_results,last_tick_errors,"
                       "coverage_at")
# PostgREST 12 answers a missing column with 400 PGRST204; older builds pass
# PostgreSQL's 42703 through. Anything else — a timeout, a 5xx, a parse error —
# is not evidence about the schema and must not be reported as if it were.
_MISSING_COLUMN_CODES = ("42703", "PGRST204")


def _meet_registry_rows(url: str, key: str, since: str,
                        nations: tuple | list | None = ("POR",)) -> list | None:
    """Rows for check_meet_freshness, with the coverage columns when they exist.

    Portugal only by default (``nations``): the meet rules read racing hours
    in Europe/Lisbon, and a leftover Bulgarian meet once raised a Lisbon-hours
    alert. None/empty = every nation.

    A 400 on a not-yet-applied migration must cost the coverage RULE, never the
    whole check — without the retry, a missing column would silently take the
    absence alert itself off the air."""
    scope = f"&nation=in.({','.join(nations)})" if nations else ""

    def fetch(cols, error=None):
        return _supabase_rows(url, key,
                              f"meet_registry?select={cols}&updated_at=gt.{since}{scope}",
                              error=error)

    failure: dict = {}
    rows = fetch(f"{_MEET_COLS},{_MEET_COVERAGE_COLS}", error=failure)
    if rows is not None:
        return rows
    rows = fetch(_MEET_COLS)
    if rows is not None:
        body = failure.get("body") or ""
        if failure.get("status") == 400 and any(c in body for c in _MISSING_COLUMN_CODES):
            print("meet_registry has no tick-coverage columns yet — freshness "
                  "check running without the per-event coverage rule")
        else:
            print("meet_registry coverage select failed; using the plain "
                  "freshness rule this run")
    return rows


def _expect_cover(mf: dict, now_epoch: int) -> tuple[list[CheckStatus], bool]:
    """(statuses to add, whether this watchdog still runs its own meet rules).

    - ``defer_to_expect`` off, or the expect monitor never ran on this host
      (no state file): no status, the meet rules run here as before.
    - its last run is at most ``expect_fresh_min`` old: one ok status, and the
      meet rules are left to it (no double alerts, no double registry reads).
    - older: a direct-path warning that the monitor has stopped, and the meet
      rules run here as the fallback.
    """
    if not mf.get("defer_to_expect", False):
        return [], True
    path = Path(os.environ.get("WATCHDOG_EXPECT_STATE",
                               str(BASE / "watchdog" / "expect-state.json")))
    last = load_state(path).get("last_run")
    if not isinstance(last, (int, float)):
        return [], True
    age = (now_epoch - float(last)) / 60
    if age <= float(mf.get("expect_fresh_min", 15)):
        return [CheckStatus("meets.expect-heartbeat", "ok",
                            f"live-meet checks run by the expect monitor ({age:.0f}m ago)")], False
    return [CheckStatus(
        "meets.expect-heartbeat", "warn",
        f"the MeetTrack expect monitor last ran {age:.0f}m ago; the watchdog's own "
        f"meet rules are covering for it",
        evidence=f"check the crontab line for watchdog/run-expect.sh and {path.name}")], True


def collect_metrics(now_epoch: int, prior_metrics: dict) -> tuple[list[CheckStatus], dict]:
    """Layer 1+2 spike checks. Returns (statuses, new_metrics). new_metrics carries the
    current readings so the next poll can compute Supabase rows/hour deltas."""
    cfg = _load_monitors()
    out: list[CheckStatus] = []
    new_metrics: dict = {}
    window = int(cfg.get("settings", {}).get("window_lines", 6))

    # Layer 1a — windowed log-counter budgets
    for m in cfg.get("log_counters", []):
        text = _read(m["log"])
        if text is None:
            continue  # log absent (job not on this host) -> skip
        value = sum_counter(text, m["key"], lines=window)
        out.append(check_budget(m["name"], value, warn_at=m["warn"], crit_at=m["crit"]))
        new_metrics[m["name"]] = {"value": value, "ts": now_epoch}

    # Layer 1b — live process-count budgets
    procs = cfg.get("processes", [])
    if procs:
        ps = _cmd(["ps", "-eo", "args"])
        for m in procs:
            value = count_matches(ps, m["pattern"])
            out.append(check_budget(m["name"], value, warn_at=m["warn"], crit_at=m["crit"]))
            new_metrics[m["name"]] = {"value": value, "ts": now_epoch}

    # Layer 2 — Supabase row-growth (rows/hour) per hot table
    sb = cfg.get("supabase")
    if sb:
        key = os.environ.get(sb.get("key_env", "SUPABASE_SERVICE_ROLE_KEY"), "")
        if key:
            for table in sb.get("tables", []):
                current = _supabase_count(sb["url"], key, table)
                mkey = f"sb:{table}"
                prev_rec = prior_metrics.get(mkey, {})
                prev, prev_ts = prev_rec.get("value"), prev_rec.get("ts")
                elapsed = now_epoch - prev_ts if prev_ts else 0
                out.append(check_rate(table, current, prev, elapsed,
                                      warn_per_hour=sb["warn_per_hour"],
                                      crit_per_hour=sb["crit_per_hour"]))
                if current is not None:
                    new_metrics[mkey] = {"value": current, "ts": now_epoch}
                else:  # keep the old reading so a transient failure doesn't reset the baseline
                    if prev is not None:
                        new_metrics[mkey] = prev_rec

    # Layer 3 — the MeetTrack ABSENCE alert: a live meet gone quiet, a meet
    # awaiting launch too long, or a recent terminal failure. Every other
    # check here is a spike budget; this is the floor (see check_meet_freshness).
    mf = cfg.get("meet_freshness")
    if mf:
        cover, run_rules = _expect_cover(mf, now_epoch)
        out.extend(cover)
        if not run_rules:
            # The expect monitor (watchdog/expect_run.py, every 5 min) owns
            # the live-meet checks while it runs: one alert per problem.
            mf = None
    if mf and sb:
        key = os.environ.get(sb.get("key_env", "SUPABASE_SERVICE_ROLE_KEY"), "")
        if key:
            from datetime import datetime, timedelta, timezone as _tz
            # 'Z' suffix, never '+00:00' — a '+' in a query string is a space.
            since = (datetime.now(_tz.utc) - timedelta(days=14)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            rows = _meet_registry_rows(sb["url"], key, since,
                                       nations=mf.get("nations", ["POR"]))
            if rows is None:
                # Never "nothing to report": on 10 Oct 2026 an unreadable
                # registry silently skipped every meet rule for two hours.
                out.append(CheckStatus(
                    "meets.registry", "crit",
                    "cannot read meet_registry: the database is unreachable or failing, "
                    "so live meets are NOT being checked",
                    evidence="see the meettrack cron logs for ReadTimeout / 5xx lines"))
            else:
                out.append(check_meet_freshness(
                    rows, now_epoch,
                    stale_warn_min=mf.get("stale_warn_min", 30),
                    stale_crit_min=mf.get("stale_crit_min", 75),
                    launch_overdue_min=mf.get("launch_overdue_min", 30),
                    coverage_gap_warn=mf.get("coverage_gap_warn", 3),
                    coverage_gap_pct=mf.get("coverage_gap_pct", 15),
                    # Unset -> the counters inherit stale_warn_min.
                    coverage_max_age_min=mf.get("coverage_max_age_min"),
                    morning_grace_until=mf.get("morning_grace_until", 13)))
                # Its own check name, so its alert and cooldown never hide
                # behind a freshness alert that is already in crit.
                out.append(check_meet_liveness(
                    rows, now_epoch,
                    tick_warn_min=mf.get("tick_warn_min", 10),
                    tick_crit_min=mf.get("tick_crit_min", 30)))
            # The meet rules fall back to UTC without Europe/Lisbon; that
            # alerts instead of passing silently, registry read or not.
            out.append(check_meet_clock())

    return out, new_metrics


def alertwatch_status(now_epoch: float) -> CheckStatus | None:
    """The alert-watch check, or None until alert-watch is switched on here. Capture
    in tg-send creates the state dir and fills the inbox before any dispatcher runs,
    so only the ENABLED file (written when the cron line is installed) turns it on."""
    state = Path(os.environ.get("ALERT_WATCH_STATE")
                 or Path.home() / ".local" / "state" / "alert-watch")
    if not (state / "ENABLED").exists():
        return None
    try:
        from alertwatch import inbox as aw_inbox, ledger as aw_ledger
        counted = [r for r in aw_ledger.recent_runs(state, now_epoch, 24 * 7)
                   if r.get("status") != "skipped"]
        unsure = [r for r in counted if float(r["started_at"]) >= now_epoch - 86400
                  and r.get("note_status") in ("failed", "uncertain")]
        note = unsure[0].get("note_status") if unsure else None
        try:
            dropped = float(json.loads((state / "state.json").read_text()).get("note_dropped_at") or 0)
        except (OSError, ValueError):
            dropped = 0.0
        if now_epoch - dropped < 86400:
            note = "failed"
        newest = counted[0].get("status") if counted else None
        if newest == "running" and now_epoch - float(counted[0]["started_at"]) > 3600 + 900:
            newest = "failed"  # past the run's time limit plus a margin: it is stuck
        pause = state / "PAUSED"
        try:
            paused_age = now_epoch - pause.stat().st_mtime
        except OSError:
            paused_age = None
        return check_alertwatch(aw_inbox.oldest_age_s(state, now_epoch),
                                paused_age is not None, newest, note_status=note,
                                paused_age_s=paused_age)
    except Exception as e:  # a broken alert-watch must not blind the watchdog
        return CheckStatus("alertwatch", "warn", f"alert-watch state unreadable: {e.__class__.__name__}")


def read_guard_heartbeat(key: Path) -> str | None:
    """The mini guard's last-tick.json over SSH with its read-only key, or None when
    SSH itself failed (mini unreachable). Bad output with a good exit is returned as
    is, so the check can tell "unreadable" from "unreachable"."""
    try:
        proc = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                               "-o", "ConnectTimeout=10", "-i", str(key), "bebop_admin@mini"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 255:      # ssh itself failed: the mini did not answer
        return None
    return proc.stdout if proc.returncode == 0 else ""   # reachable, but no heartbeat


OUTSIDE_ALARM_SECRET = Path(os.environ.get("OUTSIDE_ALARM_SECRET")
                            or Path.home() / ".config" / "bebop-uptime" / "secret")
OUTSIDE_ALARM_URL = "https://bebop-uptime.rex-651.workers.dev/status"
SECRET_OK = re.compile(r"[0-9a-f]{64}")   # the one format (openssl rand -hex 32); safe in a curl config line


def read_outside_alarm(secret_path: Path) -> tuple[str | None, str]:
    """(status JSON, "") or (None, why not). The secret goes to curl on stdin as a
    header, never on the command line or in the URL."""
    try:
        secret = secret_path.read_text().strip()
    except OSError:
        return None, "the server cannot read ~/.config/bebop-uptime/secret"
    if not SECRET_OK.fullmatch(secret):
        return None, "~/.config/bebop-uptime/secret is not 64 lowercase hex characters"
    try:
        proc = subprocess.run(["curl", "-sS", "-m", "15", "--retry", "2", "--retry-delay", "5",
                               "-w", "\n%{http_code}", "-K", "-", OUTSIDE_ALARM_URL],
                              capture_output=True, text=True, timeout=75,
                              input=f'header = "Authorization: Bearer {secret}"\n')
    except (OSError, subprocess.SubprocessError):
        return None, "could not be reached from the server (curl did not run)"
    body, _, code = proc.stdout.rpartition("\n")
    if proc.returncode == 0 and code == "200":
        return body, ""
    if code == "404":
        return None, ("refused the server's secret (404): the Worker's PING_SECRET and "
                      "~/.config/bebop-uptime/secret differ, or the Worker was replaced")
    if code.isdigit() and code != "000":
        return None, f"answered HTTP {code} (a Cloudflare or Worker fault)"
    return None, "could not be reached from the server (network, DNS or Cloudflare)"


def collect(now_epoch: int, prior_metrics: dict | None = None) -> tuple[list[CheckStatus], dict]:
    """Gather every signal into normalized statuses. Returns (statuses, new_metrics).
    Missing optional signals are skipped (not invented as failures)."""
    out: list[CheckStatus] = []

    bebop_log = _read(BASE / "bebop" / "logs" / "runs.log")
    if bebop_log is not None:
        out.append(check_bebop_runs(bebop_log, now_epoch))

    out.append(check_disk(_cmd(["df", "-P", "/"])))

    backup_state = Path(os.environ.get("RESTIC_OFFSITE_STATE")
                        or Path.home() / ".local" / "state" / "restic-offsite")
    if backup_state.is_dir():  # only where the off-site backup is installed
        out.append(check_backup(_read(backup_state / "last-success.json"), now_epoch))

    for unit in SERVICES:
        out.append(check_service_active(unit, _cmd(["systemctl", "is-active", unit])))

    out.append(check_orphan_processes(_cmd(["ps", "-ww", "-eo", "pid,ppid,etime,args"])))

    found, missing = [], []
    for label, path in CRON_LOGS:
        text = _read(path)
        if text is None:  # absent log = job may be paused or not installed here; skip
            missing.append(label)
            continue
        found.append(label)
        out.append(cron_status(label, text, now_epoch))
    out.append(check_log_coverage(found, missing))

    guard_key = Path(os.environ.get("MINI_GUARD_KEY") or Path.home() / ".ssh" / "mini_guard_read")
    if guard_key.exists():  # only once mini-guard is installed (its installer makes the key)
        out.append(check_mini_guard(read_guard_heartbeat(guard_key), now_epoch))

    alertwatch = alertwatch_status(now_epoch)
    if alertwatch is not None:
        out.append(alertwatch)

    try:
        configured = OUTSIDE_ALARM_SECRET.stat().st_size > 0
    except OSError:
        configured = False
    text, why = read_outside_alarm(OUTSIDE_ALARM_SECRET) if configured else (None, "")
    out.append(check_outside_alarm(text, now_epoch, configured=configured, problem=why))

    metric_statuses, new_metrics = collect_metrics(now_epoch, prior_metrics or {})
    out.extend(metric_statuses)
    return out, new_metrics


def shadow_logs(monitors: dict) -> list[tuple[str, str, str]]:
    """(label, text, the regex rule's level) for every readable log Jev shadows: the
    alerting CRON_LOGS plus the shadow-only extras in monitors.toml. The extras get
    the rule's verdict for comparison only; they never become a status."""
    extra = [(item.get("name"), Path(item.get("log", "")))
             for item in (monitors.get("jev_shadow") or {}).get("logs", [])]
    out, labels = [], set()
    for label, path in [*CRON_LOGS, *extra]:
        if not label or label in labels:   # shadow state is keyed by label: first source wins
            continue
        if not jev_shadow.in_scope(path):  # never send a log outside the agreed data scope
            continue
        text = _read(path)
        if text is not None:
            labels.add(label)
            out.append((label, text, cron_log_status(label, text).level))
    return out


def run_jev_shadow(now_epoch: int) -> None:
    """Shadow only: writes watchdog/logs/jev-shadow.jsonl and nothing reads it back.
    Off unless monitors.toml turns it on; WATCHDOG_JEV_SHADOW=0 is the kill switch."""
    try:
        monitors = _load_monitors()
        if not (monitors.get("jev_shadow") or {}).get("enabled"):
            return
        if os.environ.get("WATCHDOG_JEV_SHADOW") == "0":
            return
        key = jev_shadow.load_key()
        if not key:
            return
        log_dir = Path(os.environ.get("WATCHDOG_LOG_DIR", str(BASE / "watchdog" / "logs")))
        jev_shadow.shadow_pass(shadow_logs(monitors), now_epoch=now_epoch, key=key,
                               log_path=log_dir / "jev-shadow.jsonl",
                               state_path=log_dir / "jev-shadow-state.json")
    except Exception:  # noqa: BLE001 - a shadow must never break the poll it watches
        pass


def format_report(fired: list[CheckStatus]) -> str:
    """Render fired checks worst-first into the investigator's briefing text."""
    ordered = sorted(fired, key=lambda s: -LEVELS.get(s.level, 0))
    lines = []
    for s in ordered:
        lines.append(f"[{s.level.upper()}] {s.name}: {s.summary}")
        if s.evidence:
            for ev in s.evidence.splitlines():
                lines.append(f"    | {ev}")
    return "\n".join(lines)


def stage_delivery(path, fired, candidate_state: dict, now_epoch: int) -> str:
    """Persist a detected alert before its outward delivery is attempted."""
    import uuid
    attempt_id = uuid.uuid4().hex
    record = {
        "version": 1,
        "attempt_id": attempt_id,
        "detected_at": now_epoch,
        "fired": [{"name": s.name, "level": s.level, "summary": s.summary,
                   "evidence": s.evidence} for s in fired],
        "candidate_suppression_state": candidate_state,
        "delivery": {"status": "pending"},
    }
    save_state(path, record)
    return attempt_id


def record_delivery(pending_path, state_path, last_path, attempt_id: str,
                    status: str, provider_receipt=None) -> None:
    """Finish a staged delivery without confusing acceptance with human receipt."""
    if status not in {"accepted", "failed", "uncertain", "attempting"}:
        raise ValueError("invalid delivery status")
    pending = load_state(pending_path)
    if pending.get("attempt_id") != attempt_id:
        raise ValueError("watchdog delivery attempt does not match pending record")
    delivery = {"status": status}
    if status == "accepted":
        if (not isinstance(provider_receipt, dict)
                or provider_receipt.get("ok") is not True
                or provider_receipt.get("status") != "accepted"
                or not isinstance(provider_receipt.get("message_ids"), list)
                or not provider_receipt["message_ids"]
                or any(not isinstance(value, int) or isinstance(value, bool)
                       for value in provider_receipt["message_ids"])):
            raise ValueError("accepted delivery requires a provider receipt")
        delivery["provider_receipt"] = provider_receipt
    pending["delivery"] = delivery
    if status == "accepted":
        save_state(pending_path, pending)
        save_state(last_path, pending)
        save_state(state_path, pending["candidate_suppression_state"])
        Path(pending_path).unlink()
    else:
        save_state(pending_path, pending)

def _same_fired(pending: dict, fired) -> bool:
    old = [(item.get("name"), item.get("level")) for item in pending.get("fired", [])]
    new = [(item.name, item.level) for item in fired]
    return old == new


def main(argv=None) -> int:
    argv = list(argv or [])
    state_path = Path(os.environ.get("WATCHDOG_STATE", str(BASE / "watchdog" / "state.json")))
    pending_path = Path(os.environ.get("WATCHDOG_PENDING", str(BASE / "watchdog" / "delivery-pending.json")))
    last_path = Path(os.environ.get("WATCHDOG_DELIVERY_LAST", str(BASE / "watchdog" / "delivery-last.json")))
    metrics_path = Path(os.environ.get("WATCHDOG_METRICS", str(BASE / "watchdog" / "metrics-history.json")))

    if argv and argv[0] == "--record-delivery":
        if len(argv) not in (3, 4):
            print("usage: --record-delivery accepted|failed|uncertain ATTEMPT_ID [RECEIPT_JSON]", file=sys.stderr)
            return 2
        receipt = json.loads(argv[3]) if len(argv) == 4 else None
        record_delivery(pending_path, state_path, last_path, argv[2], argv[1], receipt)
        return 0

    dry_run = "--dry-run" in argv
    pending = load_state(pending_path)
    if not dry_run and pending.get("delivery", {}).get("status") == "accepted":
        # Complete interrupted local persistence from an already accepted receipt; never resend.
        record_delivery(pending_path, state_path, last_path, pending['attempt_id'], 'accepted',
                        pending['delivery'].get('provider_receipt'))
    now = int(time.time())
    prior_metrics = load_state(metrics_path)
    statuses, new_metrics = collect(now, prior_metrics)
    if not dry_run: save_state(metrics_path, new_metrics)
    result = triage(statuses, load_state(state_path), now)
    fired = result["fired"]
    attempt_id = None
    delivery_uncertain = False
    if result["escalate"]:
        pending = load_state(pending_path)
        if pending.get("delivery", {}).get("status") in ("attempting", "uncertain") and _same_fired(pending, fired):
            delivery_uncertain = True
        elif not dry_run:
            attempt_id = stage_delivery(pending_path, fired, result["state"], now)
    elif not dry_run:
        save_state(state_path, result["state"])
        if pending_path.exists() and all(s.level == "ok" for s in statuses):
            resolved = load_state(pending_path)
            resolved["resolved_at"] = now
            save_state(last_path, resolved)
            pending_path.unlink()

    if fired:
        print(format_report(fired))
    else:
        print("all checks ok" if all(s.level == "ok" for s in statuses)
              else "issues present but suppressed (within cooldown)")
    if delivery_uncertain:
        print("watchdog alert delivery remains uncertain; inspect the pending record before retry")
    if new_metrics:
        readings = " ".join(f"{k}={v.get('value')}" for k, v in sorted(new_metrics.items()))
        print("WATCHDOG_METRICS:" + readings)

    payload = {
        "escalate": result["escalate"] and not delivery_uncertain,
        "attempt_id": attempt_id,
        "delivery_uncertain": delivery_uncertain,
        "fired": [{"name": s.name, "level": s.level, "summary": s.summary,
                   "evidence": s.evidence} for s in fired],
        "checked": len(statuses),
    }
    print("WATCHDOG_JSON:" + json.dumps(payload))
    if not dry_run:
        run_jev_shadow(now)   # after the result is emitted: it cannot change it
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
