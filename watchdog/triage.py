"""Triage core: pure functions over already-collected signals.

The boundary is deliberate. These functions take *raw text* (a log file's
contents, `df` output, `systemctl is-active` output) and return a normalized
``CheckStatus``. All real I/O — reading files, shelling out — lives in
``run-watchdog.sh``. That keeps the decision logic unit-testable with sample
data and free of the environment.
"""
from __future__ import annotations

import json
import math
import re
import sys

from jev.redact import strip_json_data_lists
from dataclasses import dataclass, field
import json
from datetime import datetime, timezone

# Ordered so we can compare severities numerically (worsening detection).
LEVELS = {"ok": 0, "warn": 1, "crit": 2}


@dataclass
class CheckStatus:
    name: str
    level: str  # ok | warn | crit
    summary: str
    evidence: str = ""


# --- individual checks ------------------------------------------------------

_BEBOP_LINE = re.compile(r"^\[(?P<ts>[^\]]+)\].*\brc=(?P<rc>-?\d+)\b")


def check_bebop_runs(log_text: str, now_epoch: int, *, max_gap_hours: int = 14) -> CheckStatus:
    """Health of the bebop briefing from its runs.log.

    crit  — the most recent run failed (rc != 0).
    warn  — no run logged, or the last success is older than ``max_gap_hours``
            (a scheduled briefing was missed).
    ok    — the last run succeeded recently.
    """
    last = None
    for line in log_text.splitlines():
        m = _BEBOP_LINE.match(line.strip())
        if m:
            last = m
    if last is None:
        return CheckStatus("bebop", "warn", "no bebop runs logged yet")
    rc = int(last.group("rc"))
    if rc != 0:
        return CheckStatus("bebop", "crit", f"last bebop run failed (rc={rc})",
                           evidence=last.group(0))
    try:
        ts = datetime.fromisoformat(last.group("ts"))
        age_h = (now_epoch - ts.timestamp()) / 3600
    except ValueError:
        return CheckStatus("bebop", "warn", "last bebop run has an unparseable timestamp",
                           evidence=last.group("ts"))
    if age_h > max_gap_hours:
        return CheckStatus("bebop", "warn",
                           f"no bebop run in {age_h:.0f}h (a briefing may have been missed)")
    return CheckStatus("bebop", "ok", f"last bebop run ok, {age_h:.0f}h ago")


_DISK_PCT = re.compile(r"(\d+)%")


def check_disk(df_output: str, *, threshold: int = 85) -> CheckStatus:
    """Root-filesystem usage from `df -P /`. >=95% crit, >=threshold warn."""
    pct = None
    for line in df_output.splitlines():
        m = _DISK_PCT.search(line)
        if m:
            pct = int(m.group(1))  # last data line wins; header has no %
    if pct is None:
        return CheckStatus("disk", "warn", "could not parse df output", evidence=df_output[:200])
    if pct >= 95:
        return CheckStatus("disk", "crit", f"root filesystem {pct}% full")
    if pct >= threshold:
        return CheckStatus("disk", "warn", f"root filesystem {pct}% full")
    return CheckStatus("disk", "ok", f"root filesystem {pct}% full")


def check_backup(last_success_text: str | None, now_epoch: float, *,
                 warn_after_h: float = 30, crit_after_h: float = 54) -> CheckStatus:
    """The off-site backup's last verified run, from restic-offsite's last-success.json.
    The backup runs daily and checks itself with a test restore, but it has no alert of
    its own: on 2026-11-27 a restart killed the catch-up run and nothing said so.
    Older than warn_after_h warns (one missed day plus the 2-hour run), older than
    crit_after_h is crit."""
    if last_success_text is None:
        return CheckStatus("backup", "warn", "no verified off-site backup on record")
    try:
        data = json.loads(last_success_text)
        when = data.get("verified_at") or ""
        epoch = datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return CheckStatus("backup", "warn", "off-site backup record unreadable",
                           evidence=(last_success_text or "")[:200])
    if data.get("verified") is not True:
        return CheckStatus("backup", "warn", "the last off-site backup was not verified")
    age_h = (now_epoch - epoch) / 3600
    if age_h >= crit_after_h:
        return CheckStatus("backup", "crit", f"no verified off-site backup for {age_h:.0f} h")
    if age_h >= warn_after_h:
        return CheckStatus("backup", "warn", f"no verified off-site backup for {age_h:.0f} h")
    return CheckStatus("backup", "ok", f"off-site backup verified {age_h:.0f} h ago")


