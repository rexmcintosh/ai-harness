"""Second live test: three harness decisions, made-up inputs built around the cases
where today's regex or router is known to be weak. Each case has an expected answer."""
import json
import os
import re
import time

from test_run import URL, load_key  # noqa: F401
import urllib.error
import urllib.request


def ask(key, state, questions):
    body = json.dumps({"state": state, "model": "jev-latest", "questions": questions}).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "User-Agent": "jev-test/0.1"})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        data = {"error": err.code, "detail": err.read().decode()[:500]}
    return data, time.perf_counter() - start


# ---------- A. watchdog: does this log tail show a real failure? ----------
ERROR_RE = re.compile(r"traceback|exception|\b(?:error|failed|critical)\b(?!=)", re.IGNORECASE)
LOGS = [
    ("clean-counters", False,
     "2026-09-18 02:00:01 absorb start\n2026-09-18 02:03:44 sessions=12 learnings=31 failed=0 error=0\n2026-09-18 02:03:44 absorb done rc=0"),
    ("recovered-transient", False,
     "2026-09-18 02:00:01 ingest start\n2026-09-18 02:00:09 WARN fetch attempt 1 failed: connection reset, retrying\n2026-09-18 02:00:14 fetch ok on attempt 2\n2026-09-18 02:01:30 ingested 48 results\n2026-09-18 02:01:30 done rc=0"),
    ("real-traceback", True,
     "2026-09-18 02:00:01 ingest start\nTraceback (most recent call last):\n  File \"ingest.py\", line 88, in main\n    rows = parse(page)\nKeyError: 'heats'\n2026-09-18 02:00:03 exit rc=1"),
    ("silent-failure", True,
     "2026-09-18 02:00:01 supervise start\n2026-09-18 02:00:02 could not reach results server, giving up after 5 tries\n2026-09-18 02:00:02 nothing ingested tonight\n2026-09-18 02:00:02 exit rc=2"),
    ("error-word-in-data", False,
     "2026-09-18 02:00:01 absorb start\n2026-09-18 02:02:10 wrote article 'Common Python error handling patterns'\n2026-09-18 02:02:11 wrote article 'Why the deploy failed in July (resolved)'\n2026-09-18 02:02:12 absorb done rc=0"),
]
LOG_Q = {
    "needs_human": {
        "type": "noul",
        "instructions": "Does this scheduled-job log tail show that the job ended in a failure that a person should look at?",
        "criteria": {
            "true": "The job crashed, gave up, exited with a non-zero code, or did not complete its work",
            "false": "The job completed its work. Counters equal to zero, errors that a retry fixed, and error words that appear only inside titles or data do not count as failures",
        },
    }
}

# ---------- B. agents: what state is this Claude Code tmux pane in? ----------
PANES = [
    ("permission-modal", "BLOCKED",
     "● I need to delete the old build folder.\n\n Bash command\n   rm -r dist/\n   Remove the old build output\n\n Do you want to proceed?\n ❯ 1. Yes\n   2. Yes, and don't ask again for rm commands\n   3. No, and tell Claude what to do differently (esc)"),
    ("in-flight", "WORKING",
     "● Running the test suite now.\n\n● Bash(npm test)\n  ⎿  Running…\n\n✻ Percolating… (42s · ↓ 1.2k tokens · esc to interrupt)\n\n❯ \n  ? for shortcuts"),
    ("finished-turn", "WAITING",
     "● The fix is in. All 14 tests pass.\n\n  Tell me if you want the same change in the evening prompt.\n\n✻ Baked for 3m 12s\n\n❯ \n  ? for shortcuts"),
    ("question-no-status-line", "WAITING",
     "● I found two ways to do this.\n\n  - Option A: keep the old table and add a column.\n  - Option B: make a new table.\n\n  I recommend Option A. Which one do you want?\n\n❯ \n  ? for shortcuts"),
    ("fresh-empty", "IDLE",
     "╭──────────────────────────────╮\n│ ✻ Welcome to Claude Code!    │\n│   cwd: /home/dev/projects    │\n╰──────────────────────────────╯\n\n❯ \n  ? for shortcuts"),
    ("new-modal-wording", "BLOCKED",
     "● I want to fetch the docs page.\n\n Fetch\n   https://example.com/docs\n\n Allow this fetch?\n ❯ 1. Allow once\n   2. Always allow for example.com\n   3. Deny"),
]


def regex_classify(tail):
    if re.search(r"Esc to cancel|Enter to select|Do you want to (proceed|create|make|run|apply)", tail):
        return "BLOCKED"
    if "esc to interrupt" in tail:
        return "WORKING"
    if re.search(r'^[^A-Za-z]*[A-Z][a-z]+ for [0-9]+m [0-9]+s$|^[^A-Za-z]*[A-Z][a-z]+ for [0-9]+s$|How is Claude doing this session|new task\? /clear|say "do it"', tail, re.M):
        return "WAITING"
    return "IDLE"


