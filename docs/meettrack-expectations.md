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
| `first:<id>:<day>` | results start appearing today (results written today) | the writer counts new result files on the host: 60 min after it first saw them. It counts none: never without a start time; 180 min after a known start. It cannot count them (no tick, no meet row, counters over 30 min old, a writer that does not count): known start + 60 min, else 17:00 Lisbon | warn, racing |
| `results:<id>:<day>` | every event the host has published is in MeetTrack, with results for at least half the swimmers on its start list | per event: 20 min after it was first seen lacking while not the newest published event | crit, racing |
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
- Start times: a stated time in `[[expect.start_times]]`, else the earliest
  `events.day_time` when the start list carries it, else none.
- `first:*` asks what the writer can see of the host (`events_published` with a
  `coverage_at` at most 30 min old: "fresh"; one second older is stale). If the host lists new result files today and
  MeetTrack has no result, that is a MeetTrack fault: missed 60 min after the
  files were first seen (`files_seen:<id>:<day>` in memory). Which listed
  files are new today (`baseline:<id>:<day>`): on a meet's first day, all of
  them; on later days, all but those listed before racing starts (08:00, or an
  earlier known start); after a cold start later in the day, all but those
  whose events already have results. And with no result written today, a file
  whose event already has results is never new today. So a restart, or a meet
  that enters the registry mid-afternoon with files listed, still pages, and
  day two never mistakes yesterday's files for today's.
- The writer's counts must both be there and add up (`events_published` and
  `events_with_results` counts, the second at most the first). If not (one of
  them NULL, negative, more covered than published), `results:*` is
  `unknown` and `first:*` treats the host as unseen; every clock, including
  `files_seen`, is kept. If the host lists
  none, the host has published nothing: not a MeetTrack fault, so no alert
  without a start time, and a warning 180 min after a known one
  (`first_results_host_quiet_min`). Only when the writer cannot see the host
  does the clock decide: known start + 60 min, else 17:00 (the morning grace).
  Example, 7 Nov 2026: Alapraia's writer counted 0 files, so 17:00 is no longer
  an alert; Ponte da Barca had no meet row, its writer never ticked, so 17:00
  still alerts (the host had 6 result PDFs). When `results:*` is missed for the
  same meet, `first:*` stands down (one root cause, one alert): an open
  `first:*` alert is CLOSED once, "superseded by results:*", and results:*
  carries the incident to its own RESOLVED. It keeps the
  time it first saw the files: if `results:*` goes quiet while MeetTrack still
  has nothing, `first:*` is judged from that time, not from a fresh 60 min. The
  time is cleared only when results arrive, or when the writer has counted no
  new file for 10 min (two runs: one sample can be a flicker); a blind
  interval (stale or missing counters) keeps it. A failed read for the meet
  leaves `first:*` unknown and its baseline and files_seen untouched.
- `results:*` judges each event number on its own. An event lacks results when
  the host has published it and MeetTrack has none, or results for under half
  of its start-list swimmers whose heats are due (thin; relays and under 4 due
  swimmers never are). A heat is due 25 min after its scheduled time
  (`heat_due_min`; a heat with no time is always due), so a thin event is late
  45 min after its slot at the earliest, the `schedule:*` tolerance. A host can
  swim part of an event outside its start list's slot: Leiria (8 Nov 2026)
  swam the fast heats of events 19-31 in the morning and printed only the slow
  heats, slotted from 11:40, and event 30 paged at 11:15 for 39 swimmers whose
  heats were scheduled at 12:23 (due from 12:48). Which events
  the host has published with no results comes from the writer's counters:
  `events_published - events_with_results` of them, the lowest event numbers
  with no result (the host publishes in order). The newest published event is
  being swum and never counts, however short. A published event with no
  MeetTrack row at all ("not in MeetTrack's event list") has no number: one
  that appeared with the latest rise of `events_published` is the newest, so a
  sole publication without a row never pages from here (`first:*` covers it);
  once the count grows past it, it is judged like any other, each on its own
  clock. A dip in the count (the host's list loses a file for a run) retires
  the newest such clock and is not progress; a catch-up retires the oldest.
  At most 100 such clocks are kept (`MAX_UNLISTED_CLOCKS`); more share the
  newest, so bad counters cannot grow the state file without bound. Any other lacking
  event becomes late 20 min after it was first seen lacking while not the
  newest (memory `late_events`); it leaves the late set when it catches up, and
  the alert resolves when no event is late or short.
- A writer that counts the host's files (`feed_type = pdf`, or any non-NULL
  `events_published`) whose counters are missing, NULL or stale makes
  `results:*` `unknown`: never `met` (a restart with no memory cannot prove
  completeness) and never paged. Judgment pauses while it lasts, but each
  clock keeps its start time: an event that was short before can be overdue
  at once when the counters are fresh again. Writers that never count files (fragment,
  Lenex) are judged on their event rows (thin events) only.