def check_mini_guard(heartbeat_text: str | None, now_epoch: float, *, stale_after_s: int = 600) -> CheckStatus:
    """The Mac mini guard watches this VM from outside; this check watches the guard.
    heartbeat_text is its last-tick.json read over SSH, or None when the mini did not
    answer. The two cases get different messages: an unreachable mini is not the same
    as a guard that stopped ticking."""
    if heartbeat_text is None:
        return CheckStatus("mini-guard", "warn", "cannot reach the Mac mini to read its guard's heartbeat")
    try:
        last = float(json.loads(heartbeat_text).get("time"))
    except (ValueError, TypeError, AttributeError):
        return CheckStatus("mini-guard", "warn", "the Mac mini guard's heartbeat is unreadable",
                           evidence=(heartbeat_text or "")[:200])
    age = now_epoch - last
    if age > stale_after_s:
        return CheckStatus("mini-guard", "crit", f"the Mac mini guard has not run for {int(age // 60)} min")
    return CheckStatus("mini-guard", "ok", f"Mac mini guard ran {int(age)} s ago")


def check_service_active(name: str, systemctl_output: str) -> CheckStatus:
    """`systemctl is-active <unit>` — 'active' is ok, anything else is crit."""
    state = systemctl_output.strip()
    if state == "active":
        return CheckStatus(f"svc:{name}", "ok", f"{name} is active")
    return CheckStatus(f"svc:{name}", "crit", f"service {name} is {state or 'unknown'}")


# `ps -eo pid,ppid,etime,args` row: pid, ppid, [[dd-]hh:]mm:ss, then the command.
_PS_ROW = re.compile(r"^\s*(?P<pid>\d+)\s+(?P<ppid>\d+)\s+(?P<etime>[\d:-]+)\s+(?P<args>\S.*)$")


def _etime_minutes(raw: str) -> int | None:
    """`ps` ELAPSED ([[dd-]hh:]mm:ss) as whole minutes, or None if unparsable."""
    days, _, rest = raw.rpartition("-")
    try:
        parts = [int(p) for p in rest.split(":")]
        offset = int(days) * 1440 if days else 0
    except ValueError:
        return None
    if len(parts) == 3:
        hours, minutes, _seconds = parts
    elif len(parts) == 2:
        hours, (minutes, _seconds) = 0, parts
    else:
        return None
    return offset + hours * 60 + minutes


def _age(minutes: int) -> str:
    if minutes < 0:
        return "unknown"
    if minutes >= 1440:
        return f"{minutes // 1440}d"
    if minutes >= 60:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def check_orphan_processes(ps_output: str, *, min_hours: int = 6,
                           command: str = "codex") -> CheckStatus:
    """Leaked `codex` processes: reparented to init (PPID 1) and still running.

    The Codex CLI on PATH is a Node wrapper around a native binary. When the
    calling session dies, or a helper's timeout kills the wrapper, the native
    binary survives with PPID 1 and nobody is waiting for its answer. Two such
    reviews (47 and 18 days old) were found by hand on 2026-09-17.

    warn — one or more orphans at or past ``min_hours``.
    ok   — none, or ``ps`` produced nothing usable (never invent a failure).

    Report only. Killing a process is the operator's call, per the watchdog's
    no-remediation rule.
    """
    name = f"proc:{command}-orphans" if command != "codex" else "proc:orphans"
    found: list[tuple[int, str, str]] = []
    for line in ps_output.splitlines():
        row = _PS_ROW.match(line)
        if not row or row["ppid"] != "1":
            continue
        args = row["args"].strip()
        executable = args.split()[0].rsplit("/", 1)[-1]
        # `codex` and its helpers (`codex-code-mode`), but not unrelated
        # neighbours like `codexctl` or `codexd`.
        if executable != command and not executable.startswith(f"{command}-"):
            continue
        minutes = _etime_minutes(row["etime"])
        if minutes is not None and minutes < min_hours * 60:
            continue
        # An unreadable ELAPSED field is reported, not dropped: an orphan that
        # `ps` prints oddly would otherwise stay invisible forever.
        found.append((minutes if minutes is not None else -1, row["pid"], args))

    if not found:
        return CheckStatus(name, "ok", f"no orphaned {command} processes")

    found.sort(reverse=True)
    oldest = _age(found[0][0])
    return CheckStatus(
        name,
        "warn",
        f"{len(found)} orphaned {command} process(es), oldest {oldest}",
        evidence="\n".join(
            f"pid {pid} {_age(minutes)} {args[:80]}" for minutes, pid, args in found[:5]
        ),
    )


