# MeetTrack "expected but missing" monitor

Status: built 10 Oct 2026 on branch `claude/meettrack-expect-monitor`. Pilot.
Code: `watchdog/expectations.py` (model), `watchdog/expect_alerts.py` (alert
policy), `watchdog/expect_investigate.py` (investigation),
`watchdog/expect_run.py` (I/O and CLI), `watchdog/run-expect.sh` (cron wrapper).
Config: the `[expect]` table in `watchdog/monitors.toml`.
Tests: `tests/test_watchdog_expect.py`, `tests/test_watchdog_meet_cleanups.py`.

## Why

Every older watchdog check fires on "too much": too many errors, too many rows,
a disk too full. MeetTrack's real failures are absences. On 10 Oct 2026 the
database froze for 2 h 21 min during racing. The watchdog could not read the
meet registry, so it skipped its meet checks without saying so, sent one vague
log-noise warning, and muted itself for 6 hours. The same outage on 4 Oct went
unseen. The same afternoon, Silves results reached MeetTrack as 8 rows for 11
published events, and nothing said "Silves is missing results".

This monitor states what should have happened, by when, and checks it every 5
minutes. When something is missing it says so in plain English, investigates,
and keeps saying so until it is fixed.

## Expectations

Each expectation has an id, a deadline, a status (`met`, `missed`, `pending`,
`unknown`, `closed`, `n/a`), a severity (`crit`, `warn`, `info`), a window
(`always` or `racing`), and evidence: what we actually see.

| Id | Expected | Deadline | Severity, window |
|---|---|---|---|
| `db:reachable` | the database answers a tiny query (`meet_registry?select=sr_meet_id&limit=1`, 8 s timeout) | 2 failed runs in a row (about 5-10 min) | crit, always |
| `db:registry` | the meet registry can be read | at once | crit, always |
| `db:reads` | every per-meet read succeeds, every registry id is a meet id | 2 runs with failed reads; a bad id at once | crit (warn for bad ids only), always |
| `startlist:<id>` | a Portugal meet someone requested in the app has its start list (`entries_ingested_at`) | 24 h before racing starts; a late request gets 60 min | warn, always |
| `writer:<id>:<day>` | the live writer runs and ticks (`coverage_at` under 30 min old; or the process exists, for writers that do not stamp ticks) | racing start + 30 min | crit, racing |
| `first:<id>:<day>` | results start appearing today (results written today, or new result files on the host) | stated start + 60 min; else 17:00 Lisbon (morning grace) | warn, racing |
| `results:<id>:<day>` | every event the host has published is in MeetTrack, with results for at least half the swimmers on its start list | 20 min after the gap was first seen | crit, racing |
| `schedule:<id>:<day>` | events past their scheduled slot (`events.day_time`) have results | slot + 45 min, 2 or more events late | warn, racing |
| `source:<id>:<day>` | the host keeps publishing | note only | info |
| `feed:<id>` | a meet racing today has a live results feed | note only | info |
| `hygiene:stale-active` | finished meets are not left `ACTIVE` in `meets` (sr-10162, sr-10279) | note only | info |

Scope and data:

- Portugal only (`nations = ["POR"]`). Times are Lisbon local.
- Live meets come from `meet_registry` (live by date, a live feed type, not
  paused). A meet marked `finished` gets no results expectations, so it stops
  alerting; an open alert on it is closed with one message.
- Stale `ACTIVE` rows in `meets` are listed as a note and otherwise ignored.
- When the writer is down, its results expectations become `unknown`: one
  root cause, one alert.
