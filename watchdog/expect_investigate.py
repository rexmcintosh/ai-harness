"""Bounded, deterministic investigation of a missed MeetTrack expectation.

Called by the runner for each NEW alert (at most a few per run). Every probe
is injected (``Probes``) so tests use fakes, and each one is wrapped so a
failing or slow probe costs only its own line, never the alert. The whole pass
stops starting new probes after ``budget_s`` seconds.

What it looks at, cheapest first:
1. the database: did the tiny probe answer, and how fast
2. the writer process for the meet (``ps``)
3. the meet's poller log: last lines, error types, the last published-list line
4. swimrankings: how many result PDFs the live page lists, against what we hold
5. per-event counts from the snapshot: start-list entries against results

The findings go to ``logs/investigations/<UTC stamp>-<id>.md``; the alert
carries a one-line summary and the file path. The optional AI narrative
(expect_run --narrate) reads that file later, outside the run lock.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .expectations import Expectation, Snapshot, _zone, writer_lines

_ERROR_TYPES = [
    ("ReadTimeout", re.compile(r"ReadTimeout|read timed out", re.I)),
    ("ConnectTimeout", re.compile(r"ConnectTimeout|ConnectionError|connection (?:refused|reset)", re.I)),
    ("HTTP 5xx", re.compile(r"\b(?:5\d\d)\b.*(?:error|server|gateway)|(?:error|status)[^\n]{0,20}\b5\d\d\b", re.I)),
    ("db unavailable", re.compile(r"db unavailable", re.I)),
    ("Traceback", re.compile(r"Traceback \(most recent call last\)")),
    ("parse error", re.compile(r"parse (?:error|fail)|could not parse|cannot read", re.I)),
]
_TICK = re.compile(r"\[tick\][^\n]*errors=(\d+)")
_PUBLISHED = re.compile(r"Published ResultList PDFs: events \[([^\]]*)\]")
_RESULTLIST = re.compile(r"ResultList_(\d+)\.pdf", re.I)


@dataclass
class Probes:
    """Injected I/O. Each returns text (or raises); the investigation never
    lets an exception escape."""
    ps: Callable[[], str]
    read_log_tail: Callable[[str], str | None]      # sr id -> last ~64 KB, None if absent
    fetch_page: Callable[[str], str]                 # sr id -> live page HTML
    supervise_tail: Callable[[], str | None] = lambda: None
    clock: Callable[[], float] = time.monotonic


def summarize_log(text: str | None, tail_lines: int = 200) -> dict:
    if text is None:
        return {"present": False}
    lines = text.splitlines()[-tail_lines:]
    counts = {}
    for name, rx in _ERROR_TYPES:
        n = sum(1 for ln in lines if rx.search(ln))
        if n:
            counts[name] = n
    tick_errors = [int(m.group(1)) for ln in lines for m in [_TICK.search(ln)] if m]
    published = None
    for ln in reversed(lines):
        m = _PUBLISHED.search(ln)
        if m:
            published = [int(x) for x in re.findall(r"\d+", m.group(1))]
            break
    return {"present": True, "errors": counts, "last_lines": lines[-12:],
            "ticks_seen": len(tick_errors), "ticks_with_errors": sum(1 for x in tick_errors if x),
            "published": published}


def count_result_pdfs(html: str) -> list[int]:
    return sorted({int(n) for n in _RESULTLIST.findall(html or "")})


def investigate(exp: Expectation, snap: Snapshot, probes: Probes, *,
                budget_s: float = 40.0) -> dict:
    """Run the probes for one expectation. Returns
    {"summary": str, "sections": [(title, [lines])], "facts": {...}}."""
    started = probes.clock()
    sections: list[tuple[str, list[str]]] = []
    facts: dict = {}
    sid = exp.sr_meet_id

    def over() -> bool:
        return probes.clock() - started > budget_s

    def run(title, fn):
        if over():
            sections.append((title, ["skipped: investigation time budget used up"]))
            return None
        try:
            return fn()
        except Exception as exc:          # noqa: BLE001 - a probe never costs the alert
            sections.append((title, [f"probe failed: {type(exc).__name__}: {str(exc)[:160]}"]))
            return None

    # 1. database
    db_lines = [f"probe ok: {snap.db_ok}",
                f"latency: {snap.db_latency_s:.2f}s" if snap.db_latency_s is not None else "latency: n/a"]
    if snap.db_error:
        db_lines.append(f"error: {snap.db_error}")
    if snap.registry_error:
        db_lines.append(f"registry read error: {snap.registry_error}")
    sections.append(("Database", db_lines))
    facts["db_ok"] = snap.db_ok

    if exp.kind == "db" or not sid:
        sup = run("Supervisor log", probes.supervise_tail)
        if sup is not None:
            s = summarize_log(sup, tail_lines=60)
            sections.append(("Supervisor log (last 60 lines)",
                             [f"error types: {s.get('errors') or 'none'}"] + s.get("last_lines", [])[-6:]))
            facts["supervisor_errors"] = s.get("errors") or {}
        facts["summary"] = _summary(exp, facts)
        return {"summary": facts["summary"], "sections": sections, "facts": facts}

    # 2. writer process
    def _proc():
        return writer_lines(probes.ps(), sid)
    procs = run("Writer process", _proc)
    if procs is not None:
        sections.append(("Writer process", procs or ["none running"]))
        facts["process_running"] = bool(procs)

    # 3. poller log
    log = run("Poller log", lambda: probes.read_log_tail(sid))
    s = summarize_log(log)
    if s.get("present"):
        lines = [f"error types in the last 200 lines: {s['errors'] or 'none'}",
                 f"ticks seen: {s['ticks_seen']}, with errors: {s['ticks_with_errors']}",
                 f"last published list in the log: {s['published']}",
                 "last lines:"] + [f"  {ln[:200]}" for ln in s["last_lines"]]
        sections.append((f"Poller log poller-{sid}.log", lines))
        facts["log_errors"] = s["errors"]
        facts["log_published"] = s["published"]
    elif log is None and not any(t.startswith("Poller log") for t, _ in sections):
        sections.append((f"Poller log poller-{sid}.log", ["not found"]))

    # 4. swimrankings
    page = run("swimrankings page", lambda: probes.fetch_page(sid))
    if page is not None:
        pdfs = count_result_pdfs(page)
        facts["source_pdfs"] = pdfs
        sections.append(("swimrankings live page",
                         [f"result PDFs listed: {len(pdfs)} (events {pdfs[:30]})"]))

    # 5. per-event counts
    events = snap.events.get(sid, [])
    if events:
        rows = ["event | relay | start-list entries | results"]
        for e in events:
            rows.append(f"{e.get('event_number')} | {'yes' if (e.get('relay_count') or 1) > 1 else 'no'}"
                        f" | {e.get('entries')} | {e.get('results')}")
        sections.append(("MeetTrack per-event counts", rows))
        facts["events_with_results"] = sum(1 for e in events if (e.get("results") or 0) > 0)
        facts["results_total"] = sum(int(e.get("results") or 0) for e in events)
        facts["entries_total"] = sum(int(e.get("entries") or 0) for e in events)
    facts["summary"] = _summary(exp, facts)
    return {"summary": facts["summary"], "sections": sections, "facts": facts}


def _summary(exp: Expectation, f: dict) -> str:
    """One plain-English line: what the probes point at. Never a certainty."""
    bits = []
    if not f.get("db_ok", True):
        bits.append("the database is not answering")
    if exp.kind == "db":
        sup = f.get("supervisor_errors") or {}
        if sup:
            bits.append("supervisor log shows " + ", ".join(f"{n} {k}" for k, n in sup.items()))
        return "; ".join(bits) or "database probe failed; no other signal"
    if "process_running" in f:
        bits.append("writer process running" if f["process_running"] else "no writer process running")
    errs = f.get("log_errors") or {}
    if errs:
        bits.append("poller log: " + ", ".join(f"{n} {k}" for k, n in errs.items()))
    src = f.get("source_pdfs")
    if src is not None:
        held = f.get("events_with_results")
        held_txt = f"; MeetTrack has results for {held}" if held is not None else ""
        bits.append(f"swimrankings lists {len(src)} result PDFs{held_txt}")
    if (exp.kind == "results" and f.get("db_ok", True) and f.get("process_running")
            and not errs and src):
        bits.append("likely a parser problem (files are read but give few or no rows)")
    elif exp.kind in ("writer",) and f.get("process_running") is False:
        bits.append("likely the writer stopped; the supervisor relaunches within 5 min")
    elif exp.kind == "first_results" and src == []:
        bits.append("the host has not published any results yet")
    return "; ".join(bits) or "no clear signal from the probes"


def render_markdown(exp: Expectation, inv: dict, now: float) -> str:
    tz = _zone("Europe/Lisbon")
    stamp = datetime.fromtimestamp(now, tz=tz).strftime("%Y-%m-%d %H:%M %Z")
    out = [f"# Investigation: {exp.id}", "",
           f"- When: {stamp}",
           f"- Meet: {exp.meet or 'system'}",
           f"- Expected: {exp.expected}",
           f"- We see: {exp.evidence}",
           f"- Summary: {inv['summary']}", ""]
    for title, lines in inv["sections"]:
        out.append(f"## {title}")
        out.append("")
        out.extend(f"    {ln}" if not ln.startswith("  ") else f"    {ln.strip()}" for ln in lines)
        out.append("")
    return "\n".join(out)


def safe_name(eid: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", eid).strip("-")


def write_investigation(dir_: Path, exp: Expectation, inv: dict, now: float) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = dir_ / f"{stamp}-{safe_name(exp.id)}.md"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(render_markdown(exp, inv, now))
    tmp.replace(path)
    return path
