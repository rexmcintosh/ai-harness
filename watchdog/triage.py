"""Triage core: pure functions over already-collected signals.

The boundary is deliberate. These functions take *raw text* (a log file's
contents, `df` output, `systemctl is-active` output) and return a normalized
``CheckStatus``. All real I/O — reading files, shelling out — lives in
``run-watchdog.sh``. That keeps the decision logic unit-testable with sample
data and free of the environment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
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


def check_cron_log(name: str, log_text: str, *, tail_lines: int = 50) -> CheckStatus:
    """Scan the tail of a cron log for error markers. Tail-only so an old, since-
    resolved error doesn't fire forever."""
    tail = log_text.splitlines()[-tail_lines:]
    hits = [ln for ln in tail if _ERROR_MARKERS.search(ln)]
    if hits:
        return CheckStatus(f"cron:{name}", "warn",
                           f"{len(hits)} error marker(s) in recent {name} log",
                           evidence="\n".join(hits[-5:]))
    return CheckStatus(f"cron:{name}", "ok", f"{name} log clean")


def _iso_epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


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


def check_meet_freshness(rows, now_epoch, *,
                         stale_warn_min: int = 20, stale_crit_min: int = 75,
                         launch_overdue_min: int = 30,
                         coverage_gap_warn: int = 3, coverage_gap_pct: int = 15,
                         coverage_max_age_min: int | None = None,
                         racing_start: int = 8, racing_end: int = 22) -> CheckStatus:
    """The ABSENCE alert MeetTrack never had: every other check fires on 'too
    much'; a dead poller, a wedged writer, and an idle Saturday all used to
    look identical to healthy. `rows` is a recent slice of meet_registry.

    - crit  — a meet went 'failed' in the last 24h (its recovery gave up; data
              is lost until a human intervenes), any hour of the day.
    - during racing hours (Europe/Lisbon — the registry is POR-scoped):
      warn/crit — a live-by-date meet with a writer ('polling'/'backfilling')
              whose last_ingest_at (falling back to the status-change time,
              covering the never-ingested case) is older than the floor.
              A long lunch break can trip this; one 6h-cooldown ping during a
              national championship beats silence — tune from rehearsals.
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
              dispatched it within one 5-minute tick).
    """
    try:
        from zoneinfo import ZoneInfo
        local = datetime.fromtimestamp(now_epoch, tz=ZoneInfo("Europe/Lisbon"))
    except Exception:
        local = datetime.fromtimestamp(now_epoch, tz=timezone.utc)
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
        start, end = r.get("start_date"), r.get("end_date")
        live = bool(start) and start <= today and (not end or today <= end)
        if not live:
            continue
        ref = _iso_epoch(r.get("last_ingest_at")) or _iso_epoch(r.get("updated_at"))
        age_min = (now_epoch - ref) / 60 if ref is not None else None
        if status in ("polling", "backfilling"):
            if age_min is not None and age_min >= stale_warn_min:
                stale.append(f"{r.get('sr_meet_id')} {name} quiet {age_min:.0f}m")
                worst_stale_min = max(worst_stale_min, age_min)
            # Only a meet with a writer can have reported coverage at all.
            gap = _coverage_complaint(r, name, coverage_gap_warn, coverage_gap_pct,
                                      now_epoch, coverage_max_age_min)
            if gap:
                blind.append(gap)
        elif status in ("discovered", "queued"):
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

def triage(statuses, prior_state, now_epoch, *, cooldown_hours: int = 6) -> dict:
    """Decide what to escalate.

    A non-ok status *fires* (escalates) when it is new, when it has worsened
    since the last alert, or when the cooldown window has elapsed since the last
    alert. Repeats at the same level within the window are suppressed (flap
    control). Recovered checks (now ok) are dropped from state.

    Returns ``{"escalate": bool, "fired": [CheckStatus], "state": new_state}``.
    """
    cooldown = cooldown_hours * 3600
    fired = []
    new_state = {}
    for s in statuses:
        if s.level == "ok":
            continue  # recovered or healthy -> not carried in state
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
