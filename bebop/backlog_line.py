#!/usr/bin/env python3
# bebop/backlog_line.py
"""One line for the morning briefing: what the backlog is waiting on.

`backlog-run` works the queue at 03:00 UTC. Finished work is left `in_review` for Rex to
approve; anything needing a human decision or an outward action is parked `held`. Neither
state pings him, so items sat for weeks. This builds the reminder.

Two design rules, both learned from the loom line above it in `run-briefing.sh`:

  * **Code, not the model.** These are counts Rex acts on. `run-briefing.sh` appends the
    finished string to the composed briefing, after the agent has answered, so the model
    cannot drop it, reword it or round it off.
  * **Silence when nothing is waiting.** An empty string means no line at all. A line that
    turns up every single morning is a line nobody reads by week two.

And one operating rule: **fail open**. A missing file, broken YAML or a shape nobody
expected costs Rex the line, never the briefing. Nothing here raises past `main()`.

Reads the backlog file and nothing else — no email, no calendar, no network, no `jev`.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

BACKLOG_ENV = "BEBOP_BACKLOG_FILE"      # tests point this at their own file
NOW_ENV = "BEBOP_BACKLOG_NOW"           # ISO date; injectable "today" for tests
DEFAULT_BACKLOG = ("projects", "backlog", "backlog.yaml")

TAIL = "Look: backlog-run report"

# An item id is `YYYY-MM-DD-<slug>`; only the slug is worth six words of a briefing.
_ID_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def backlog_path() -> Path:
    """The backlog file: $BEBOP_BACKLOG_FILE, else the shared private repo."""
    override = os.environ.get(BACKLOG_ENV)
    return Path(override).expanduser() if override else Path.home().joinpath(*DEFAULT_BACKLOG)


def utc_today() -> date:
    """Today in UTC, because that is what `backlogrun.cli.today()` stamps `worked` with."""
    override = os.environ.get(NOW_ENV)
    if override:
        try:
            return date.fromisoformat(override)
        except ValueError:
            pass                        # a garbled override must not cost the line
    return datetime.now(timezone.utc).date()


def _as_date(value) -> date | None:
    """A backlog date, whichever way PyYAML handed it over. None when it is not one."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _as_utc(value) -> datetime | None:
    """The runner's `worked_at` stamp (`2026-09-20T22:41:07Z`), or None when it is not one.
    PyYAML may hand it over as a string or as a datetime; a naive one is UTC by contract."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def utc_now(today: date | None = None) -> datetime:
    """Now in UTC. When "today" was injected (tests, replays), the hour the briefing goes out
    on that day, so an injected date and the clock can never disagree."""
    if today is not None and today != datetime.now(timezone.utc).date():
        return datetime(today.year, today.month, today.day, 7, 0, tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


NEW_FOR = timedelta(hours=24)           # one briefing a day: a hold is news exactly once


def _newly_held(it: dict, *, today: date, now: datetime) -> bool:
    # `worked_at` is the runner's UTC timestamp for the hold. With it the answer does not
    # depend on WHEN the runner fires: 03:00 UTC, 22:00 UTC, or a run across midnight are all
    # reported on the next morning and only that one. A stamp that cannot be read falls back
    # to the date rule rather than dropping the item.
    stamp = _as_utc(it.get("worked_at"))
    if stamp is not None:
        return timedelta(0) <= now - stamp < NEW_FOR
    # Records written before `worked_at` existed carry only `worked`, a DATE. That rule is
    # exact only while the runner fires after midnight UTC and before the briefing: then
    # "worked is today" means "held by last night's run". A hold placed by hand
    # (`backlog-run hold`) writes neither field, so it is never counted.
    return _as_date(it.get("worked")) == today


def _age_days(when: date, today: date) -> int:
    # A date in the future is a typo, not a negative age. Clamp rather than print "-3 days".
    return max(0, (today - when).days)


def _age_text(days: int) -> str:
    if days == 0:
        return "today"
    return "1 day" if days == 1 else f"{days} days"


NAME_MAX = 60                           # an id is a filename-ish slug; anything longer is noise


def _short_name(iid: str) -> str:
    # This string ends up in a Telegram message. Whatever the backlog holds, the briefing
    # gets one short line of printable text: control characters and line breaks become
    # spaces, runs of space collapse, and a runaway id is cut.
    name = "".join(ch if ch.isprintable() else " " for ch in _ID_DATE.sub("", iid))
    name = " ".join(name.split())
    return name if len(name) <= NAME_MAX else name[:NAME_MAX - 3].rstrip() + "..."


def briefing_line(items, *, today: date, now: datetime | None = None) -> str:
    """The line, or "" when nothing is waiting. Pure when `now` is given: no file, no env."""
    if not isinstance(items, list):
        return ""
    now = now or utc_now(today)

    review: list[tuple[date | None, str]] = []
    newly_held = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        status = it.get("status")
        iid = it.get("id")
        if not isinstance(status, str) or not isinstance(iid, str) or not iid:
            continue
        if status == "in_review":
            # How long it has waited: since the runner last touched it, else since it was
            # written down. An item with neither still counts; it just cannot be "oldest".
            review.append((_as_date(it.get("worked")) or _as_date(it.get("created")), iid))
        elif status == "held":
            if _newly_held(it, today=today, now=now):
                newly_held += 1

    if not review and not newly_held:
        return ""                       # the silence is the point

    parts = ["Backlog:"]
    if review:
        parts.append(f"{len(review)} {'waits' if len(review) == 1 else 'wait'} for your review")
        dated = sorted((d, i) for d, i in review if d is not None)
        if dated:
            when, iid = dated[0]
            parts[-1] += f" (oldest {_age_text(_age_days(when, today))}: {_short_name(iid)})"
        parts[-1] += "."
    if newly_held:
        parts.append(f"{newly_held} new on hold since yesterday.")
    parts.append(TAIL)
    return " ".join(parts)


def line_from_file(path=None, *, today: date | None = None) -> str:
    """The line for one backlog file. Returns "" for anything it cannot make sense of."""
    try:
        text = Path(path or backlog_path()).read_text(encoding="utf-8")
        doc = yaml.safe_load(text)
        items = doc.get("items") if isinstance(doc, dict) else doc
        return briefing_line(items, today=today or utc_today())
    except Exception:                   # noqa: BLE001 - a broken backlog never costs the briefing
        return ""


def main(argv=None) -> int:
    try:
        sys.stdout.write(line_from_file())
    except Exception:                   # noqa: BLE001 - belt and braces; stdout IS the briefing
        return 0
    return 0                            # never non-zero: the caller must not see a failure


if __name__ == "__main__":
    raise SystemExit(main())