# Match error words, but NOT when they're a key in a key=value metric line
# (e.g. "failed=0", "error=0") — those are counters, not errors. The (?!=)
# lookahead excludes the `=` case while still matching "FAILED rc=1", "error:", etc.
_ERROR_MARKERS = re.compile(
    r"traceback|exception|\b(?:error|failed|critical)\b(?!=)", re.IGNORECASE)
# Same idea for JSON summaries (diem; loom until its 2026-10-09 retirement): `"failed": 0`, `"error": null`, `"errors": []`
# are empty counters. Blank them before matching; a non-empty value still fires.
_JSON_EMPTY_COUNTER = re.compile(
    r'"(?:errors?|failed|failures?|critical|exceptions?)"\s*:\s*(?:0|null|false|\[\]|\{\}|"")(?![\w.])',
    re.IGNORECASE)
# ...and JSON lists of per-item data (`"quarantined_items": [[id, reason], ...]`,
# `"articles": [names]`): standing item notes and file names, not this run's outcome.
# (strip_json_data_lists lives in jev.redact: the Jev shadow cuts the same lists before sending.)


def _outcome_text(line: str) -> str:
    return _JSON_EMPTY_COUNTER.sub("", strip_json_data_lists(line))


def _last_run_end(log_text: str, run_end):
    """The last match of `run_end` in the log, or None when it never matches."""
    last = None
    for last in run_end.finditer(log_text):
        pass
    return last


def _after_last_run_end(log_text: str, run_end) -> str | None:
    """The text after the last match of `run_end`, or None when it never matches."""
    last = _last_run_end(log_text, run_end)
    return None if last is None else log_text[last.end():]


def check_cron_log(name: str, log_text: str, *, tail_lines: int = 50,
                   run_end=None) -> CheckStatus:
    """Scan the tail of a cron log for error markers. Tail-only so an old, since-
    resolved error doesn't fire forever.

    `run_end` (a compiled regex searched in the whole text, or any object whose
    finditer(text) yields regex matches in order) is for a quiet log
    whose good runs each end with a known pattern and print nothing after it.
    Then only the text after its last match (all of it, with no match) counts,
    however long: a crash stops counting once a later run finishes, even while
    it is still in the last `tail_lines`. Any other line there also warns: a run
    that did not finish, or lines that no longer fit the pattern."""
    if run_end is None:
        tail = log_text.splitlines()[-tail_lines:]
    else:
        rest = _after_last_run_end(log_text, run_end)
        tail = (log_text if rest is None else rest).splitlines()
    hits = [ln for ln in tail if _ERROR_MARKERS.search(_outcome_text(ln))]
    unfinished = [ln for ln in tail if ln.strip()] if run_end is not None else []
    if unfinished and not hits:
        return CheckStatus(f"cron:{name}", "warn",
                           f"{len(unfinished)} line(s) in the {name} log after its last "
                           f"finished run: a run did not finish, or the lines that end "
                           f"a good run have changed",
                           evidence="\n".join(unfinished[-5:]))
    if hits:
        return CheckStatus(f"cron:{name}", "warn",
                           f"{len(hits)} error marker(s) in recent {name} log",
                           evidence="\n".join(hits[-5:]))
    return CheckStatus(f"cron:{name}", "ok", f"{name} log clean")


def check_run_age(status: CheckStatus, log_text: str, run_end, now_epoch: float, *,
                  max_age_hours: float) -> CheckStatus:
    """`status` (check_cron_log's verdict on this log, same `run_end`), unless the
    last finished run is more than `max_age_hours` old. A cron that stops running,
    or a run that hangs and holds its lock, writes nothing, so no log rule sees it.

    The finish time is the `end_ts` group of the last `run_end` match. Too old ->
    warn (a crit stays crit) under the same check name, so one outage is one alert stream: a run that
    keeps crashing is already a warn here, and its lines stay as the evidence.
    An unreadable time warns too, and so does one with no time zone (it would be read
    as this host's local time). A log with no finished run is left to `status`:
    check_cron_log warns on any line in it, and an empty log is not timed."""
    last = _last_run_end(log_text, run_end)
    if last is None:
        return status
    label = status.name.removeprefix("cron:")
    stamp = last.group("end_ts")
    end_line = last.group(0).splitlines()[-1]
    finished = _aware_iso_epoch(stamp)
    level = "crit" if status.level == "crit" else "warn"      # never lower a crit
    if finished is None:
        return CheckStatus(status.name, level,
                           f"cannot read the time of the last finished {label} run",
                           evidence="\n".join(filter(None, (end_line, status.evidence))))
    age_h = (now_epoch - finished) / 3600
    if age_h <= max_age_hours:
        return status
    return CheckStatus(status.name, level,
                       f"no {label} run has finished for {age_h:.1f}h (limit "
                       f"{max_age_hours:g}h; last at {stamp}): its cron may have stopped, "
                       f"a run may be stuck, or every run since has failed",
                       evidence=status.evidence or end_line)


