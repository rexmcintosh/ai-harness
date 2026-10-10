# watchdog — autonomous SRE for the mesh

Watches the automation system's *own* moving parts (bebop, MeetTrack, disk,
key services). When a signal is anomalous, a read-only agent investigates and posts
a **diagnosis + proposed fix** to Telegram. It never fixes production — it puts a fix
on a silver platter and lets Rex decide. (Maps to the "autonomous SRE" idea from
Naval's *AI Industrial Revolution* — minus the auto-remediation, by design.)

## Cheap-first

A pure-Python pre-check (`watchdog/run.py`) collects signals and triages them with
**flap suppression** — no tokens are spent on a healthy poll. Only when something
*fires* does the shell invoke the investigator agent.

## Run

```bash
./watchdog/run-watchdog.sh --dry-run   # collect + triage, print what WOULD escalate
./watchdog/run-watchdog.sh             # …and actually investigate + notify on escalation
```

## What it checks (v1)

| Check | Signal | Fires when |
|---|---|---|
| `bebop` | `bebop/logs/runs.log` | last run failed (crit), or no run in >14h (warn) |
| `disk` | `df -P /` | ≥95% crit, ≥85% warn |
| `svc:tailscaled` | `systemctl is-active` | not `active` (crit) |
| `cron:*` | MeetTrack logs | error markers in the recent tail (warn) |
| `cron-logs:coverage` | which configured cron logs were readable | none of them is readable, so the log check is blind (warn) |
| `proc:orphans` | `ps -eo pid,ppid,etime,args` | a `codex` process has PPID 1 and ≥6h elapsed (warn) |
| `meets.freshness` | `meet_registry` (racing hours, Lisbon) | a meet `failed` in 24h (crit); a live writer's `last_ingest_at` ≥30/75 min old while a published event has no results (warn/crit); events missing results or tick errors (warn); a live meet unlaunched ≥30 min (warn) |
| `meets.liveness` | `meet_registry.coverage_at` (racing hours) | a live PDF writer's last tick ≥10/30 min ago (warn/crit); a tick stamp >5 min in the future, a clock or data fault (warn) |
| `meets.clock` | `zoneinfo` `Europe/Lisbon` | the zone cannot be resolved, so the meet rules read racing hours and dates as UTC (warn; also a line in `logs/runs.log.err`) |

Error-marker matching ignores `key=value` counters (e.g. `failed=0`), empty JSON counters
(`"failed": 0`, `"error": null`) and JSON per-item data lists (`"quarantined_items": [...]`,
`"articles": [...]`) so metric lines and standing item notes don't read as failures. A
missing log is skipped (the job may be paused); the coverage check names what was missing.

## Jev shadow mode (shadow only)

`jev_shadow.py` asks Jev (TypeSafe's typed judge, pinned to `jev-1.13.0`) the question
the regex answers: does this log tail show a failure a person should look at? Each
answer is appended to `logs/jev-shadow.jsonl` beside the regex's verdict. **Nothing reads
that file back.** No status, alert or escalation depends on it, it runs after the poll's
result is emitted, it is skipped on `--dry-run`, and any failure in it is swallowed.

- Turn off: `enabled = false` under `[jev_shadow]` in `monitors.toml`, or
  `WATCHDOG_JEV_SHADOW=0`. With no `TYPESAFE_API_KEY` (environment or `~/.env`) it does nothing.
- What is sent: the last 50 lines, redacted (emails, customer rows, mail subjects, JSON
  item lists, URL queries, token-like strings, social handles). Only the redacted tail is stored.
- Which logs: the alerting `CRON_LOGS` plus the shadow-only list in `monitors.toml`.
  Data scope is public and operations data only. Do not add a log that carries
  customer, student, personal-email or financial detail.
- A tail is judged once, when it changes. One pass is capped at 20 seconds.
- Compare the two: `python3 -m watchdog.jev_shadow report`. Bands: under 0.3 ok, 0.7 and
  over alert, between them gray. Those lines came from one replay of 116 real tails
  (`docs/jev-assessment-2026-09-18.md`, Test 4) and are what the shadow data is for re-checking.

## Spike monitoring (rate/volume, not just failures)

The failure checks above answer *"did something error?"*. The spike monitor answers
*"is something doing far more than normal?"* — the gap that let a weekend splash_poller
restart-storm burn through Supabase writes while every poll still read "all ok".
Config lives in [`monitors.toml`](monitors.toml) (data, not code; no secrets):

| Layer | Watches | Fires when |
|---|---|---|
| **1 — log counters** | `relaunched`/`launched` (supervise), `ingested` (ingest), summed over the recent window | value ≥ budget |
| **1 — processes** | live `poller.py` count (`ps`) | count ≥ budget |
| **2 — Supabase** | row counts of hot tables (`results`,`splits`,`swimmers`) via read-only PostgREST | rows/hour ≥ budget |

Detection uses **hard budgets** (no cold-start, no tuning). Every metric value is logged
on **every poll** (`metrics:` lines in `logs/runs.log`) so you can calibrate the budgets
and add statistical thresholds later. Supabase needs `SUPABASE_SERVICE_ROLE_KEY` in the
environment — the runner sources it from `WATCHDOG_ENV_FILE` (default
`/home/dev/projects/splash_poller/.env`) **inside a subshell**, so the investigator agent
never inherits the secret.

### Layer 3 — Supabase spend cap (manual, do once)

The watchdog is the in-system tripwire; the provider-side cap is the seatbelt that stops
spend even if the VPS is down. In the Supabase dashboard for project
**`moaagxigxjyuuqygoqfg`**: *Organization → Billing → Cost Control* — set a **spend cap**,
and *Project → Settings → Billing* — add a **usage alert** email. This isn't code; it's a
two-minute config that backstops everything above.

## Flap suppression

`triage()` re-alerts a problem only when it's **new**, has **worsened**, or the
**cooldown** (6h) has elapsed since the last alert. Recovered checks drop from state.
Nothing is silently dropped — every poll appends to `logs/runs.log`.

## Delivery evidence

The pre-check stages `watchdog/delivery-pending.json` before alert delivery.
Only a valid `tg-send` provider receipt moves the proposed cooldown state into
`state.json`. A definite rejection remains retryable on the next 30-minute poll.
A timeout or other uncertain send remains visible and is not sent again
automatically. The last accepted provider receipt is retained in
`watchdog/delivery-last.json`. Provider acceptance is not human receipt.

## MeetTrack "expected but missing" monitor

`run-expect.sh` (cron, every 5 min) runs `expect_run.py`: it states what should
have happened on each live Portugal meet (start list by T-24h, writer ticking,
results flowing, every published event in MeetTrack within 20 min, the database
answering) and alerts directly on Telegram when it did not, with reminders every
30/60 min, a RESOLVED line, and a deterministic investigation written to
`logs/investigations/`. `./watchdog/run-expect.sh --dry-run` prints today's
expectations read-only. Design, alert policy and how to add an expectation:
[`docs/meettrack-expectations.md`](../docs/meettrack-expectations.md).

While it runs, this watchdog's own meet rules stand down (`defer_to_expect`);
if it stops for 15 minutes they take over and `meets.expect-heartbeat` warns.
Meet alerts here repeat after 1 hour, not 6. An unreadable registry is a crit
(`meets.registry`), never a silent skip.

## Boundary

The investigator agent runs with `--allowedTools Read` only. The wrapper sends
its result through `bin/tg-send`; the agent has no send tool.

**MeetTrack alerts do not wait on the model.** When a `meets.*` check fires, or the
pre-check itself fails, the wrapper sends the plain report straight through
`bin/tg-send` and records that receipt as the delivery. The wrapper then releases
`logs/run.lock`, so the next watchdog run is never held up by the narrative. The
investigator runs under its own `logs/narrative.lock` (a second narrative is skipped
while one still runs), capped by `WATCHDOG_CLAUDE_TIMEOUT` (default 600 s) plus a
30 s kill grace, and its narrative follows as a second message, best effort:
`narrative=sent|send-failed|unavailable|skipped` in `logs/runs.log`.
Every other alert keeps the investigator-first path. (On 3-5 Oct 2026 every
investigator run hit the weekly usage limit, so no alert left the box all weekend.)

The investigator only runs under coreutils `timeout(1)` with `--kill-after`; the
wrapper test-runs exactly that invocation first. When `timeout` is missing or does not
support it, the investigator never runs: every alert, MeetTrack or not, goes out on
the direct path (`investigator=skipped no usable timeout(1)` in `logs/runs.log`), with
no narrative.

Every alert send is capped too: `bin/tg-send` keeps its own 30 s request budget, and
the wrapper wraps it in `timeout` (`WATCHDOG_SEND_TIMEOUT`, default 60 s). A capped
send may already have been accepted, so it is recorded `uncertain` and never replayed.

## Architecture

- `triage.py` — failure checks: pure functions over collected text → `CheckStatus`.
  Unit-tested (`tests/test_watchdog_triage.py`).
- `metrics.py` — spike checks: pure rate/budget functions → `CheckStatus`. Unit-tested
  (`tests/test_watchdog_metrics.py`).
- `monitors.toml` — spike-monitor config (log counters, processes, Supabase tables + budgets).
- `run.py` — collects real signals, loads/saves `state.json` + `metrics-history.json`,
  triages, emits the report + `WATCHDOG_JSON:`/`WATCHDOG_METRICS:` lines. Plumbing tested
  in `tests/test_watchdog_run.py`.
- `run-watchdog.sh` — cron entry; parses the pre-check, escalates to the agent.
- `prompts/investigate.md` — the read-only investigator prompt.

## Activate after review

No runtime was changed by this repair. After the reviewed commit reaches the
main checkout, run `pytest -q tests/test_watchdog_delivery.py tests/test_watchdog_retry.py tests/test_watchdog_wrapper.py`
and `watchdog/run-watchdog.sh --dry-run`. The existing 30-minute cron entry then
uses the repaired wrapper automatically. The dry run does not send or commit
notification suppression.

The wrapper owns one file lock across precheck, investigation and delivery. It persists an `attempting` delivery before calling Telegram; interruption requires inspection, not automatic replay. An accepted receipt is saved before cooldown state, and a later interrupted local commit resumes from that receipt without sending again. `--dry-run` passes through to the precheck and does not stage pending delivery, cooldown or metric state. The wrapper may still append its diagnostic run log. State writes sync the file and directory.
