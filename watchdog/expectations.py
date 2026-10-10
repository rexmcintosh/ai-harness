"""MeetTrack "expected but missing" model: pure functions, no I/O.

Every run turns one read of the world (a ``Snapshot``) into a list of
``Expectation`` records. Each record says what should have happened, by when,
and what we actually see. The alert policy (``expect_alerts.py``) decides who
hears about it; the runner (``expect_run.py``) does the reading and sending.

Design notes live in docs/meettrack-expectations.md. In short:

- ``db:reachable``     the database answers a tiny query (2 failed runs = missed)
- ``db:registry``      the meet registry can be read at all
- ``startlist:<id>``   a requested Portugal meet has its start list by T-24h
- ``writer:<id>:<day>`` the live writer for a meet racing today runs and ticks
- ``first:<id>:<day>``  results start appearing once racing should have begun
- ``results:<id>:<day>`` every event the host has published is in MeetTrack,
                        with roughly as many results as start-list swimmers,
                        within N minutes
- ``schedule:<id>:<day>`` events past their scheduled slot have results (only
                        when the start list carries times)
- ``source:<id>:<day>``, ``feed:<id>``, ``hygiene:stale-active`` notes only:
                        never paged, shown in dry-runs and investigations

``memory`` is the evaluator's own small state between runs (consecutive
database failures, when a gap was first seen, today's baseline). It is a plain
dict so the runner can persist it as JSON.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone

# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

DEFAULTS = {
    "nations": ["POR"],
    "timezones": {"POR": "Europe/Lisbon"},
    "racing_start": "08:00",          # local time; alerts for live meets only inside
    "racing_end": "22:00",
    "db_fail_runs": 2,
    "startlist_scope": "requested",   # "requested" (someone asked in the app) or "all"
    "startlist_lead_hours": 24,
    "startlist_request_grace_min": 60,
    "default_session_start": "09:00",  # first session when nothing better is known
    "first_results_by": "17:00",      # morning grace: unknown start -> results by then
    "first_results_grace_min": 60,    # known start -> results within this
    "launch_grace_min": 30,
    "tick_crit_min": 30,
    "coverage_max_age_min": 30,
    "results_lag_min": 20,
    "thin_pct": 50,
    "thin_min_entries": 4,
    "slot_lag_min": 45,
    "source_gap_min": 60,
    "stale_active_days": 2,
    "start_times": [],                # [{sr_meet_id, date, start}] stated session starts
    "nation_defaults": {},            # {"POR": {"first_results_by": "17:00", ...}}
}

LIVE_FEEDS = ("pdf", "fragment", "lenex")
WRITERS = ("track_pdf_meet", "poller.py", "lastheat_ingest", "reconcile_meet")


def valid_sid(sid) -> bool:
    """A swimrankings meet id is a short run of digits. Checked once, before an
    id from the database goes into a URL, a query, a file path or a ps match."""
    s = str(sid) if isinstance(sid, (str, int)) and not isinstance(sid, bool) else ""
    return s.isdigit() and 0 < len(s) <= 9


def writer_lines(ps_text: str, sid: str) -> list[str]:
    """`ps` lines of writer processes for this meet: every form the supervisor
    starts (splash_poller supervise_meets._poller_argv): the live URL
    .../<id>/ for the pdf, fragment and Lenex writers, and --sr-meet-id <id>
    for the reconcile one-shot. One matcher for the check and the investigation."""
    if not valid_sid(sid):
        return []
    rx = re.compile(rf"(?:live\.swimrankings\.net/{sid}/|--sr-meet-id[ =]{sid}\b|\bsr-{sid}\b)")
    return [ln.strip() for ln in (ps_text or "").splitlines()
            if any(w in ln for w in WRITERS) and rx.search(ln)]


def make_config(raw: dict | None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    for k, v in (raw or {}).items():
        cfg[k] = v
    return cfg


def nation_value(cfg: dict, nation: str | None, key: str):
    """A per-nation override (``[expect.nation_defaults.POR]``) or the global value."""
    per = (cfg.get("nation_defaults") or {}).get(nation or "", {}) or {}
    return per.get(key, cfg[key])


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------

@dataclass
class Expectation:
    id: str
    kind: str
    meet: str | None          # "Silves (sr-11113)" or None for system checks
    expected: str             # plain English: what should be true
    deadline: float | None    # epoch seconds; None = no clock (e.g. the DB check)
    status: str               # met | missed | pending | unknown | closed | n/a
    severity: str             # crit | warn | info (info is never paged)
    evidence: str             # plain English: what we see
    window: str = "racing"    # "racing" (live-meet hours) or "always"
    sr_meet_id: str | None = None
    day: str | None = None
    data: dict = field(default_factory=dict)   # facts for the investigation

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "id", "kind", "meet", "expected", "deadline", "status", "severity",
            "evidence", "window", "sr_meet_id", "day")}


@dataclass
class Snapshot:
    """One read of the world. Built by the runner; faked in tests."""
    now: float
    db_ok: bool
    db_latency_s: float | None = None
    db_error: str | None = None
    registry: list | None = None              # meet_registry rows; None = unreadable
    registry_error: str | None = None
    meets: dict = field(default_factory=dict)          # "11113" -> meets row
    events: dict = field(default_factory=dict)         # "11113" -> [event dicts]
    results_today: dict = field(default_factory=dict)  # "11113" -> int
    stale_active: list = field(default_factory=list)   # meets rows ACTIVE but over
    processes: dict | None = None                      # "11113" -> [ps lines]; None = unknown
    thin_last_write: dict = field(default_factory=dict)  # "11113" -> epoch of the newest
                                                         # result on its thin events
    read_errors: dict = field(default_factory=dict)      # "11113" -> ["events: HTTP 500"]


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------

def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:          # no tzdata: fall back to UTC (dry-run shows it)
        return timezone.utc


def meet_zone(cfg: dict, nation: str | None):
    return _zone((cfg.get("timezones") or {}).get(nation or "", "Europe/Lisbon"))


def local(now: float, tz) -> datetime:
    return datetime.fromtimestamp(now, tz=tz)


def at_local(day: str, hhmm: str, tz) -> float:
    h, m = (int(x) for x in hhmm.split(":"))
    d = date.fromisoformat(day)
    return datetime.combine(d, dtime(h, m), tzinfo=tz).timestamp()


def iso_epoch(ts) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def naive_local_epoch(ts, tz) -> float | None:
    """events.day_time is a naive local timestamp ('2026-03-28T15:00:00')."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.timestamp()