def check_log_coverage(found: list[str], missing: list[str]) -> CheckStatus:
    """A missing log is skipped (the job may be paused or not installed here), but
    if NONE of the configured logs is readable the log check is blind: say so."""
    if missing and not found:
        return CheckStatus("cron-logs:coverage", "warn",
                           f"no configured cron log is readable (missing: {', '.join(missing)})")
    note = f"; missing: {', '.join(missing)}" if missing else ""
    return CheckStatus("cron-logs:coverage", "ok", f"{len(found)} cron log(s) read{note}")


def check_alertwatch(oldest_inbox_age_s: float | None, paused: bool, newest_run_status: str | None,
                     *, max_age_s: int = 1200, note_status: str | None = None,
                     paused_age_s: float | None = None, max_pause_s: int = 6 * 3600) -> CheckStatus:
    """alert-watch turns captured Telegram reports into fix runs. Warn when its inbox
    stops moving or its newest run failed. A short pause is a choice, not a fault (it
    sends its own note when it pauses itself), but a pause that outlasts max_pause_s
    warns: on 2026-11-21 it paused itself and stayed off for 6 days unnoticed.
    A usage-limit run is not a failure."""
    if note_status in ("failed", "uncertain"):  # first: the pause note itself may be the one lost
        return CheckStatus("alertwatch", "warn",
                           f"alert-watch: a note to Rex {note_status}; see its runs/ record and state.json"
                           + (" (alert-watch is paused)" if paused else ""))
    if paused:
        if paused_age_s is not None and paused_age_s > max_pause_s:
            return CheckStatus("alertwatch", "warn",
                               f"alert-watch has been paused for {int(paused_age_s // 3600)} h; "
                               "delete ~/.local/state/alert-watch/PAUSED to resume")
        return CheckStatus("alertwatch", "ok", "alert-watch is paused")
    if newest_run_status == "failed":
        return CheckStatus("alertwatch", "warn", "alert-watch: the newest run failed; see its runs/ record")
    if oldest_inbox_age_s is not None and oldest_inbox_age_s > max_age_s:
        return CheckStatus("alertwatch", "warn",
                           f"alert-watch: a report has waited {int(oldest_inbox_age_s // 60)} min in the inbox")
    return CheckStatus("alertwatch", "ok", "alert-watch inbox moving")


