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
- ``first:<id>:<day>``  results start appearing once the host has published
                        them (or, when the writer cannot see the host, once
                        racing should have begun)
- ``results:<id>:<day>`` every event the host has published is in MeetTrack,
                        with roughly as many results as start-list swimmers,
                        within N minutes of that event becoming late (judged
                        per event number; the newest event is in progress)
- ``schedule:<id>:<day>`` events past their scheduled slot have results (only
                        when the start list carries times)
- ``source:<id>:<day>``, ``feed:<id>``, ``hygiene:stale-active`` notes only:
                        never paged, shown in dry-runs and investigations

``memory`` is the evaluator's own small state between runs (consecutive
database failures, when each late event became late, the last progress on
them, today's baseline). It is a plain dict so the runner can persist it as JSON.
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
    "first_results_by": "17:00",      # unknown start and the writer cannot see the host -> by then
    "first_results_grace_min": 60,    # host files (or a known start, writer blind) -> results within this
    "first_results_host_quiet_min": 180,  # known start, host has published nothing -> warn after this
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


# A meet on another SPLASH host (anlisboa.info, ...): splash_poller keys it by
# its live_rankings_id, 'sp-<host label>-<folder slug>' (meet_config.is_splash_key).
_SPLASH_KEY = re.compile(r"^sp-[a-z0-9_-]{1,60}$")


def valid_sid(sid) -> bool:
    """A swimrankings meet id (a short run of digits) or a splash_poller key
    for another SPLASH host. Checked once, before an id from the database goes
    into a URL, a query, a file path or a ps match."""
    s = str(sid) if isinstance(sid, (str, int)) and not isinstance(sid, bool) else ""
    return (s.isdigit() and 0 < len(s) <= 9) or bool(_SPLASH_KEY.fullmatch(s))


def is_splash(sid) -> bool:
    return isinstance(sid, str) and bool(_SPLASH_KEY.fullmatch(sid))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "-", s.lower()).strip("-")


def splash_key_for(url: str) -> str | None:
    """The registry key splash_poller gives a meet folder on a non-swimrankings
    SPLASH host: the same rule as its meet_config.from_meet_url. None for a
    value that is not an http(s) URL."""
    from urllib.parse import urlparse
    p = urlparse(url or "")
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    segments = [s for s in p.path.split("/") if s]
    last = segments[-1] if segments else p.netloc
    return f"sp-{_slug(p.netloc.split('.')[0])}-{_slug(last)}"[:60].rstrip("-")


def meets_id(sid: str) -> str:
    """meets.live_rankings_id for a registry key: 'sr-<id>', or the sp- key itself."""
    return sid if is_splash(sid) else f"sr-{sid}"


def writer_lines(ps_text: str, sid: str, meet_url: str | None = None) -> list[str]:
    """`ps` lines of writer processes for this meet: every form the supervisor
    starts (splash_poller supervise_meets._poller_argv): the live URL
    .../<id>/ for the pdf, fragment and Lenex writers, and --sr-meet-id <id>
    for the reconcile one-shot. For another SPLASH host the writers carry the
    meet's folder: `meet_url` when known, else the folder the key names
    (host label and folder slug). One matcher for the check and the investigation."""
    if not valid_sid(sid):
        return []
    if is_splash(sid):
        # A writer names its folder in --meet-url; the folder's own key (the
        # rule splash_poller keys it by) must be this meet's. The key is not
        # split back apart: a host label may itself contain '-'.
        reconcile = re.compile(rf"--sr-meet-id[ =]{re.escape(sid)}(?=\s|$)")
        out = []
        for ln in (ps_text or "").splitlines():
            if not any(w in ln for w in WRITERS):
                continue
            urls = re.findall(r"--meet-url[ =](\S+)", ln)
            if reconcile.search(ln) or any(u == meet_url or splash_key_for(u) == sid for u in urls):
                out.append(ln.strip())
        return out
    else:
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
    sid = str(row.get("sr_meet_id"))
    short = row.get("city") or row.get("name") or "meet"
    return f"{short} ({meets_id(sid)})"


def live_on(row: dict, day: str) -> bool:
    start, end = row.get("start_date"), row.get("end_date") or row.get("start_date")
    return bool(start) and start <= day <= end


