"""Alert policy for MeetTrack expectations: pure functions, no I/O.

Rules (docs/meettrack-expectations.md, "Alert policy"):

- A missed crit/warn expectation alerts at once (NEW), keyed by its id, so the
  same problem is never announced twice.
- While it stays missed, a REMINDER goes out every ``repeat_crit_min`` /
  ``repeat_warn_min`` (start lists: ``repeat_startlist_min``). No long mute.
- When it is met again, one RESOLVED line says so and how long it lasted.
- Live-meet expectations (window "racing") only alert inside racing hours.
  When racing hours end with one still open, one CLOSED line says so and the
  reminders stop; a problem that persists tomorrow is a new id, so a new alert.
  Database and start-list expectations (window "always") alert at any hour.
- An open alert whose expectation is no longer produced (the meet finished or
  left the registry window) is CLOSED, never left hanging. While the database
  is down nothing is closed or resolved: we cannot see, so we do not guess.
- "info" expectations are notes for dry-runs and investigations: never paged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .expectations import Expectation, _zone

SEVERITY_RANK = {"info": 0, "warn": 1, "crit": 2}

POLICY_DEFAULTS = {
    "repeat_crit_min": 30,
    "repeat_warn_min": 60,
    "repeat_startlist_min": 360,
}


@dataclass
class Notice:
    kind: str                     # new | reminder | resolved | closed
    id: str
    meet: str | None
    expected: str
    evidence: str
    severity: str
    deadline: float | None = None
    first_missed: float | None = None
    sends: int = 1
    reason: str = ""
    investigation: dict | None = field(default=None)   # {"summary", "path"}


def _repeat_min(exp_kind: str, severity: str, cfg: dict) -> int:
    c = {**POLICY_DEFAULTS, **(cfg or {})}
    if exp_kind == "startlist":
        return int(c["repeat_startlist_min"])
    return int(c["repeat_crit_min"] if severity == "crit" else c["repeat_warn_min"])


def _record(e: Expectation, now: float, first: float | None = None, sends: int = 1) -> dict:
    return {"kind": e.kind, "meet": e.meet, "expected": e.expected, "evidence": e.evidence,
            "severity": e.severity, "window": e.window, "deadline": e.deadline,
            "first_missed": first if first is not None else now, "last_sent": now,
            "sends": sends}


def plan(expectations: list[Expectation], state: dict, now: float, cfg: dict,
         racing_now: bool) -> tuple[list[Notice], dict]:
    """Return (notices to send this run, the new alert state)."""
    open_ = {k: dict(v) for k, v in ((state or {}).get("open") or {}).items()}
    notices: list[Notice] = []
    seen = set()
    # Any database check not met (one failed probe, or an unreadable registry)
    # means the meet expectations were not evaluated this run.
    db_blind = any(e.kind == "db" and e.status != "met" for e in expectations)

    for e in expectations:
        seen.add(e.id)
        rec = open_.get(e.id)
        pageable = SEVERITY_RANK.get(e.severity, 0) >= 1
        if e.status == "missed" and pageable:
            if e.window == "racing" and not racing_now:
                if rec:
                    notices.append(Notice("closed", e.id, e.meet, e.expected, e.evidence,
                                          e.severity, e.deadline, rec.get("first_missed"),
                                          rec.get("sends", 1),
                                          reason="racing hours are over; still missing at close, "
                                                 "no more reminders today"))
                    del open_[e.id]
                continue
            if rec is None:
                notices.append(Notice("new", e.id, e.meet, e.expected, e.evidence, e.severity,
                                      e.deadline, now))
                open_[e.id] = _record(e, now)
                continue
            worse = SEVERITY_RANK[e.severity] > SEVERITY_RANK.get(rec.get("severity"), 0)
            due = now - float(rec.get("last_sent", 0)) >= _repeat_min(e.kind, e.severity, cfg) * 60
            if worse or due:
                sends = int(rec.get("sends", 1)) + 1
                notices.append(Notice("reminder", e.id, e.meet, e.expected, e.evidence,
                                      e.severity, e.deadline, rec.get("first_missed"), sends,
                                      reason="now worse" if worse else ""))
                open_[e.id] = _record(e, now, rec.get("first_missed"), sends)
            else:
                rec["evidence"] = e.evidence
                rec["severity"] = rec.get("severity", e.severity)
            continue
        if not rec:
            continue
        if e.status == "unknown":
            continue                                    # blind: keep it open, say nothing
        if e.status == "closed":
            notices.append(Notice("closed", e.id, e.meet, e.expected, e.evidence, e.severity,
                                  e.deadline, rec.get("first_missed"), rec.get("sends", 1),
                                  reason=e.evidence))
        else:                                           # met / pending / n/a / info
            notices.append(Notice("resolved", e.id, e.meet, e.expected, e.evidence,
                                  rec.get("severity", e.severity), e.deadline,
                                  rec.get("first_missed"), rec.get("sends", 1)))
        del open_[e.id]

    if not db_blind:
        for eid in [k for k in open_ if k not in seen]:
            rec = open_.pop(eid)
            notices.append(Notice("closed", eid, rec.get("meet"), rec.get("expected", ""),
                                  rec.get("evidence", ""), rec.get("severity", "warn"),
                                  rec.get("deadline"), rec.get("first_missed"),
                                  rec.get("sends", 1),
                                  reason="no longer checked (the meet finished or left the "
                                         "monitor's window)"))
    order = {"new": 0, "reminder": 1, "resolved": 2, "closed": 3}
    notices.sort(key=lambda n: (not n.id.startswith("db:"), order[n.kind],
                                -SEVERITY_RANK.get(n.severity, 0), n.id))
    return notices, {"open": open_}


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

_TITLE = {
    "db": "database not answering",
    "db:reads": "some database reads failing",
    "startlist": "start list missing",
    "writer": "live writer not running",
    "first_results": "no results since racing should have started",
    "results": "results missing",
    "schedule": "events behind the schedule",
}


def _kind_of(eid: str) -> str:
    head = eid.split(":", 1)[0]
    return {"first": "first_results"}.get(head, head)


def _ago(now: float, then: float | None) -> str:
    if then is None:
        return ""
    m = int(max(0, now - then) // 60)
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m} min"


def render(notices: list[Notice], now: float) -> str:
    tz = _zone("Europe/Lisbon")
    stamp = datetime.fromtimestamp(now, tz=tz).strftime("%a %d %b %H:%M Lisbon")
    lines = [f"MeetTrack monitor, {stamp}"]
    for n in notices:
        what = _TITLE.get(n.id, _TITLE.get(_kind_of(n.id), _kind_of(n.id)))
        who = f"{n.meet}: " if n.meet else ""
        sev = "CRIT" if n.severity == "crit" else "WARN"
        if n.kind == "new":
            due = ""
            if n.deadline is not None and _kind_of(n.id) != "db":
                due = f" (due {datetime.fromtimestamp(n.deadline, tz=tz).strftime('%H:%M')})"
            lines += ["", f"[{sev}] NEW {who}{what}",
                      f"  Expected: {n.expected}{due}.",
                      f"  We see: {n.evidence}."]
        elif n.kind == "reminder":
            extra = f", {n.reason}" if n.reason else ""
            lines += ["", f"[{sev}] STILL MISSING {who}{what} "
                          f"(for {_ago(now, n.first_missed)}, reminder {n.sends - 1}{extra})",
                      f"  We see: {n.evidence}."]
        elif n.kind == "resolved":
            lines += ["", f"[OK] RESOLVED {who}{what} after {_ago(now, n.first_missed)}",
                      f"  Now: {n.evidence}."]
        else:
            lines += ["", f"[--] CLOSED {who}{what} (open {_ago(now, n.first_missed)})",
                      f"  {n.reason[:1].upper() + n.reason[1:] if n.reason else ''}.",
                      f"  Last seen: {n.evidence}."]
        if n.investigation:
            if n.investigation.get("summary"):
                lines.append(f"  Checked: {n.investigation['summary']}")
            if n.investigation.get("path"):
                lines.append(f"  Details: {n.investigation['path']}")
    return "\n".join(lines)