def check_outside_alarm(status_text: str | None, now_epoch: float, *, configured: bool = True,
                        stale_after_s: int = 20 * 60,
                        expected: tuple = ("bebop-vm", "mini-guard")) -> CheckStatus:
    """The bebop-uptime Worker on Cloudflare is the alarm for "the whole house is down".
    Nothing tells Rex if it stops or cannot send, so the VM reads its status: no recent
    cron pass is crit; missing setup, unreachable, unreadable, no Telegram token, a failed
    send, or a check it reports as down is warn."""
    name = "outside-alarm"
    if not configured:
        return CheckStatus(name, "warn", "outside alarm: the server has no ~/.config/bebop-uptime/secret, "
                           "so it cannot check the alarm (see setup/uptime-worker)")
    if status_text is None:
        return CheckStatus(name, "warn", "outside alarm (Cloudflare bebop-uptime) could not be reached from the server")
    try:
        d = json.loads(status_text)
        if not isinstance(d, dict):
            raise TypeError
        checks = d.get("checks") or {}
        if not isinstance(checks, dict):
            raise TypeError
        last_cron = float(d.get("lastCron") or 0)
        if not math.isfinite(last_cron):
            raise ValueError
    except (ValueError, TypeError):
        return CheckStatus(name, "warn", "outside alarm (Cloudflare bebop-uptime) gave an unreadable status")
    age = max(0.0, now_epoch - last_cron)   # a clock ahead of ours is not "stale"
    if not last_cron or age > stale_after_s:
        when = "never" if not last_cron else f"{int(age // 60)} min ago"
        return CheckStatus(name, "crit", f"outside alarm (Cloudflare bebop-uptime) last ran {when}; "
                           "a house power or internet outage would go unreported")
    if not d.get("alerts"):
        return CheckStatus(name, "warn", "outside alarm has no Telegram token, so it cannot message Rex "
                           "(see setup/uptime-worker/README.md)")
    if d.get("lastSendError"):
        return CheckStatus(name, "warn", "outside alarm could not deliver a Telegram message; it retries every 5 min")
    missing = [n for n in expected if not isinstance(checks.get(n), dict)]
    if missing:   # the Worker ignores a check until its first ping, so say it here
        return CheckStatus(name, "warn", "outside alarm has never heard from: " + ", ".join(missing))
    down = sorted(n for n, c in checks.items() if isinstance(c, dict) and c.get("announced") == "down")
    if down:
        return CheckStatus(name, "warn", "outside alarm reports no check-in from: " + ", ".join(down))
    return CheckStatus(name, "ok", f"outside alarm ran {int(age // 60)} min ago")


def _iso_epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _aware_iso_epoch(ts: str | None) -> float | None:
    """Like _iso_epoch, but a time with no zone offset is unreadable (None)."""
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
    except ValueError:
        return None
    return parsed.timestamp() if parsed is not None and parsed.tzinfo is not None else None


# One published event without results is the normal in-flight state: a
# ResultList appears and the next tick parses it. The share rule therefore needs
# two before it can fire; the flat rule is what catches the 3-of-40 shape, which
# is under any sane percentage and is exactly what the heartbeat cannot see.
_COVERAGE_SHARE_MIN_GAP = 2


def _coverage_complaint(row, label, gap_warn, gap_pct, now_epoch, max_age_min):
    """'this live meet is writing, but blind' — or None.

    Silent on NULL counters by design: only the PDF writer can list a meet's
    published events, so the fragment and Lenex writers leave those two NULL,
    and a gap that was never measured must never alert.

    Silent too on counters older than `max_age_min` (or with no coverage_at at
    all). The counters are a SNAPSHOT, not a running total: they sit on the row
    until the next tick overwrites them, so last weekend's 37-of-40 would
    otherwise raise a "missing events" warning during this weekend's racing
    hours, about a meet that is already over.
    """
    measured = _iso_epoch(row.get("coverage_at"))
    if measured is None or (now_epoch - measured) / 60 > max_age_min:
        return None
    errors = row.get("last_tick_errors")
    if errors:
        return f"{row.get('sr_meet_id')} {label} {errors} event/row error(s) last tick"
    published, covered = row.get("events_published"), row.get("events_with_results")
    if published is None or covered is None:
        return None
    gap = published - covered
    if gap <= 0:
        return None
    if gap >= gap_warn or (gap >= _COVERAGE_SHARE_MIN_GAP
                           and gap * 100 >= gap_pct * published):
        return f"{row.get('sr_meet_id')} {label} {covered}/{published} events have results"
    return None