def stated_start(cfg: dict, sid: str, day: str) -> str | None:
    for item in cfg.get("start_times") or []:
        if str(item.get("sr_meet_id")) == str(sid) and item.get("date") == day:
            return item.get("start")
    return None


def event_no(v) -> int | None:
    """An event number as an int: an int, or a string of digits ('16', ' 9 ').
    Anything else (None, a bool, 'x', a float) is not a number."""
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def by_event_number(events: list) -> list[dict]:
    """One record per event number, in number order. Rows that share a number
    are one event: their results add up, the start list is the larger row's,
    and the slot is the earliest. A second, empty session row under the same
    number (a pipeline bug seen on 7 Nov 2026) must not read as an event
    without results. Rows without a number stay on their own, last.
    ``events.event_number`` is ``integer NOT NULL`` (splash_poller's baseline
    migration); digit strings are accepted in case a reader ever stringifies it."""
    groups: dict = {}
    for i, e in enumerate(events or []):
        n = event_no(e.get("event_number"))
        key = n if n is not None else f"row:{e.get('id') or i}"
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"event_number": n if n is not None else e.get("event_number"),
                               "key": str(key), "results": 0, "entries": 0,
                               "relay_count": 1, "rows": 0, "ids": [], "day_times": []}
        g["results"] += int(e.get("results") or 0)
        g["entries"] = max(g["entries"], int(e.get("entries") or 0))
        g["relay_count"] = max(g["relay_count"], int(e.get("relay_count") or 1))
        g["rows"] += 1
        if e.get("id"):
            g["ids"].append(e["id"])
        if e.get("day_time"):
            g["day_times"].append(e["day_time"])
    return sorted(groups.values(),
                  key=lambda g: (0, g["event_number"]) if not g["key"].startswith("row:")
                  else (1, 0))


def scheduled_events_on(events: list, day: str, tz) -> list:
    """Events (one per number) whose start-list slot (day_time) falls on `day`,
    with the earliest such slot epoch."""
    out = []
    for g in by_event_number(events):
        slots = [s for s in (naive_local_epoch(t, tz) for t in g["day_times"])
                 if s is not None and local(s, tz).date().isoformat() == day]
        if slots:
            out.append((min(slots), g))
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
    if scope == "all" and requested is None:
        # Mirror splash_poller ingest_entries auto mode: only listed, unpaused,
        # non-Lenex meets get their start list fetched without a request.
        skip = None
        if row.get("listed") is False:
            skip = "the meet is unlisted"
        elif row.get("dispatch_paused"):
            skip = "dispatch is paused for this meet"
        elif row.get("feed_type") == "lenex":
            skip = "a Lenex feed carries its own entries"
        if skip:
            return [Expectation(eid, "startlist", label(row), expected, None, "n/a", "info",
                                f"not expected: {skip}, so ingest_entries.py does not fetch "
                                "its start list unasked", window="always", sr_meet_id=sid)]
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


def counters_fresh(row: dict, now: float, cfg: dict) -> bool:
    """The writer counted the host's published result files (events_published
    is not NULL) in a tick at most coverage_max_age_min ago."""
    measured = iso_epoch(row.get("coverage_at"))
    return (row.get("events_published") is not None and measured is not None
            and (now - measured) / 60 <= int(cfg["coverage_max_age_min"]))