- The order assumption: if a host publishes out of order, the inferred numbers
  can be wrong for a while (event 4 taken for the empty event 5), but the count
  of late events is right and the event being swum is still exempt; a host
  that publishes an earlier event after a later one gets that event judged
  like any superseded event. When the rows show it (more events without
  results below one with results than the counts allow), the evidence says
  "the event numbers are a guess" (`data.order_guess`); it never pages by itself.
- A writer counts files when `feed_type` is `pdf` (case and spaces ignored) or
  its `events_published` is not NULL.
- Rows that share an event number are one event: results add up, the start
  list is the larger row's, the slot is the earliest. A second, empty session
  row under the same number (7 Nov 2026, Leiria events 2 and 16) is not an
  event without results, for `results:*`, `schedule:*` and the source note.
- Memory is per expectation id (meet and day) and is dropped as soon as that
  id is no longer produced: a meet that leaves the registry, or yesterday.
- A late clock whose event is missing from an otherwise good read of the
  events (a partial read) is kept and `results:*` is `unknown` for that run;
  after 15 min of absence (`ABSENT_CONFIRM_MIN`) the event counts as gone and
  its clock is retired (not progress).
- After a restart, a thin event's clock starts at its own newest result when
  the snapshot could ask for it (`thin_refined`); one whose event ids cannot
  go into a filter safely uses the meet's last result time instead (later,
  so never an earlier page), and the evidence says how many were skipped.
  Never before its own newest due heat's slot plus `heat_due_min` (read with
  its due count, in the same query): a heat becoming due grows the count it is
  judged on. Not read: the clock starts at that run. No due heat with a slot:
  its newest result bounds it, as before.
- Limit: the newest event is not paged while it is the newest, so a stuck
  last event of a session is only judged once the next event is published
  (on a multi-day meet, the next morning). The monitor cannot tell a host
  that publishes an empty or partial result file from a parser that reads too
  few rows: both look like an event short of results. Time is the only signal.
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
- Reminders while the problem persists: crit every 60 min (was 30 until 2026-10-11), warn every 60 min,
  start lists every 6 h. A severity rise reminds at once.
- `results:*` reminders are progress-aware (from the change after the 7 Nov
  2026 alerts): no reminder
  while a late event gains results or the writer writes any result for the
  meet. Once nothing has moved for a full interval, it reminds as above and
  says so ("nothing new for 1h05"). A progress time that is not a number, or
  more than 5 min in the future (clock skew), is ignored, so it can never mute
  reminders. On the 7 Nov Leiria data this is one NEW
  (10:10), two reminders during the 13:52-16:10 stall with event 2 at 4 of 16,
  and RESOLVED at 16:10, against one NEW and seven reminders before.
- RESOLVED once, with how long it lasted, when the expectation is met again.
- Racing-window expectations alert only 08:00-22:00 Lisbon. If one is still
  open at 22:00 (missed, or not judged or not due at that moment), one CLOSED
  line says so and the reminders stop. If it is still
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

The dry run sends nothing and saves no state, but it does run the
investigation for every alert it would send, and that fetches the meet's live
results page (swimrankings or the SPLASH host). For a check that must not
touch the hosts, replay recorded data through the tests instead.

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