def _lisbon_zone():
    """(ZoneInfo('Europe/Lisbon'), None), or (None, why it failed)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Lisbon"), None
    except Exception as e:      # no tzdata, broken install, ...
        return None, f"{type(e).__name__}: {e}"


# Fallback reasons already logged by this process (one watchdog run), so the
# freshness and liveness rules together write one stderr line, not two.
_lisbon_logged: set = set()


def _lisbon(now_epoch):
    """Wall-clock time in Europe/Lisbon (the registry is POR-scoped).

    Without the zone the meet rules still run, on UTC (racing hours shift by
    one hour in summer), and say so: one line on stderr per run (the wrapper
    keeps it in runs.log.err) here, and a 'meets.clock' alert from
    check_meet_clock."""
    zone, err = _lisbon_zone()
    if zone is None:
        if err not in _lisbon_logged:
            _lisbon_logged.add(err)
            print(f"watchdog: Europe/Lisbon unavailable ({err}); "
                  f"meet checks use UTC", file=sys.stderr)
        return datetime.fromtimestamp(now_epoch, tz=timezone.utc)
    return datetime.fromtimestamp(now_epoch, tz=zone)


def check_meet_clock() -> CheckStatus:
    """warn when Europe/Lisbon cannot be resolved: every meet rule then reads
    racing hours and 'today' in UTC instead, which must not be silent."""
    zone, err = _lisbon_zone()
    if zone is None:
        return CheckStatus("meets.clock", "warn",
                           "Europe/Lisbon timezone unavailable: meet checks "
                           "read racing hours and dates as UTC",
                           evidence=err or "")
    return CheckStatus("meets.clock", "ok", "Europe/Lisbon resolved")


# A coverage_at further ahead of the watchdog's clock than this is not a
# fresh tick: it is a clock or data fault, and taking it as fresh would hide a
# dead writer until the bad stamp falls into the past. Small skew is normal.
FUTURE_SKEW_MIN = 5


def _future_min(measured, now_epoch):
    """Minutes `measured` lies ahead of now, if beyond FUTURE_SKEW_MIN; else None."""
    ahead = (measured - now_epoch) / 60
    return ahead if ahead > FUTURE_SKEW_MIN else None


def _live_today(row, today):
    start, end = row.get("start_date"), row.get("end_date")
    return bool(start) and start <= today and (not end or today <= end)


def _results_outstanding(row, now_epoch, max_age_min):
    """Does this meet have a published event still without results?

    True when the writer's counters say so, and ALSO when they cannot say:
    NULL counters (the Lenex and fragment writers do not count published
    events) or counters older than `max_age_min` (a snapshot from a writer that
    has since stopped is not evidence that nothing is pending). Only a fresh
    reading of "every published event has results" is False: that is a lunch
    break, an evening, or the afternoon after the last session, and silence
    then is normal (sr-10976: 228, 272 and 772 quiet minutes, nothing pending).
    """
    published, covered = row.get("events_published"), row.get("events_with_results")
    if published is None or covered is None:
        return True
    measured = _iso_epoch(row.get("coverage_at"))
    if measured is None or (now_epoch - measured) / 60 > max_age_min:
        return True
    if _future_min(measured, now_epoch) is not None:
        return True             # a stamp from the future is not a fresh reading
    return published > covered


def _results_outstanding_unmeasured(row, now_epoch, max_age_min):
    """True when the coverage counters are missing, stale or from the future,
    so they say nothing about this tick."""
    measured = _iso_epoch(row.get("coverage_at"))
    if measured is None or (now_epoch - measured) / 60 > max_age_min:
        return True
    return _future_min(measured, now_epoch) is not None


def check_meet_liveness(rows, now_epoch, *, tick_warn_min: int = 10,
                        tick_crit_min: int = 30, racing_start: int = 8,
                        racing_end: int = 22) -> CheckStatus:
    """'Is the writer still running?', asked of the tick, not of the results.

    The PDF writer stamps coverage_at on EVERY completed tick, results or none
    (about every 65 s; every ~4 min even while the database times out), so its
    age says whether the writer is alive. last_ingest_at moves only when a
    result changes, which is why it cannot tell a lunch break from a dead
    writer. On 4 Oct 2026 this rule would have gone crit 30 minutes into the
    Supabase outage, which the freshness rule could not see because it was
    already in crit for the quiet afternoon.

    Only rows whose writer measures per tick count: 'polling', live by date,
    with events_published and coverage_at set. 'backfilling' is left out on
    purpose: it is the reconcile one-shot (reconcile_meet.py), which the
    supervisor dispatches only once a meet is past its end_date, so it is
    never live by date, and it does not tick. The Lenex and fragment writers
    stamp coverage_at only when results change, so for them the freshness rule
    stays the floor. Racing hours only (Europe/Lisbon), like the other meet
    rules. A coverage_at more than FUTURE_SKEW_MIN minutes ahead of now is a
    clock or data fault, not a fresh tick: it warns (crit if a stopped writer
    is also listed), since it would otherwise hide a dead writer.
    """
    local = _lisbon(now_epoch)
    if not racing_start <= local.hour < racing_end:
        return CheckStatus("meets.liveness", "ok", "outside racing hours")
    today = local.date().isoformat()
    late, future = [], []
    for r in rows:
        if r.get("ingest_status") != "polling" or not _live_today(r, today):
            continue
        if r.get("events_published") is None:
            continue
        measured = _iso_epoch(r.get("coverage_at"))
        if measured is None:
            continue
        name = r.get("name") or r.get("sr_meet_id")
        ahead = _future_min(measured, now_epoch)
        if ahead is not None:
            future.append((ahead, f"{r.get('sr_meet_id')} {name} last tick "
                                  f"stamped {int(ahead)}m in the future"))
            continue
        age_min = (now_epoch - measured) / 60
        if age_min >= tick_warn_min:
            # Whole minutes, floored: never "30m" while still under a 30 crit.
            late.append((age_min, f"{r.get('sr_meet_id')} {name} "
                                  f"last tick {int(age_min)}m ago"))
    if not late and not future:
        return CheckStatus("meets.liveness", "ok", "live meet writers ticking (or none live)")
    late.sort(reverse=True)                 # worst first, so truncation keeps it
    future.sort(reverse=True)
    level = "crit" if late and late[0][0] >= tick_crit_min else "warn"
    parts = []
    if late:
        parts.append(f"{len(late)} live meet writer(s) stopped ticking")
    if future:
        parts.append(f"{len(future)} with a tick stamp in the future "
                     f"(clock or data fault)")
    lines = [line for _age, line in late] + [line for _a, line in future]
    return CheckStatus("meets.liveness", level, "; ".join(parts),
                       evidence="\n".join(lines[:5]))


def check_meet_freshness(rows, now_epoch, *,
                         stale_warn_min: int = 30, stale_crit_min: int = 75,
                         launch_overdue_min: int = 30,
                         coverage_gap_warn: int = 3, coverage_gap_pct: int = 15,
                         coverage_max_age_min: int | None = None,
                         racing_start: int = 8, racing_end: int = 22,
                         morning_grace_until: int = 13) -> CheckStatus:
    """The ABSENCE alert MeetTrack never had: every other check fires on 'too
    much'; a dead poller, a wedged writer, and an idle Saturday all used to
    look identical to healthy. `rows` is a recent slice of meet_registry.

    - crit  — a meet went 'failed' in the last 24h (its recovery gave up; data
              is lost until a human intervenes), any hour of the day.
    - during racing hours (Europe/Lisbon — the registry is POR-scoped):
      warn/crit — a live-by-date meet with a writer ('polling'/'backfilling')
              whose last_ingest_at (falling back to the status-change time,
              covering the never-ingested case) is older than the floor,
              while a published event still has no results (see
              _results_outstanding: unmeasured or stale counters count as
              outstanding). Lunch, evenings and the afternoon after the last
              session have nothing outstanding and stay quiet; whether the
              writer itself is still running is check_meet_liveness's job.
      warn  — a live meet with a writer whose last tick reported errors, or
              whose published events outrun the ones holding results by
              `coverage_gap_warn` (or `coverage_gap_pct` of them). The
              heartbeat cannot see this: 37 of 40 events inserting on every
              tick looks exactly like a healthy meet. Counters older than
              `coverage_max_age_min` are ignored — unset, that bound IS
              `stale_warn_min`, because the counters are written by the very
              tick whose silence that threshold already measures, so one knob
              covers both unless an operator deliberately splits them.
      warn  — a live-by-date meet still 'discovered'/'queued' with no status
              movement for `launch_overdue_min` (the supervisor should have
              dispatched it within one 5-minute tick). A meet whose probe
              found no live feed (feed_type 'none') has nothing to launch and
              is skipped: Castro Daire, 10 Oct 2026, raised two false WARNs.
    Morning grace: before `morning_grace_until` (local hour), a meet with no
    result yet TODAY has not started racing; it is not "gone quiet". Many
    Portuguese meets race only in the afternoon (Benedita and Silves, 10 Oct
    2026, started about 15:00-15:30). Whether racing really started is the
    expect monitor's first-results check (watchdog/expectations.py).
    """
    local = _lisbon(now_epoch)
    today = local.date().isoformat()
    racing = racing_start <= local.hour < racing_end
    if coverage_max_age_min is None:
        coverage_max_age_min = stale_warn_min

    failed, stale, blind, unlaunched = [], [], [], []
    worst_stale_min = 0.0
    for r in rows:
        status = r.get("ingest_status")
        name = r.get("name") or r.get("sr_meet_id")
        if status == "failed":
            upd = _iso_epoch(r.get("updated_at"))
            if upd is not None and (now_epoch - upd) < 24 * 3600:
                failed.append(f"{r.get('sr_meet_id')} {name}")
            continue
        if not racing:
            continue
        if not _live_today(r, today):
            continue
        ref = _iso_epoch(r.get("last_ingest_at")) or _iso_epoch(r.get("updated_at"))
        age_min = (now_epoch - ref) / 60 if ref is not None else None
        if status in ("polling", "backfilling"):
            last = _iso_epoch(r.get("last_ingest_at"))
            started_today = (last is not None and
                             _lisbon(last).date().isoformat() == today)
            # A measured gap (published events without results) is never
            # graced: that is a parser or writer fault, whatever the hour.
            pub, cov = r.get("events_published"), r.get("events_with_results")
            measured_gap = (pub is not None and cov is not None and pub > cov
                            and not _results_outstanding_unmeasured(r, now_epoch,
                                                                    coverage_max_age_min))
            pre_race = (not started_today and local.hour < morning_grace_until
                        and not measured_gap)
            if (not pre_race and age_min is not None and age_min >= stale_warn_min
                    and _results_outstanding(r, now_epoch, coverage_max_age_min)):
                stale.append(f"{r.get('sr_meet_id')} {name} quiet {age_min:.0f}m")
                worst_stale_min = max(worst_stale_min, age_min)
            # Only a meet with a writer can have reported coverage at all.
            gap = _coverage_complaint(r, name, coverage_gap_warn, coverage_gap_pct,
                                      now_epoch, coverage_max_age_min)
            if gap:
                blind.append(gap)
        elif status in ("discovered", "queued") and r.get("feed_type") != "none":
            if age_min is not None and age_min >= launch_overdue_min:
                unlaunched.append(f"{r.get('sr_meet_id')} {name} unlaunched {age_min:.0f}m")

    if failed:
        return CheckStatus("meets.freshness", "crit",
                           f"{len(failed)} meet(s) FAILED in the last 24h",
                           evidence="\n".join(failed[:5]))
    if stale:
        level = "crit" if worst_stale_min >= stale_crit_min else "warn"
        return CheckStatus("meets.freshness", level,
                           f"{len(stale)} live meet(s) gone quiet during racing hours",
                           evidence="\n".join(stale[:5]))
    if blind:
        # A writer that stopped (above) is worse news than one still writing;
        # both beat a meet that never launched, because this one is losing
        # events while the pool is racing.
        return CheckStatus("meets.freshness", "warn",
                           f"{len(blind)} live meet(s) missing events during racing hours",
                           evidence="\n".join(blind[:5]))
    if unlaunched:
        return CheckStatus("meets.freshness", "warn",
                           f"{len(unlaunched)} live meet(s) awaiting launch too long",
                           evidence="\n".join(unlaunched[:5]))
    return CheckStatus("meets.freshness", "ok", "live meets fresh (or none live)")


# --- triage (escalation + flap suppression) ---------------------------------

def triage(statuses, prior_state, now_epoch, *, cooldown_hours: int = 6,
           fast_prefixes: tuple = ("meets.",), fast_cooldown_hours: float = 1) -> dict:
    """Decide what to escalate.

    A non-ok status *fires* (escalates) when it is new, when it has worsened
    since the last alert, or when the cooldown window has elapsed since the last
    alert. Repeats at the same level within the window are suppressed (flap
    control). Recovered checks (now ok) are dropped from state.

    Live-meet checks (``fast_prefixes``) repeat after ``fast_cooldown_hours``:
    a problem during racing must not hide behind a 6-hour mute, as the 10 Oct
    2026 database outage did.

    Returns ``{"escalate": bool, "fired": [CheckStatus], "state": new_state}``.
    """
    fired = []
    new_state = {}
    for s in statuses:
        if s.level == "ok":
            continue  # recovered or healthy -> not carried in state
        hours = fast_cooldown_hours if s.name.startswith(fast_prefixes) else cooldown_hours
        cooldown = hours * 3600
        prior = prior_state.get(s.name)
        if prior is None:
            should_fire = True
        elif LEVELS[s.level] > LEVELS.get(prior["level"], 0):
            should_fire = True  # worsened -> fire immediately
        else:
            should_fire = (now_epoch - prior["ts"]) >= cooldown
        if should_fire:
            fired.append(s)
            new_state[s.name] = {"level": s.level, "ts": now_epoch}
        else:
            # suppressed: keep the original alert time so cooldown is measured
            # from when we actually told the human, not from each poll.
            new_state[s.name] = {"level": s.level, "ts": prior["ts"]}
    return {"escalate": bool(fired), "fired": fired, "state": new_state}