def hm(epoch: float | None, tz) -> str:
    """'17:19' local, or 'unknown'. Adds the date when it is not today's."""
    if epoch is None:
        return "unknown"
    return datetime.fromtimestamp(epoch, tz=tz).strftime("%H:%M")


def hm_day(epoch: float | None, tz, now: float) -> str:
    if epoch is None:
        return "never"
    dt = datetime.fromtimestamp(epoch, tz=tz)
    if dt.date() == local(now, tz).date():
        return dt.strftime("%H:%M")
    return dt.strftime("%d %b %H:%M")


def minutes(a: float, b: float) -> int:
    return int(max(0, a - b) // 60)


def in_racing_window(now: float, cfg: dict, tz=None) -> bool:
    tz = tz or _zone("Europe/Lisbon")
    t = local(now, tz).strftime("%H:%M")
    return cfg["racing_start"] <= t < cfg["racing_end"]


# ---------------------------------------------------------------------------
# meet helpers
# ---------------------------------------------------------------------------

def label(row: dict) -> str:
    sid = row.get("sr_meet_id")
    short = row.get("city") or row.get("name") or "meet"
    return f"{short} (sr-{sid})"


def live_on(row: dict, day: str) -> bool:
    start, end = row.get("start_date"), row.get("end_date") or row.get("start_date")
    return bool(start) and start <= day <= end


def stated_start(cfg: dict, sid: str, day: str) -> str | None:
    for item in cfg.get("start_times") or []:
        if str(item.get("sr_meet_id")) == str(sid) and item.get("date") == day:
            return item.get("start")
    return None


def scheduled_events_on(events: list, day: str, tz) -> list:
    """Events whose start-list slot (day_time) falls on `day`, with the slot epoch."""
    out = []
    for e in events or []:
        slot = naive_local_epoch(e.get("day_time"), tz)
        if slot is not None and local(slot, tz).date().isoformat() == day:
            out.append((slot, e))
    out.sort(key=lambda p: p[0])
    return out


def session_start(cfg: dict, row: dict, events: list, day: str, tz) -> tuple[float | None, str]:
    """Best known start of racing on `day`: a stated time, else the earliest
    scheduled event, else None. Returns (epoch, where it came from)."""
    sid = str(row.get("sr_meet_id"))
    stated = stated_start(cfg, sid, day)
    if stated:
        return at_local(day, stated, tz), "stated start time"
    sched = scheduled_events_on(events, day, tz)
    if sched:
        return sched[0][0], "start-list schedule"
    return None, ""


# ---------------------------------------------------------------------------
# evaluators
# ---------------------------------------------------------------------------

def eval_db(snap: Snapshot, cfg: dict, mem: dict) -> list[Expectation]:
    out = []
    expected = "the database answers a tiny query within a few seconds"
    if snap.db_ok:
        mem["db_failures"] = 0
        mem.pop("db_first_failure", None)
        lat = f"{snap.db_latency_s:.1f}s" if snap.db_latency_s is not None else "ok"
        out.append(Expectation("db:reachable", "db", None, expected, None, "met", "crit",
                               f"answered in {lat}", window="always"))
    else:
        fails = int(mem.get("db_failures", 0)) + 1
        mem["db_failures"] = fails
        first = mem.setdefault("db_first_failure", snap.now)
        need = int(cfg["db_fail_runs"])
        status = "missed" if fails >= need else "pending"
        tz = _zone("Europe/Lisbon")
        live = mem.get("last_live") or []
        blind = (f" Meet checks are blind until it answers; live meets at the last good read: "
                 f"{', '.join(live)}." if live else " Meet checks are blind until it answers.")
        out.append(Expectation(
            "db:reachable", "db", None, expected, first, status, "crit",
            f"{fails} check(s) in a row failed ({snap.db_error or 'no answer'}); first failure "
            f"at {hm(first, tz)} Lisbon.{blind}",
            window="always", data={"error": snap.db_error, "failures": fails}))
    if snap.db_ok and snap.registry is None:
        out.append(Expectation(
            "db:registry", "db", None, "the meet registry can be read", None, "missed", "crit",
            f"the database answers, but reading meet_registry failed "
            f"({snap.registry_error or 'unknown error'}). Meet checks are blind.",
            window="always", data={"error": snap.registry_error}))
    elif snap.db_ok:
        out.append(Expectation("db:registry", "db", None, "the meet registry can be read",
                               None, "met", "crit", "read ok", window="always"))
    return out


def eval_startlist(row: dict, snap: Snapshot, cfg: dict, tz) -> list[Expectation]:
    sid = str(row.get("sr_meet_id"))
    eid = f"startlist:{sid}"
    if row.get("ingest_status") in ("finished", "unsupported", "failed"):
        return []
    first_day = row.get("start_date")
    if not first_day:
        return []
    requested = iso_epoch(row.get("entries_requested_at"))
    scope = cfg["startlist_scope"]
    lead = float(nation_value(cfg, row.get("nation"), "startlist_lead_hours"))
    start, _src = session_start(cfg, row, snap.events.get(sid, []), first_day, tz)
    if start is None:
        start = at_local(first_day, nation_value(cfg, row.get("nation"), "default_session_start"), tz)
    expected = f"the start list is in MeetTrack {int(lead)}h before racing"
    if scope == "requested" and requested is None:
        return [Expectation(eid, "startlist", label(row), expected, None, "n/a", "info",
                            "not expected: nobody has asked for this meet's entries in the app, "
                            "so ingest_entries.py does not look for its start list",
                            window="always", sr_meet_id=sid)]
    deadline = start - lead * 3600
    if requested is not None:
        deadline = max(deadline, requested + float(cfg["startlist_request_grace_min"]) * 60)
    loaded = iso_epoch(row.get("entries_ingested_at"))
    if loaded is not None:
        return [Expectation(eid, "startlist", label(row), expected, deadline, "met", "warn",
                            f"start list loaded {hm_day(loaded, tz, snap.now)}",
                            window="always", sr_meet_id=sid)]
    if snap.now >= start:
        return [Expectation(eid, "startlist", label(row), expected, deadline, "closed", "warn",
                            "racing has started without a start list; the live writer seeds "
                            "events from the result files instead",
                            window="always", sr_meet_id=sid)]
    status = "missed" if snap.now >= deadline else "pending"
    why = ("requested in the app at " + hm_day(requested, tz, snap.now)
           if requested is not None else "nobody has asked for it in the app")
    return [Expectation(
        eid, "startlist", label(row), expected, deadline, status, "warn",
        f"no start list yet ({why}); racing starts {hm_day(start, tz, snap.now)}; "
        f"live feed type: {row.get('feed_type') or 'unknown'}",
        window="always", sr_meet_id=sid,
        data={"racing_start": start, "requested_at": requested})]


def _process_note(snap: Snapshot, sid: str) -> str:
    if snap.processes is None:
        return "process list not read"
    lines = snap.processes.get(sid) or []
    if not lines:
        return "no writer process found"
    pid = lines[0].split()[0] if lines[0].split() else "?"
    return f"writer process running (pid {pid})"


def eval_writer(row: dict, snap: Snapshot, cfg: dict, tz, day: str) -> Expectation:
    sid = str(row.get("sr_meet_id"))
    eid = f"writer:{sid}:{day}"
    status_now = row.get("ingest_status")
    window_start = at_local(day, cfg["racing_start"], tz)
    deadline = window_start + float(cfg["launch_grace_min"]) * 60
    expected = "the live writer is running and ticking about every minute"
    proc = _process_note(snap, sid)

    def exp(status, evidence, severity="crit"):
        return Expectation(eid, "writer", label(row), expected, deadline, status, severity,
                           evidence, sr_meet_id=sid, day=day,
                           data={"ingest_status": status_now})

    if status_now == "failed":
        return exp("missed", f"the writer gave up (registry status 'failed', "
                             f"{hm_day(iso_epoch(row.get('updated_at')), tz, snap.now)}); {proc}")
    if status_now in ("discovered", "queued"):
        if snap.now < deadline:
            return exp("pending", f"not launched yet (status {status_now})")
        return exp("missed", f"not launched: registry status '{status_now}' "
                             f"since {hm_day(iso_epoch(row.get('updated_at')), tz, snap.now)}; {proc}")
    if status_now == "finished":
        return exp("met", "meet marked finished")
    tick = iso_epoch(row.get("coverage_at"))
    if row.get("events_published") is not None and tick is not None:
        age = minutes(snap.now, tick)
        if age >= int(cfg["tick_crit_min"]) and snap.now >= deadline:
            return exp("missed", f"last writer tick {age} min ago "
                                 f"({hm_day(tick, tz, snap.now)}); {proc}")
        return exp("met", f"ticking, last tick {age} min ago; {proc}")
    # Writers that do not stamp every tick (Lenex): the process is the signal.
    if snap.processes is not None and not snap.processes.get(sid) and snap.now >= deadline:
        return exp("missed", f"registry says '{status_now}' but {proc}")
    return exp("met", f"status '{status_now}'; {proc}")


def eval_first_results(row: dict, snap: Snapshot, cfg: dict, tz, day: str, mem: dict) -> Expectation:
    sid = str(row.get("sr_meet_id"))
    eid = f"first:{sid}:{day}"
    events = snap.events.get(sid, [])
    start, src = session_start(cfg, row, events, day, tz)
    if start is not None:
        deadline = start + float(cfg["first_results_grace_min"]) * 60
        how = f"{int(cfg['first_results_grace_min'])} min after the {src} ({hm(start, tz)})"
    else:
        by = nation_value(cfg, row.get("nation"), "first_results_by")
        deadline = at_local(day, by, tz)
        how = f"by {by} (no start time known)"
    published = row.get("events_published")
    base_key = f"baseline:{sid}:{day}"
    if base_key not in mem:
        mem[base_key] = int(published or 0) if snap.now < deadline else 0
    baseline = int(mem[base_key])
    today_n = int(snap.results_today.get(sid, 0) or 0)
    expected = f"results start appearing {how}"
    new_published = int(published or 0) - baseline
    if today_n > 0 or new_published > 0:
        return Expectation(eid, "first_results", label(row), expected, deadline, "met", "warn",
                           f"{today_n} results written today; host lists {published or 0} result "
                           f"files", sr_meet_id=sid, day=day)
    if snap.now < deadline:
        return Expectation(eid, "first_results", label(row), expected, deadline, "pending", "warn",
                           "no results yet today", sr_meet_id=sid, day=day)
    since = start if start is not None else None
    after = (f"; racing started about {minutes(snap.now, since)} min ago"
             if since is not None else "")
    return Expectation(
        eid, "first_results", label(row), expected, deadline, "missed", "warn",
        f"0 results today{after}; the host lists {published or 0} result files "
        f"({max(new_published, 0)} new today)", sr_meet_id=sid, day=day,
        data={"start": start, "published": published})


def _thin(events: list, cfg: dict) -> list:
    pct, floor = int(cfg["thin_pct"]), int(cfg["thin_min_entries"])
    out = []
    for e in events or []:
        res, ent = int(e.get("results") or 0), int(e.get("entries") or 0)
        if (e.get("relay_count") or 1) > 1 or res == 0 or ent < floor:
            continue
        if res * 100 < pct * ent:
            out.append(e)
    return out


def eval_results(row: dict, snap: Snapshot, cfg: dict, tz, day: str, mem: dict) -> Expectation:
    sid = str(row.get("sr_meet_id"))
    eid = f"results:{sid}:{day}"
    lag = int(cfg["results_lag_min"])
    expected = (f"every event the host publishes is in MeetTrack within {lag} min, with "
                f"results for most swimmers on its start list")
    events = snap.events.get(sid, [])
    published, covered = row.get("events_published"), row.get("events_with_results")
    measured = iso_epoch(row.get("coverage_at"))
    fresh = (measured is not None
             and (snap.now - measured) / 60 <= int(cfg["coverage_max_age_min"]))
    missing = (published - covered) if (fresh and published is not None and covered is not None) else 0
    missing = max(0, missing)
    thin = _thin(events, cfg)
    gaps = mem.setdefault("gap_since", {})
    last_write = iso_epoch(row.get("last_ingest_at"))
    errors = row.get("last_tick_errors") if fresh else None

    if missing == 0 and not thin:
        gaps.pop(eid, None)
        if published is None and not events:
            note = "nothing published yet"
        elif published is None:
            note = "no per-event counters from this writer; every event with results looks complete"
        else:
            note = f"all {published} published events have results"
        if not fresh and published is not None:
            note += " (counters not fresh)"
        return Expectation(eid, "results", label(row), expected, None, "met", "crit", note,
                           sr_meet_id=sid, day=day)

    since = gaps.get(eid, snap.now)
    if thin:
        # The thin events have not gained a result since their newest one
        # (snapshot), or at worst since the meet's last write: they have looked
        # like this at least since then, memory or not.
        lw = snap.thin_last_write.get(sid) or last_write
        if lw is not None:
            since = min(since, lw)
    gaps[eid] = since
    deadline = since + lag * 60
    status = "missed" if snap.now >= deadline else "pending"
    parts = []
    if published is not None and fresh:
        parts.append(f"the host has published {published} events; {missing} of them {'has' if missing == 1 else 'have'} no "
                     f"results in MeetTrack")
    if thin:
        res = sum(int(e.get("results") or 0) for e in thin)
        ent = sum(int(e.get("entries") or 0) for e in thin)
        nums = ", ".join(str(e.get("event_number")) for e in thin[:12])
        parts.append(f"{len(thin)} more have far fewer results than swimmers on the start "
                     f"list ({res} results for {ent} entries; events {nums})")
    if errors:
        parts.append(f"the last writer tick reported {errors} error(s)")
    parts.append(f"gap open since {hm_day(since, tz, snap.now)}; last result written "
                 f"{hm_day(last_write, tz, snap.now)}")
    return Expectation(eid, "results", label(row), expected, deadline, status, "crit",
                       "; ".join(parts), sr_meet_id=sid, day=day,
                       data={"missing": missing, "thin": [e.get("event_number") for e in thin],
                             "published": published, "covered": covered})


def eval_schedule(row: dict, snap: Snapshot, cfg: dict, tz, day: str) -> Expectation | None:
    sid = str(row.get("sr_meet_id"))
    sched = scheduled_events_on(snap.events.get(sid, []), day, tz)
    if not sched:
        return None
    lag = int(cfg["slot_lag_min"])
    due = [(slot, e) for slot, e in sched if slot + lag * 60 <= snap.now]
    late = [(slot, e) for slot, e in due if int(e.get("results") or 0) == 0]
    expected = f"each event has results within {lag} min of its scheduled slot"
    eid = f"schedule:{sid}:{day}"
    if len(late) < 2:
        return Expectation(eid, "schedule", label(row), expected, None, "met", "warn",
                           f"{len(due)} events due by the schedule; {len(due) - len(late)} have results",
                           sr_meet_id=sid, day=day)
    first_slot = late[0][0]
    nums = ", ".join(str(e.get("event_number")) for _s, e in late[:12])
    return Expectation(
        eid, "schedule", label(row), expected, first_slot + lag * 60, "missed", "warn",
        f"{len(late)} of {len(due)} events that should have finished by the schedule have no "
        f"results (events {nums}); first was due at {hm(first_slot, tz)}",
        sr_meet_id=sid, day=day)


def eval_source(row: dict, snap: Snapshot, cfg: dict, tz, day: str) -> Expectation | None:
    """A note, never a page: the host itself has gone quiet mid-meet. We cannot
    tell a lunch break or the end of a session from an organiser who is behind."""
    sid = str(row.get("sr_meet_id"))
    events = snap.events.get(sid, [])
    published, covered = row.get("events_published"), row.get("events_with_results")
    last_write = iso_epoch(row.get("last_ingest_at"))
    today_n = int(snap.results_today.get(sid, 0) or 0)
    if not events or published is None or today_n == 0 or last_write is None:
        return None
    if covered is not None and covered < published:
        return None            # our side is behind: that is results:<id>
    quiet = minutes(snap.now, last_write)
    if published >= len(events) or quiet < int(cfg["source_gap_min"]):
        return None
    return Expectation(
        f"source:{sid}:{day}", "source", label(row), "the host keeps publishing during racing",
        None, "missed", "info",
        f"the host has published {published} of {len(events)} start-list events and MeetTrack "
        f"holds all of them; nothing new for {quiet} min (lunch, end of a session, or the "
        f"organiser is behind)", sr_meet_id=sid, day=day)


def eval_hygiene(snap: Snapshot, cfg: dict) -> Expectation | None:
    if not snap.stale_active:
        return None
    items = ", ".join(f"{m.get('live_rankings_id')} ({m.get('nation') or '?'}, ended "
                      f"{m.get('end_date')})" for m in snap.stale_active[:8])
    return Expectation(
        "hygiene:stale-active", "hygiene", None, "finished meets are not left ACTIVE",
        None, "missed", "info",
        f"ignored by the monitor, still ACTIVE in meets: {items}", window="always")


def eval_reads(snap: Snapshot, cfg: dict, mem: dict, bad_ids: list) -> Expectation:
    """Partial blindness: the database answers but some per-meet reads fail, or
    the registry holds ids we refuse to use. Two runs in a row, like the probe."""
    expected = "every per-meet read succeeds and every registry id is a meet id"
    problems = [f"{sid}: {'; '.join(e)}" for sid, e in sorted(snap.read_errors.items())]
    if bad_ids:
        problems.append(f"registry ids that are not meet ids (skipped): {', '.join(bad_ids[:5])}")
    if not problems:
        mem["read_fail_runs"] = 0
        return Expectation("db:reads", "db", None, expected, None, "met", "crit", "all reads ok",
                           window="always")
    runs = int(mem.get("read_fail_runs", 0)) + 1
    mem["read_fail_runs"] = runs
    status = "missed" if runs >= int(cfg["db_fail_runs"]) or bad_ids else "pending"
    return Expectation("db:reads", "db", None, expected, None, status,
                       "warn" if bad_ids and not snap.read_errors else "crit",
                       f"{runs} run(s) in a row with failed reads; those meets are not judged: "
                       f"{' | '.join(problems)}", window="always")


def evaluate(snap: Snapshot, cfg: dict, memory: dict | None) -> tuple[list[Expectation], dict]:
    mem = copy.deepcopy(memory or {})
    out = eval_db(snap, cfg, mem)
    if not snap.db_ok or snap.registry is None:
        for name in mem.get("last_live") or []:
            out.append(Expectation(f"blind:{name}", "blind", name, "meet checks run", None,
                                   "unknown", "info",
                                   "not checked: the database cannot be read", window="always"))
        mem["last_run"] = snap.now
        return out, mem

    nations = set(cfg["nations"])
    live_names, bad_ids = [], []
    for row in snap.registry:
        if row.get("nation") not in nations:
            continue
        if not valid_sid(row.get("sr_meet_id")):
            bad_ids.append(repr(row.get("sr_meet_id"))[:40])
            continue
        tz = meet_zone(cfg, row.get("nation"))
        day = local(snap.now, tz).date().isoformat()
        if row.get("start_date") and row["start_date"] >= day:
            out.extend(eval_startlist(row, snap, cfg, tz))
        if not live_on(row, day) or row.get("dispatch_paused"):
            continue
        if row.get("ingest_status") == "unsupported":
            continue
        if row.get("feed_type") not in LIVE_FEEDS:
            if row.get("ingest_status") not in ("finished",):
                out.append(Expectation(
                    f"feed:{row.get('sr_meet_id')}", "feed", label(row),
                    "a live meet has a results feed", None, "missed", "info",
                    f"racing today, but no live results feed found on swimrankings "
                    f"(feed type {row.get('feed_type') or 'unknown'}); not tracked",
                    sr_meet_id=str(row.get("sr_meet_id"))))
            continue
        live_names.append(label(row))
        writer = eval_writer(row, snap, cfg, tz, day)
        out.append(writer)
        if row.get("ingest_status") == "finished":
            continue
        downstream = [eval_first_results(row, snap, cfg, tz, day, mem),
                      eval_results(row, snap, cfg, tz, day, mem),
                      eval_schedule(row, snap, cfg, tz, day),
                      eval_source(row, snap, cfg, tz, day)]
        errs = snap.read_errors.get(str(row.get("sr_meet_id")))
        for d in downstream:
            if d is None:
                continue
            if errs and d.status in ("missed", "met", "pending"):
                # A failed read is not evidence either way: never page on it,
                # never resolve on it. db:reads says the meet is partly blind.
                d.status = "unknown"
                d.evidence = f"not judged: {'; '.join(errs)}"
            elif writer.status == "missed" and d.status == "missed":
                # One root cause, one alert: a stopped writer explains missing
                # results. Kept open (not resolved) until the writer is back.
                d.status = "unknown"
                d.evidence = f"not judged while the writer is down ({writer.id}); {d.evidence}"
            out.append(d)
    hyg = eval_hygiene(snap, cfg)
    if hyg:
        out.append(hyg)
    out.append(eval_reads(snap, cfg, mem, bad_ids))

    seen_ids = {e.id for e in out}
    mem["gap_since"] = {k: v for k, v in (mem.get("gap_since") or {}).items() if k in seen_ids}
    today_keys = {f"baseline:{e.sr_meet_id}:{e.day}" for e in out if e.kind == "first_results"}
    for k in [k for k in mem if k.startswith("baseline:") and k not in today_keys]:
        del mem[k]
    mem["last_live"] = live_names
    mem["last_run"] = snap.now
    return out, mem
