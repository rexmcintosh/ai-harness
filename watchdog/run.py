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
import subprocess
import sys
import time
from pathlib import Path

import tomllib

from .triage import (
    CheckStatus,
    LEVELS,
    check_bebop_runs,
    check_cron_log,
    check_disk,
    check_log_coverage,
    check_meet_freshness,
    check_orphan_processes,
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
    ("loom", BASE / "loom" / "logs" / "runs.log"),   # what loom/run-absorb.sh writes
    ("meettrack-ingest", Path("/home/dev/projects/splash_poller/logs/ingest_entries.cron.log")),
    ("meettrack-supervise", Path("/home/dev/projects/splash_poller/logs/supervise.cron.log")),
]
SERVICES = ["tailscaled"]


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
    """Read-only row count via PostgREST (Prefer: count=exact). None on any failure —
    a count we can't read must not crash or fake an alert."""
    try:
        import requests
        r = requests.head(f"{url}/rest/v1/{table}?select=*",
                          headers={"apikey": key, "Authorization": f"Bearer {key}",
                                   "Prefer": "count=exact", "Range": "0-0"},
                          timeout=15)
        return parse_count_header(r.headers.get("Content-Range"))
    except Exception:  # noqa: BLE001 — network/parse error -> skip this metric
        return None


def _supabase_rows(url: str, key: str, path: str) -> list | None:
    """Read-only PostgREST GET returning rows. None on any failure — a read we
    can't make must not crash a poll or fake an alert."""
    try:
        import requests
        r = requests.get(f"{url}/rest/v1/{path}",
                         headers={"apikey": key, "Authorization": f"Bearer {key}"},
                         timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:  # noqa: BLE001 — network/parse error -> skip this check
        return None


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
    if mf and sb:
        key = os.environ.get(sb.get("key_env", "SUPABASE_SERVICE_ROLE_KEY"), "")
        if key:
            from datetime import datetime, timedelta, timezone as _tz
            # 'Z' suffix, never '+00:00' — a '+' in a query string is a space.
            since = (datetime.now(_tz.utc) - timedelta(days=14)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            rows = _supabase_rows(
                sb["url"], key,
                "meet_registry?select=sr_meet_id,name,ingest_status,"
                "last_ingest_at,updated_at,start_date,end_date"
                f"&updated_at=gt.{since}")
            if rows is not None:
                out.append(check_meet_freshness(
                    rows, now_epoch,
                    stale_warn_min=mf.get("stale_warn_min", 20),
                    stale_crit_min=mf.get("stale_crit_min", 75),
                    launch_overdue_min=mf.get("launch_overdue_min", 30)))

    return out, new_metrics


def collect(now_epoch: int, prior_metrics: dict | None = None) -> tuple[list[CheckStatus], dict]:
    """Gather every signal into normalized statuses. Returns (statuses, new_metrics).
    Missing optional signals are skipped (not invented as failures)."""
    out: list[CheckStatus] = []

    bebop_log = _read(BASE / "bebop" / "logs" / "runs.log")
    if bebop_log is not None:
        out.append(check_bebop_runs(bebop_log, now_epoch))

    out.append(check_disk(_cmd(["df", "-P", "/"])))

    for unit in SERVICES:
        out.append(check_service_active(unit, _cmd(["systemctl", "is-active", unit])))

    out.append(check_orphan_processes(_cmd(["ps", "-eo", "pid,ppid,etime,args"])))

    found, missing = [], []
    for label, path in CRON_LOGS:
        text = _read(path)
        if text is None:  # absent log = job may be paused or not installed here; skip
            missing.append(label)
            continue
        found.append(label)
        out.append(check_cron_log(label, text))
    out.append(check_log_coverage(found, missing))

    metric_statuses, new_metrics = collect_metrics(now_epoch, prior_metrics or {})
    out.extend(metric_statuses)
    return out, new_metrics


def shadow_logs(monitors: dict) -> list[tuple[str, str, str]]:
    """(label, text, the regex rule's level) for every readable log Jev shadows: the
    alerting CRON_LOGS plus the shadow-only extras in monitors.toml. The extras get
    the rule's verdict for comparison only; they never become a status."""
    extra = [(item.get("name"), Path(item.get("log", "")))
             for item in (monitors.get("jev_shadow") or {}).get("logs", [])]
    out = []
    for label, path in [*CRON_LOGS, *extra]:
        text = _read(path)
        if label and text is not None:
            out.append((label, text, check_cron_log(label, text).level))
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
        log_dir = Path(os.environ.get("WATCHDOG_LOG_DIR", str(BASE / "watchdog" / "logs")))
        jev_shadow.shadow_pass(shadow_logs(monitors), now_epoch=now_epoch,
                               key=jev_shadow.load_key(),
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