- A failed per-meet read (events, today's results) makes that meet's results
  expectations `unknown`, never "0 results"; `db:reads` alerts if it lasts.
- While the database is down, every meet expectation is `unknown`. The
  database alert lists the meets that were live at the last good read.
- Start times: `events.day_time` when the start list carries it, else a stated
  time in `[[expect.start_times]]`, else the per-nation default. The default
  is the morning grace: no results are expected before 17:00 when nothing says
  when racing starts. A meet whose host has published result files but whose
  results are missing is caught by `results:*` at any racing hour.
- Start lists are only expected when someone asked for them
  (`entries_requested_at`), because `ingest_entries.py` is demand-triggered.
  `startlist_scope = "all"` expects one for every listed Portugal meet.

## Database use

Per 5-minute run, with three live meets: 1 probe, 1 registry read, 1 `meets`
read, 3 per-event count reads (`events` with embedded `results(count)` and
`heats(count)`, indexed by meet and event), 3 "results written today" counts
(filtered by `meet_id`, indexed), 1 stale-ACTIVE read. About 10 small reads,
under 2 a minute, against the pollers' 47. If the probe fails, nothing else is
read. The 30-minute watchdog's full-table counts are now planner estimates
(`Prefer: count=planned`): no table scans.

## Alert policy

- Direct Telegram through `bin/tg-send`. No model is on the alert path.
- One message per run, holding every change: NEW, STILL MISSING (reminders),
  RESOLVED, CLOSED. Database lines first.
- De-duplicated by expectation id. A NEW alert goes out once; after that only
  reminders.
- Reminders while the problem persists: crit every 30 min, warn every 60 min,
  start lists every 6 h. A severity rise reminds at once.
- RESOLVED once, with how long it lasted, when the expectation is met again.
- Racing-window expectations alert only 08:00-22:00 Lisbon. If one is still
  open at 22:00, one CLOSED line says so and the reminders stop. If it is still
  wrong tomorrow, tomorrow's id alerts anew.
- `always` expectations (database, start lists) alert at any hour.
- An open alert whose expectation is no longer produced (meet finished, left
  the window) is CLOSED with one line. Nothing is closed or resolved while the
  database is down.
- `info` expectations are never sent. They show in dry-runs and investigations.
- A definite send failure keeps the old alert state, so the next run sends the
  same news again. An uncertain send (timeout, tg-send rc 3) counts as sent.
- If the monitor itself crashes or times out, `run-expect.sh` sends "MeetTrack
  monitor could not run" directly, at most once an hour. If it stops running
  altogether, the 30-minute watchdog notices (`meets.expect-heartbeat`, direct
  path) and its own meet rules take over.

## Investigations

For each NEW alert (at most 3 a run, 40 s budget each), the runner probes, in
order: the database probe result; the writer process (`ps`); the meet's poller
log (last 64 KB: error types, tick errors, the last published list); the
swimrankings live page (count of `ResultList_N.pdf` links); per-event start-list
entries against results. Each probe is bounded and a failing probe costs only
its own line. The findings go to
`watchdog/logs/investigations/<UTC stamp>-<id>.md`; the alert carries a one-line
summary ("Checked: ...") and the path ("Details: ...").

After the message is sent, the state saved and the run lock released, the runner
may spawn the AI narrative (`ai_narrative = true`): a detached child under
`timeout`, with no secrets in its environment and its own lock
(`logs/expect-narrative.lock`). It runs the Claude investigator (Read-only,
`prompts/investigate-expect.md`) on the investigation file, appends "AI
narrative" to it and sends a short follow-up. If it fails or times out, the file
says so and nothing else happens. It can never delay or block the next run.

The narrative is **off by default** (`ai_narrative = false`). Poller logs are
untrusted text, and the Claude Read tool is not limited to the log folders by
anything stronger than the prompt. Turn it on only once the investigator runs
under an OS-enforced file boundary (a restricted user or container). The
deterministic investigation does not depend on it.

Meet ids from the registry are checked (`valid_sid`: digits only) before they
go into a URL, a query, a log path or a process match. A bad id is reported by
`db:reads`, never used.

## Running it

    ./watchdog/run-expect.sh --dry-run   # read-only, prints every expectation and the message it would send
    ./watchdog/run-expect.sh             # one real run

Files: `watchdog/expect-state.json` (memory and open alerts),
`watchdog/logs/expect-runs.log` (one JSON line per run),
`watchdog/logs/expect.err`, `watchdog/logs/investigations/`.

Switch on (owner step, after merge): add to the crontab

    */5 * * * *  /home/dev/projects/ai-harness/watchdog/run-expect.sh >> /home/dev/projects/ai-harness/watchdog/logs/expect.cron.log 2>&1

The 30-minute watchdog stands down for live meets while this monitor has run
in the last 15 minutes (`defer_to_expect`), so there are no double alerts.
Turn the monitor off with `enabled = false` under `[expect]`; the watchdog's
own meet rules then take over within 15 minutes.

## Adding an expectation

1. Write an evaluator in `watchdog/expectations.py`:
   `eval_<name>(row, snap, cfg, tz, day[, mem]) -> Expectation`. Pure: read only
   `snap` (and `mem` if it must remember something between runs, under a key
   that includes the id). Give it a stable id (`<name>:<sr id>:<day>` for
   per-day checks), a plain-English `expected` and `evidence`, a `deadline`,
   a severity and a window.
2. Call it from `evaluate()`. If it needs new data, add one cheap, scoped,
   timed read to `build_snapshot()` in `expect_run.py` (filter by meet; no
   full-table counts) and a field on `Snapshot`.
3. Add its title to `_TITLE` in `expect_alerts.py`, and its repeat interval if
   the defaults do not fit.
4. If the investigation should look at something new, add a probe to
   `expect_investigate.py` behind `run()`, so it is bounded.
5. Add its thresholds to `DEFAULTS` and to `[expect]` in `monitors.toml`.
6. Test it with a fake clock and a fake `Snapshot` in
   `tests/test_watchdog_expect.py`: met, pending, missed, and what the policy
   sends. Then run the dry-run against production and read the output.