def eval_first_results(row: dict, snap: Snapshot, cfg: dict, tz, day: str, mem: dict) -> Expectation:
    """Results start appearing today. What is wrong depends on what the writer
    can see of the host:

    - it counts the host's files and the host lists new ones, but MeetTrack has
      no result today: missed ``first_results_grace_min`` after they were first
      seen (the host has results, MeetTrack has none);
    - it counts the host's files and there are none: the host has published
      nothing, which is not a MeetTrack fault. Never paged without a start
      time; with one, a warning ``first_results_host_quiet_min`` after it;
    - it does not count them (no tick yet, no meet row, a writer without a
      file list, or old counters): we cannot see the host, so the clock
      decides as before: the known start + grace, else ``first_results_by``.
    """
    sid = str(row.get("sr_meet_id"))
    eid = f"first:{sid}:{day}"
    events = snap.events.get(sid, [])
    start, src = session_start(cfg, row, events, day, tz)
    grace_min = int(cfg["first_results_grace_min"])
    if start is not None:
        clock_deadline = start + grace_min * 60
        clock_how = f"{grace_min} min after the {src} ({hm(start, tz)})"
    else:
        by = nation_value(cfg, row.get("nation"), "first_results_by")
        clock_deadline = at_local(day, by, tz)
        clock_how = f"by {by} (no start time known)"
    published = row.get("events_published")
    base_key = f"baseline:{sid}:{day}"
    if base_key not in mem:
        # Files the host already lists before racing could have produced any
        # are yesterday's (day two of a meet), not today's news.
        mem[base_key] = int(published or 0) if snap.now < clock_deadline else 0
    baseline = int(mem[base_key])
    today_n = int(snap.results_today.get(sid, 0) or 0)
    new_published = int(published or 0) - baseline
    measured = counters_fresh(row, snap.now, cfg)
    checked = hm_day(iso_epoch(row.get("coverage_at")), tz, snap.now)
    files_key = f"files_seen:{sid}:{day}"
    covered = row.get("events_with_results")
    if measured and today_n == 0 and covered is not None:
        # Nothing was written today, so every event that has results got them
        # before today: its file is not today's news. This bounds the baseline
        # after a cold start past the deadline (baseline 0 on day two).
        new_published = min(new_published, int(published) - int(covered))

    def exp(expected, deadline, status, evidence, **data):
        return Expectation(eid, "first_results", label(row), expected, deadline, status, "warn",
                           evidence, sr_meet_id=sid, day=day,
                           data={"start": start, "published": published, **data})

    if today_n > 0:
        mem.pop(files_key, None)
        return exp(f"results start appearing {clock_how}", clock_deadline, "met",
                   f"{today_n} results written today; host lists {published or 0} result files")
    if measured and new_published > 0:
        seen = float(mem.setdefault(files_key, snap.now))
        deadline = seen + grace_min * 60
        files = f"{published} result file{'' if published == 1 else 's'}"
        return exp(f"results reach MeetTrack within {grace_min} min of the host's first result file",
                   deadline, "missed" if snap.now >= deadline else "pending",
                   f"0 results today; the host lists {files} ({new_published} new today), first "
                   f"seen {hm_day(seen, tz, snap.now)}; the writer checked at {checked}",
                   files_seen=seen)
    mem.pop(files_key, None)
    if measured:
        if start is None:
            return exp("results start appearing once the host publishes them (no start time known)",
                       None, "pending",
                       f"the host has published no results yet (the writer checked at {checked}); "
                       f"no start time is known, so nothing is due")
        quiet = int(cfg["first_results_host_quiet_min"])
        deadline = start + quiet * 60
        late = snap.now >= deadline
        return exp(f"the host publishes results within {quiet} min of the {src} ({hm(start, tz)})",
                   deadline, "missed" if late else "pending",
                   f"the host has published no {'new ' if baseline else ''}result files "
                   f"{minutes(snap.now, start)} min after "
                   f"the {src} ({hm(start, tz)}); the writer checked at {checked}; MeetTrack "
                   f"holds everything the host has published" if late else
                   f"the host has published no results yet (the writer checked at {checked})")
    # Blind: nothing tells us what the host has. The clock decides.
    if published is None and iso_epoch(row.get("coverage_at")) is None:
        why = "the writer has not reported the host's result list (no tick counters)"
    elif published is None:
        why = "the writer has not reported the host's result list (this writer does not count files)"
    else:
        why = (f"the writer has not reported the host's result list since {checked} "
               f"(counters older than {int(cfg['coverage_max_age_min'])} min)")
    if not events:
        why += "; MeetTrack holds no events for this meet (no meet row, or nothing seeded)"
    if snap.now < clock_deadline:
        return exp(f"results start appearing {clock_how}", clock_deadline, "pending",
                   f"no results yet today; {why}")
    after = f"; racing started about {minutes(snap.now, start)} min ago" if start is not None else ""
    return exp(f"results start appearing {clock_how}", clock_deadline, "missed",
               f"0 results today{after}; {why}, so we cannot tell whether the host has published")


def _thin(events: list, cfg: dict) -> list:
    """Events (one per number, see ``by_event_number``) with results for under
    ``thin_pct`` % of their start list. Relays, events with no result and
    events whose start list is under ``thin_min_entries`` are never thin."""
    pct, floor = int(cfg["thin_pct"]), int(cfg["thin_min_entries"])
    out = []
    for g in by_event_number(events):
        res, ent = g["results"], g["entries"]
        if g["relay_count"] > 1 or res == 0 or ent < floor:
            continue
        if res * 100 < pct * ent:
            out.append(g)
    return out