PANE_Q = {
    "state": {
        "type": "choice",
        "instructions": "This is the visible text of a terminal running an AI coding agent. What state is the agent in right now?",
        "criteria": {
            "BLOCKED": "A permission or selection menu is on screen and the agent cannot continue until the user picks an option",
            "WORKING": "The agent is in the middle of a task right now; a progress line or 'esc to interrupt' is visible",
            "WAITING": "The agent finished its turn and said something or asked something; the user's reply is needed",
            "IDLE": "Empty prompt. The agent has said nothing that needs a reply and no task is running",
        },
    }
}

# ---------- C. council router: which review panel fits this input? ----------
PANELS = {
    "code-review": "Review a code change for correctness, security, and design.",
    "decision": "Weigh a choice or trade-off and recommend a direction.",
    "brainstorm": "Generate and pressure-test ideas / explore a problem space.",
    "red-team": "Adversarially try to break a plan, claim, or design.",
    "spec-review": "Review a design doc / spec / plan for clarity, soundness, and buildability.",
}
ASKS = [
    ("diff", "code-review",
     "diff --git a/src/webhook.ts b/src/webhook.ts\n@@ -41,6 +41,9 @@\n-  const sig = req.headers['stripe-signature']\n+  const sig = req.headers.get('stripe-signature')\n+  if (!sig) return new Response('missing signature', { status: 400 })"),
    ("tradeoff", "decision",
     "Should I move the season database off the free tier now, or wait until it pauses once and see how bad it is? Cost is $25 a month."),
    ("ideas", "brainstorm",
     "What are some ways a swim results app could make parents open it every week, not only on meet days?"),
    ("break-it", "red-team",
     "Here is my plan for letting a bot approve permission prompts from my phone. Find every way this goes wrong."),
    ("spec", "spec-review",
     "# Design: nightly backlog runner v2\n## Goals\nPick two items, run each in a worktree, hold anything outward-facing.\n## Non-goals\n...\n## Open questions\n..."),
    ("ambiguous", None,
     "Thoughts on the new teaser pipeline?"),
]
ROUTE_Q = {"panel": {"type": "choice",
                     "instructions": "Which review panel is the single best fit for this input?",
                     "criteria": PANELS}}


def main():
    key = load_key()
    out = {"logs": [], "panes": [], "router": []}
    times = []

    print("=== A. watchdog log tails (regex today vs Jev)")
    for name, expect, text in LOGS:
        rx = any(ERROR_RE.search(ln) for ln in text.splitlines())
        data, secs = ask(key, text, LOG_Q)
        times.append(secs)
        p = data["answers"]["needs_human"]["noul"] if "answers" in data else None
        out["logs"].append({"case": name, "expect": expect, "regex": rx, "jev": p, "raw": data})
        print(f"{name:22s} expect={str(expect):5s} regex={str(rx):5s} jev={p if p is None else round(p, 2)}  ({secs:.2f}s)")

    print("\n=== B. tmux pane state (regex today vs Jev)")
    for name, expect, text in PANES:
        rx = regex_classify(text)
        data, secs = ask(key, text, PANE_Q)
        times.append(secs)
        a = data.get("answers", {}).get("state", {})
        out["panes"].append({"case": name, "expect": expect, "regex": rx, "jev": a, "raw": data})
        print(f"{name:24s} expect={expect:8s} regex={rx:8s} jev={a.get('choice')} conf={a.get('confidence', 0):.2f}  ({secs:.2f}s)")

    print("\n=== C. council router")
    for name, expect, text in ASKS:
        data, secs = ask(key, text, ROUTE_Q)
        times.append(secs)
        a = data.get("answers", {}).get("panel", {})
        probs = {k: round(v, 2) for k, v in a.get("probabilities", {}).items()}
        out["router"].append({"case": name, "expect": expect, "jev": a, "raw": data})
        print(f"{name:10s} expect={str(expect):12s} jev={a.get('choice')} conf={a.get('confidence', 0):.2f} probs={probs}  ({secs:.2f}s)")

    times.sort()
    print(f"\ncalls={len(times)} median={times[len(times)//2]:.2f}s max={times[-1]:.2f}s")
    tok = sum(r["raw"].get("usage", {}).get("input_tokens", 0) for grp in out.values() for r in grp)
    print(f"input tokens total={tok}  cost=${tok * 0.042 / 1e6:.6f}")
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_run2_results.json"), "w") as fh:
        json.dump(out, fh, indent=2)


if __name__ == "__main__":
    main()
