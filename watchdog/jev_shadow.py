"""Jev shadow mode: a second opinion on cron-log tails. SHADOW ONLY.

Jev (TypeSafe's typed judge) is asked the same question the cron-log regex answers:
does this log tail show a failure a person should look at? Its answer is appended to
``watchdog/logs/jev-shadow.jsonl`` next to the regex's answer and NOTHING else happens:
no status, no alert, no escalation reads this file. It exists so the two can be compared
on real polls before anyone decides whether Jev should be trusted with more.

Contract:
  * Never raises into a poll. Any failure means "no record this time".
  * Only the redacted tail leaves the machine, and only the redacted tail is stored.
  * The model version is pinned; a moving alias would shift the numbers under us.
  * A tail is judged once. The watchdog polls every 30 min; most tails have not changed.

Background and the first replay: docs/jev-assessment-2026-09-18.md (Test 4).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import jev
from jev import client as _client
from jev.redact import _EMAIL, _TOKENISH, _URL_QUERY  # noqa: F401  (kept: tools/jev_council imports them)
from jev.redact import redact_log as redact
from jev.scope import OUT_OF_SCOPE, in_scope  # noqa: F401

# One place talks to TypeSafe: jev/client.py. These names are kept for callers and tests
# written against the first version of this module.
URL = jev.URL
MODEL = jev.MODEL
load_key = _client.load_key
_http_post = _client.http_post
_NoRedirect = _client._NoRedirect
_OPENER = _client._OPENER
TAIL_LINES = 50            # same window check_cron_log reads
TIMEOUT_SECONDS = 8
BUDGET_SECONDS = 20        # whole pass; an outage must not stall the poll
OK_BELOW, ALERT_FROM = 0.3, 0.7   # read off the first replay; shadow data exists to re-check them

QUESTIONS = {
    "needs_human": jev.noul(
        "This is a window of lines from a scheduled job's log. "
        "Does it show a job failure that a person should look at?",
        true="A job crashed, gave up, timed out, exited with a non-zero code, could not reach a "
             "service it needs, could not deliver its output, or did not complete its work",
        false="The jobs completed their work. Counters equal to zero (failed=0, \"error\": null), "
              "errors that an automatic retry fixed, error words that appear only inside titles, "
              "story text, file names or data, a job that reports an upstream source was down but "
              "completes normally, and normal skipped, held or deferred outcomes are not failures"),
    "latest_run_failed": jev.noul(
        "Look only at the last run visible at the end of this log window. "
        "Did that last run end in failure?",
        true="The last run crashed, gave up, timed out, or exited with a non-zero code",
        false="The last run completed its work, or the window ends without any sign that the last run failed"),
}


def build_request(tail: str) -> dict:
    return {"state": tail, "model": MODEL, "questions": QUESTIONS}


def band(p: float) -> str:
    return "ok" if p < OK_BELOW else ("alert" if p >= ALERT_FROM else "gray")


def judge(tail: str, key: str, transport=None) -> dict | None:
    """One Jev call through the shared client. None on ANY failure (and when JEV_DISABLED
    is set): no retry here, the next changed tail gets its own try."""
    got = jev.try_ask(tail, QUESTIONS, project="watchdog", task="cron-log-shadow", key=key,
                      timeout=TIMEOUT_SECONDS, retries=0, transport=transport)
    if not got:
        return None
    try:
        return {
            "needs_human": float(got["answers"]["needs_human"]["noul"]),
            "latest_run_failed": float(got["answers"]["latest_run_failed"]["noul"]),
            "model": got["model"], "input_tokens": got["input_tokens"], "seconds": got["seconds"],
        }
    except Exception:  # noqa: BLE001 - shadow mode must never break a poll
        return None


def _load_json(path) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def _save_json(path, data: dict) -> None:
    """Atomic: a killed or failed write leaves the last good state, never half a file
    (a corrupt state would read as empty and re-send every tail)."""
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, target)


def _agree(regex_level: str, jev_band: str) -> bool | None:
    if jev_band == "gray":
        return None
    return (regex_level != "ok") == (jev_band == "alert")


def shadow_pass(logs, *, now_epoch: int, key: str | None, log_path, state_path,
                judge=judge, budget_seconds: float = BUDGET_SECONDS, clock=time.monotonic) -> int:
    """logs: [(label, full log text, regex level)]. Returns how many records were written."""
    if not key:
        return 0
    written = 0
    try:
        seen = _load_json(state_path)
        started = clock()
        for label, text, regex_level in logs:
            if clock() - started > budget_seconds:
                break
            tail = redact("\n".join(text.splitlines()[-TAIL_LINES:]))
            digest = hashlib.sha256(tail.encode()).hexdigest()
            if seen.get(label) == digest:
                continue
            try:
                verdict = judge(tail, key)
            except Exception:  # noqa: BLE001
                verdict = None
            if not verdict:
                continue                       # not marked seen: try again next poll
            p = verdict["needs_human"]
            record = {
                "ts": now_epoch, "log": label, "tail_sha": digest[:16],
                "regex_level": regex_level,
                "jev_needs_human": p, "jev_latest_run_failed": verdict.get("latest_run_failed"),
                "jev_band": band(p), "agree": _agree(regex_level, band(p)),
                "model": verdict.get("model"), "input_tokens": verdict.get("input_tokens"),
                "seconds": verdict.get("seconds"), "tail": tail,
            }
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as handle:
                handle.write(json.dumps(record) + "\n")
            seen[label] = digest
            written += 1
            _save_json(state_path, seen)       # per record: a kill mid-pass re-sends nothing
    except Exception:  # noqa: BLE001
        pass
    return written


def summarize(records: list[dict]) -> dict:
    out = {"records": len(records), "agree": 0, "disagree": 0, "gray": 0,
           "rule_alert_jev_ok": 0, "rule_ok_jev_alert": 0}
    for r in records:
        if r.get("agree") is None:
            out["gray"] += 1
        elif r["agree"]:
            out["agree"] += 1
        else:
            out["disagree"] += 1
            out["rule_ok_jev_alert" if r.get("regex_level") == "ok" else "rule_alert_jev_ok"] += 1
    return out


def main(argv=None) -> int:
    """``python3 -m watchdog.jev_shadow report [PATH]``: how the two have compared so far."""
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] != "report":
        print("usage: python3 -m watchdog.jev_shadow report [PATH]", file=sys.stderr)
        return 2
    path = Path(argv[1]) if len(argv) > 1 else Path(__file__).parent / "logs" / "jev-shadow.jsonl"
    try:
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except OSError:
        print(f"no shadow records yet at {path}")
        return 0
    s = summarize(records)
    print(f"{s['records']} records: agree {s['agree']}, disagree {s['disagree']}, gray band {s['gray']}")
    print(f"  rule alerted, Jev said ok:   {s['rule_alert_jev_ok']}")
    print(f"  rule said ok, Jev alerted:   {s['rule_ok_jev_alert']}")
    for r in records:
        if r.get("agree") is False or r.get("agree") is None:
            when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(r["ts"]))
            print(f"  {when}Z {r['log']:24s} rule={r['regex_level']:4s} jev={r['jev_needs_human']:.2f} ({r['jev_band']}) sha={r['tail_sha']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
