"""Build a replay set for the watchdog log check from REAL cron logs.

Each sample is a 50-line window, because watchdog.check_cron_log reads the last 50 lines.
Windows are redacted before anything leaves this machine: emails, customer rows, wiki
article names, long token-like strings, URL query strings.
Sampling is stratified: about half the windows are ones where today's regex fires."""
import json
import os
import random
import re

P = os.path.expanduser
LOGS = {  # label -> (path, max windows)
    "loom-runs": (P("~/projects/ai-harness/loom/logs/runs.log"), 3),
    "loom-runs-err": (P("~/projects/ai-harness/loom/logs/runs.log.err"), 12),
    "meettrack-supervise": (P("~/projects/splash_poller/logs/supervise.cron.log.paused-20260815"), 16),
    "diem-drain": (P("~/.local/state/diem/drain.log"), 16),
    "watchdog-cron": (P("~/projects/ai-harness/watchdog/logs/cron.log"), 12),
    "backlog-run": (P("~/projects/.backlog-run/cron.log"), 7),
    "writing-engine-morning": (P("~/projects/.session-gc/writing-engine-morning.log"), 10),
    "swimtrack-engine-morning": (P("~/projects/.session-gc/swimtrack-engine-morning.log"), 10),
    "writing-engine-poller": (P("~/projects/.session-gc/writing-engine-poller.log"), 8),
    "ops-tick": (P("~/.local/state/ops/tick.log"), 8),
    "engage-scan": (P("~/.local/state/engage/scan.log"), 10),
    "bento-sync": (P("~/projects/sat-prep/tmp/bento-sync.log"), 12),
}
ERROR_RE = re.compile(r"traceback|exception|\b(?:error|failed|critical)\b(?!=)", re.IGNORECASE)  # watchdog/triage.py
WINDOW = 50

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
CUSTOMER_ROW = re.compile(r"^\s*\S+@\S+\s*\|")             # bento-sync family rows
ARTICLES = re.compile(r'"(articles|rejected_items|quarantined_items|deferred_items)":\s*\[.*?\](?=[,}]|$)')
TOKENISH = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")
URLQ = re.compile(r"(https?://[^\s?\"']+)\?[^\s\"']+")
HANDLE = re.compile(r"@[A-Za-z0-9_.]{3,}")


def redact(line: str) -> str:
    if CUSTOMER_ROW.search(line):
        return "  <customer row removed>"
    if re.match(r"\s*(mailed|would mail|skipped)\b", line):   # sat-prep: subject lines carry student names
        return re.sub(r":.*$", ": <subject removed>", EMAIL.sub("<email>", line))[:300]
    line = ARTICLES.sub(r'"\1": "<list removed>"', line)
    if '"articles"' in line or '_items"' in line:          # unterminated list on a long line
        line = re.sub(r'"(articles|\w+_items)":.*$', r'"\1": "<list removed>"', line)
    line = EMAIL.sub("<email>", line)
    line = URLQ.sub(r"\1?<query removed>", line)
    line = TOKENISH.sub("<token>", line)
    line = HANDLE.sub("@<handle>", line)
    return line[:300]


def main():
    rng = random.Random(20260918)
    out = []
    for label, (path, cap) in LOGS.items():
        with open(path, errors="replace") as fh:
            lines = fh.read().splitlines()
        wins = [lines[i:i + WINDOW] for i in range(0, len(lines), WINDOW)]
        wins = [w for w in wins if len(w) >= 10]
        fired = [w for w in wins if any(ERROR_RE.search(ln) for ln in w)]
        quiet = [w for w in wins if not any(ERROR_RE.search(ln) for ln in w)]
        rng.shuffle(fired); rng.shuffle(quiet)
        half = cap // 2
        pick = fired[:cap - min(half, len(quiet))] + quiet[:half]
        pick = pick[:cap]
        for n, w in enumerate(pick):
            red = []
            for ln in (redact(x) for x in w):          # collapse runs of removed rows: they are our artifact, not log content
                if ln == "  <customer row removed>" and red and red[-1].startswith("  <customer row"):
                    continue
                red.append(ln)
            out.append({
                "id": f"{label}-{n:02d}",
                "source": label,
                "regex_fires": any(ERROR_RE.search(ln) for ln in w),   # on the raw lines, as production does
                "text": "\n".join(red),
            })
        print(f"{label:26s} windows={len(wins):5d} fired={len(fired):5d} quiet={len(quiet):5d} picked={len(pick)}")
    rng.shuffle(out)
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "windows.jsonl"), "w") as fh:
        for row in out:
            fh.write(json.dumps(row) + "\n")
    print("total", len(out), "regex fires on", sum(r["regex_fires"] for r in out))


if __name__ == "__main__":
    main()