def _n(groups: list) -> int:
    """How many events: an unlisted entry stands for ``count`` of them."""
    return sum(int(g.get("count") or 1) for g in groups)


def _events_txt(groups: list) -> str:
    named = [g for g in groups if not str(g.get("key")).startswith("unlisted")]
    unlisted = _n([g for g in groups if str(g.get("key")).startswith("unlisted")])
    out = []
    if named:
        nums = [str(g["event_number"]) for g in named]
        more = f" and {len(nums) - 12} more" if len(nums) > 12 else ""
        out.append(f"event{'s' if len(nums) > 1 else ''} {', '.join(nums[:12])}{more}")
    if unlisted:
        out.append(f"{unlisted} not in MeetTrack's event list")
    return " and ".join(out)


def _so_far(g: dict) -> str:
    if g["results"] == 0:
        return "no results yet"
    return f"{g['results']} of {g['entries']}" if g["entries"] else f"{g['results']} results"


def eval_results(row: dict, snap: Snapshot, cfg: dict, tz, day: str, mem: dict) -> Expectation:
    """What is wrong now, per event number, not since the first gap of the day.

    An event *lacks* results when the host has published it and MeetTrack has
    none (``events_published - events_with_results`` of them: the lowest
    numbers without a result, as the host publishes in order), or only a few
    (thin). The newest published event is being swum: it never counts. A
    published event with no MeetTrack row (unlisted) has no number: it is the
    newest while the published count has not grown since it appeared. Any
    other lacking event is *late* once it has lacked results for
    ``results_lag_min`` since it stopped being the newest (``memory
    late_events``); it leaves the late set when it catches up. ``data
    progress_at`` is the last time a lacking event gained results or the
    writer wrote any result: the alert policy holds reminders while that moves.

    A writer that counts the host's files (PDF) but whose counters are missing
    or older than ``coverage_max_age_min`` cannot show which published events
    lack results: ``unknown``, never ``met``; every clock keeps its start time.
    """
    sid = str(row.get("sr_meet_id"))
    eid = f"results:{sid}:{day}"
    lag = int(cfg["results_lag_min"])
    max_age = int(cfg["coverage_max_age_min"])
    expected = (f"every event the host publishes is in MeetTrack within {lag} min, with "
                f"results for most swimmers on its start list")
    events = snap.events.get(sid, [])
    published, covered = row.get("events_published"), row.get("events_with_results")
    ticked = iso_epoch(row.get("coverage_at"))
    fresh = ticked is not None and (snap.now - ticked) / 60 <= max_age
    counted = fresh and published is not None and covered is not None
    last_write = iso_epoch(row.get("last_ingest_at"))
    errors = row.get("last_tick_errors") if fresh else None

    def exp(status, evidence, deadline=None, **data):
        return Expectation(eid, "results", label(row), expected, deadline, status, "crit",
                           evidence, sr_meet_id=sid, day=day,
                           data={"published": published, "covered": covered, **data})

    all_recs = mem.setdefault("late_events", {})
    recs = dict(all_recs.get(eid) or {})     # written back below, only when judged
    progress = mem.setdefault("results_progress", {})
    if snap.read_errors.get(sid):
        # A failed read is not evidence: keep every clock as it is.
        return exp("unknown", f"not judged: {'; '.join(snap.read_errors[sid])}")
    counting_writer = str(row.get("feed_type") or "").strip().lower() == "pdf"
    if (published is not None or counting_writer) and not counted:
        # This writer counts the host's files, but its counters cannot be used:
        # which published events lack results is not known. Never "met" (or a
        # page) on that. Judgment pauses; the clocks keep their start times, so
        # an event short before can be overdue at once when they are fresh again.
        if published is None or ticked is None:
            why = "the writer has not counted the host's result files yet"
        elif not fresh:
            why = (f"the writer's counters are older than {max_age} min (last tick "
                   f"{hm_day(ticked, tz, snap.now)})")
        else:
            why = "the writer's last tick did not count the events with results"
        return exp("unknown", f"not judged: {why}, so it is not known which published "
                              f"events lack results")

    groups = by_event_number(events)
    by_key = {g["key"]: g for g in groups}
    numbered = [g for g in groups if not g["key"].startswith("row:")]
    missing, unlisted, order_guess = [], 0, False
    if counted:
        k = max(0, int(published) - int(covered))
        zero = [g for g in numbered if g["results"] == 0]
        missing = zero[:k]
        unlisted = k - len(missing)          # published, but no event row for it yet
        # In number order, every event below one with results is published, so
        # each of them without results is one the counts must include. More of
        # them than that: the host did not publish in order (or never lists
        # some events), so which numbers lack results is a guess. Shown, not paged.
        top = max((g["event_number"] for g in numbered if g["results"] > 0), default=None)
        order_guess = k > 0 and top is not None and sum(
            1 for g in zero if g["event_number"] < top) > k
    # Unlisted events carry no number. One that appeared with the latest rise
    # of the published count is the newest publication (a sole publication
    # with no row must not page as late); once the count grows past it, it
    # has been superseded and is judged like any other event.
    u_prev = recs.get("unlisted") or {}
    prev_n = int(u_prev.get("n") or 0)
    seen_pub = u_prev.get("seen_published")
    # The host's list can lose a file for a run (7 Nov: it flickered): fewer
    # unlisted events with a smaller published count is that, not a catch-up.
    dipped = seen_pub is not None and published is not None and int(published) < int(seen_pub)
    marker = u_prev.get("published")
    if unlisted > prev_n:
        marker = published
    unlisted_newest = bool(unlisted) and marker is not None and int(published) <= int(marker)
    known = [g for g in numbered if g["results"] > 0] + missing
    newest = None if unlisted_newest else (
        max(known, key=lambda g: g["event_number"]) if known else None)
    lacking = {g["key"]: g for g in missing}
    for g in _thin(events, cfg):
        lacking[g["key"]] = g
    in_progress = lacking.pop(newest["key"], None) if newest else None
    late_unlisted = unlisted - (1 if unlisted_newest else 0)

    # Clocks: when each event started lacking while not the newest.
    continuous = (mem.get("last_run") is not None
                  and snap.now - float(mem["last_run"]) <= max_age * 60)
    thin_since = snap.thin_last_write.get(sid) or last_write
    moved = False
    for key, g in lacking.items():
        rec = recs.get(key)
        if rec is None:
            since = snap.now
            if not continuous and g["results"] > 0 and thin_since is not None:
                # No memory of the last runs: a thin event has looked like this
                # at least since its newest result (or the meet's last write).
                since = min(since, thin_since)
            recs[key] = {"since": since, "results": g["results"]}
        elif g["results"] > int(rec.get("results") or 0):
            moved = True
            rec["results"] = g["results"]
    for key in [k for k in recs if k not in lacking and not k.startswith("unlisted")]:
        if in_progress is not None and key == in_progress["key"]:
            continue                     # the newest again (the host's list flickered)
        g = by_key.get(key)
        if g is not None and g["results"] > int(recs[key].get("results") or 0):
            moved = True                 # caught up
        del recs[key]
    # One clock per superseded unlisted publication, oldest first. Fewer of
    # them: a catch-up retires the oldest; a dip in the list, the newest.
    clocks = sorted(float(x) for x in u_prev.get("sinces") or [])
    if late_unlisted > len(clocks):
        clocks += [snap.now] * (late_unlisted - len(clocks))
    elif late_unlisted < len(clocks):
        drop = len(clocks) - late_unlisted
        clocks = clocks[:-drop] if dipped else clocks[drop:]
    if unlisted < prev_n and not dipped:
        moved = True                     # an unlisted event got its row or its results
    since_of = {k: float(recs[k]["since"]) for k in lacking}
    if unlisted:
        recs["unlisted"] = {"n": unlisted, "published": marker, "seen_published": published,
                            "sinces": clocks, "results": 0}
        for i, t in enumerate(clocks):
            key = f"unlisted:{i}"
            lacking[key] = {"key": key, "event_number": None, "results": 0, "entries": 0,
                            "count": 1}
            since_of[key] = t
    else:
        recs.pop("unlisted", None)
    if moved:
        progress[eid] = snap.now
    if recs:
        all_recs[eid] = recs
    else:
        all_recs.pop(eid, None)
    marks = [t for t in (progress.get(eid), last_write) if t is not None]
    progress_at = max(marks) if marks else None

    newest_txt = (f"event {newest['event_number']}, the newest, is in progress ({_so_far(in_progress)})"
                  if in_progress is not None else
                  "the newest publication, not in MeetTrack's event list yet, is in progress"
                  if unlisted_newest else None)
    if not lacking:
        if published is None and not events:
            note = "nothing published yet"
        elif published is None:
            note = "no per-event counters from this writer; every event with results looks complete"
        elif newest_txt:
            note = f"every one of the {published} published events but the newest is in MeetTrack"
        else:
            note = f"all {published} published events have results"
        if newest_txt:
            note += f"; {newest_txt}"
        return exp("met", note, newest=newest and newest["event_number"],
                   progress_at=progress_at)

    since_all = min(since_of.values())
    late = [g for k, g in lacking.items() if snap.now - since_of[k] >= lag * 60]
    waiting = [g for k, g in lacking.items() if snap.now - since_of[k] < lag * 60]
    parts = []
    late_none = [g for g in late if g["results"] == 0]
    late_thin = [g for g in late if g["results"] > 0]
    if late_none:
        n = _n(late_none)
        head = f"the host has published {published} events; " if counted else ""
        parts.append(f"{head}{n} of them {'has' if n == 1 else 'have'} no results in MeetTrack "
                     f"after {lag}+ min ({_events_txt(late_none)})")
    if late_thin:
        res = sum(g["results"] for g in late_thin)
        ent = sum(g["entries"] for g in late_thin)
        one = len(late_thin) == 1
        lead = "more" if late_none else f"published event{'' if one else 's'}"
        parts.append(f"{len(late_thin)} {lead} {'has' if one else 'have'} far fewer results than "
                     f"swimmers on the start list after {lag}+ min ({res} results for {ent} "
                     f"entries; {_events_txt(late_thin)})")
    if waiting:
        one = _n(waiting) == 1
        due = min(since_of[g["key"]] for g in waiting) + lag * 60
        lead = "more" if late else f"published event{'' if one else 's'}"
        parts.append(f"{_n(waiting)} {lead} {'is' if one else 'are'} short, not yet due "
                     f"({_events_txt(waiting)}, due {hm(due, tz)})")
    if newest_txt:
        parts.append(newest_txt)
    if order_guess:
        parts.append("the event numbers are a guess: the host has not published in number "
                     "order, so the counts do not say which events lack results")
    if errors:
        parts.append(f"the last writer tick reported {errors} error(s)")
    parts.append(f"the oldest has been short since {hm_day(since_all, tz, snap.now)}; last "
                 f"result written {hm_day(last_write, tz, snap.now)}")
    return exp("missed" if late else "pending", "; ".join(parts), since_all + lag * 60,
               missing=len(missing) + unlisted, thin=[g["event_number"] for g in _thin(events, cfg)],
               late=[g["event_number"] if not g["key"].startswith("unlisted") else "unlisted"
                     for g in late],
               newest=newest and newest["event_number"], progress_at=progress_at,
               order_guess=order_guess)


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
    n_events = len(by_event_number(events))
    if published >= n_events or quiet < int(cfg["source_gap_min"]):
        return None
    return Expectation(
        f"source:{sid}:{day}", "source", label(row), "the host keeps publishing during racing",
        None, "missed", "info",
        f"the host has published {published} of {n_events} start-list events and MeetTrack "
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
        first = eval_first_results(row, snap, cfg, tz, day, mem)
        results = eval_results(row, snap, cfg, tz, day, mem)
        if first.status == "missed" and results.status == "missed":
            # One root cause, one alert: results:* already names the published
            # events MeetTrack lacks. Kept open (not resolved) if it was open.
            first.status = "unknown"
            first.evidence = f"not judged while {results.id} is missed; {first.evidence}"
        downstream = [first, results,
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
    mem.pop("gap_since", None)           # the old one-clock-per-meet memory (before per-event rules)
    for key in ("late_events", "results_progress"):
        mem[key] = {k: v for k, v in (mem.get(key) or {}).items() if k in seen_ids}
    today = {f"{e.sr_meet_id}:{e.day}" for e in out if e.kind == "first_results"}
    for k in [k for k in mem if k.startswith(("baseline:", "files_seen:"))
              and k.split(":", 1)[1] not in today]:
        del mem[k]
    mem["last_live"] = live_names
    mem["last_run"] = snap.now
    return out, mem
