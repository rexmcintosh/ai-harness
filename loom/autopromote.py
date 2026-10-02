# loom/autopromote.py
"""The gate that decides whether tonight's promote may run unattended.

Auto-promote exists because the manual gate demonstrably did not happen: between
2026-07-11 and 2026-07-19 nothing was promoted and 131 weave commits piled up,
despite a nightly Telegram message saying so. Reviewing 55 articles before they
land is not a thing anyone does; reading them afterwards in Obsidian is. The trade
is safe only because the wiki is private, git-backed, every promote writes a
backup, and `loom rollback --ts` reverts it.

Two things it must refuse, or the trade stops being safe:
  * a staged `_staged/.claude` swap — that rewrites Rex's live memories/skills,
    the path has never run live, and the blast radius is his agent's behaviour,
    not a private note. Always hand-gated.
  * a hold for tonight — the one-word veto from the briefing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from .promote import _git, _shadow_has_stage

HOLD_FILE = ".hold"

# The nightly promote fires at 02:00 UTC (cron `0 2 * * *`; runs.log shows it as
# 03:00:01+01:00). Its `today` string is the LOCAL date at that instant, which for
# any plausible local offset here equals the UTC date of this boundary.
PROMOTE_UTC_HOUR = 2


def next_promote_date(now: Optional[datetime] = None) -> str:
    """The `today` string the NEXT unattended promote will run with.

    A hold must name the promote it is meant to stop, not the day it was set:
    a veto typed at 07:00 on day D targets the 02:00-UTC run on day D+1, which
    compares against today == D+1. Anything set before that day's boundary
    (00:00-02:00 UTC) still targets the same day's run."""
    # A naive `now` (tests, callers) is interpreted as host-local time —
    # astimezone() does exactly that — then normalized to UTC.
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    boundary = now.replace(hour=PROMOTE_UTC_HOUR, minute=0, second=0, microsecond=0)
    if now >= boundary:                      # today's run already fired -> tomorrow's
        boundary += timedelta(days=1)
    return boundary.strftime("%Y-%m-%d")


def _hold_path(loom_dir: Path) -> Path:
    return Path(loom_dir) / HOLD_FILE


def set_hold(loom_dir: Path, day: str) -> Path:
    """Veto ONE promote. `day` is the target promote's date — callers should pass
    next_promote_date(), never the calendar day the veto was typed (that was the
    off-by-one that left auto-promote with no working brake). Scoped to one run
    on purpose: a hold that persisted silently would recreate the 8-day drift
    this whole design exists to fix."""
    p = _hold_path(loom_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Atomic: a crash mid-write must not leave a truncated .hold, because
    # is_held treats malformed/empty as no-hold — i.e. it would silently
    # disable the exact brake this file exists to be.
    tmp = p.with_suffix(".hold.tmp")
    tmp.write_text(day.strip() + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def clear_hold(loom_dir: Path) -> None:
    _hold_path(loom_dir).unlink(missing_ok=True)


def is_held(loom_dir: Path, day: str) -> bool:
    p = _hold_path(loom_dir)
    try:
        return p.read_text(encoding="utf-8").strip() == day.strip()
    except (FileNotFoundError, OSError):
        return False


def pending_articles(wiki_root: Path) -> List[str]:
    out = _git(Path(wiki_root), "diff", "--name-only", "master..loom-shadow").stdout
    return [f for f in out.split()
            if f.endswith(".md") and not Path(f).name.startswith("_")]


def auto_promote_check(*, wiki_root: Path, loom_dir: Path, today: str) -> dict:
    """Decide whether tonight's promote may run without a human. Never raises —
    a check that blew up would take the nightly run down with it."""
    wiki_root = Path(wiki_root)
    try:
        commits = int(_git(wiki_root, "rev-list", "--count",
                           "master..loom-shadow").stdout.strip() or 0)
        articles = pending_articles(wiki_root)
        staged = list(_shadow_has_stage(wiki_root))
        dirty = bool(_git(wiki_root, "status", "--porcelain").stdout.strip())
    except Exception as e:                      # noqa: BLE001 — degrade, never crash the run
        return {"go": False, "reason": f"check-failed: {type(e).__name__}",
                "commits": 0, "articles": [], "staged": [], "dirty": False}

    base = {"commits": commits, "articles": articles, "staged": staged, "dirty": dirty}
    if is_held(loom_dir, today):
        return {"go": False, "reason": "hold", **base}
    if staged:
        return {"go": False, "reason": "staged-claude", **base}
    if dirty:
        # promote() refuses a dirty tree, and rightly so. Catch it here so an
        # Obsidian note left half-written is a quiet stand-down, not a nightly
        # exception + failure ping.
        return {"go": False, "reason": "wiki-dirty", **base}
    if commits == 0:
        return {"go": False, "reason": "nothing-pending", **base}
    return {"go": True, "reason": "", **base}
