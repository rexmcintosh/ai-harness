# alert-watch: Telegram reports become fixes

Date: 2026-11-15. Status: design for owner approval. Nothing here is installed.

## Intent

Today a report lands on Rex's phone and stops there. Nothing acts on it until Rex
opens a session, pastes the report and explains the context. During a meet weekend
that wait is the slow part: on 13 Nov 2026 the Algés relay fix needed a merge and a
writer restart, and both waited for Rex.

alert-watch closes that gap. Every message that goes to Telegram through
`bin/tg-send` is also written to a local inbox. A dispatcher picks up new messages
and starts a Claude run. The run decides which messages are real failures and fixes
them: it takes allowed live steps on its own, and it makes code fixes on a branch,
tests them, gets the council review, merges, pushes and restarts what needs it.
Every action ends in a short Telegram note to Rex that says what it saw, what it did
and how it checked the result.

### Owner decisions (Rex, 2026-11-14 and 2026-11-15)

- The watcher may take live steps without asking.
- The approved list of live steps is in "Authority" below ("List is right").
- The watcher may also merge to `main` and push without asking ("add merges too").
  This is an explicit change from the global rule that a merge waits for "do it".
  It applies to alert-watch runs only. See "Authority" and "The global rule".

### Improvement test (from `docs/automation-ops-2026-09-05.md`)

1. **Outcome.** A defect is fixed sooner, and Rex does not have to carry a report
   into a session by hand.
2. **Evidence of the problem.** `watchdog/logs/investigations/` holds 37 incident
   files from 22 Oct to 14 Nov 2026, 25 of them from 6 to 14 Nov; each one ended
   as a message to Rex. The 13 Nov Algés
   fix waited for Rex for both the merge and the restart. **Proof it works:** over
   the first four weeks, count real failure reports and how each ended: fixed and
   checked by the watcher, a clear "needs you" note, or nothing. Also record the
   time from report to verified fix.
3. **Existing parts it reuses.** `bin/tg-send` (the one sender almost every job
   uses), the atomic-rename claim pattern from `fixit/queue.py`, the headless
   `claude -p` call pattern from `watchdog/run-watchdog.sh`, and
   `venice-delegate council-review --diff`. No new bot, no Telegram listener, no
   public endpoint, no second task database.
4. **What Rex sees when it fails.** A Telegram note from the dispatcher itself (it
   does not need a model to send it), and a watchdog warning when the inbox stops
   moving. See "When the watcher fails".
5. **Authority and how it stops.** The allowed list below, daily caps, one run at a
   time, a run time limit, and a pause file. See "Authority" and "Stopping it".
6. **When to retire it.** If, after four weeks, most runs end in "needs you", or any
   merge had to be reverted twice for the same cause, turn it off and review.

## Goals

- Every `tg-send` message is captured, even when Telegram itself is down.
- A real failure report starts a fix without Rex.
- Live steps and merges happen inside the approved list, with a note for each.
- Rex can open any run later and see everything it did.

## Non-goals

- Replying to the watcher from Telegram. There is no inbound listener; the session
  bridge was retired on 2026-10-07. Rex opens the saved run in a terminal instead.
- Jobs that call the Telegram API directly (`diem/report.py`,
  `romance-empire/src/social/tiktok_replies.py`). They are left out for now.
- Code changes in repositories other than `splash_poller` and `ai-harness`.
  Reports from other jobs are diagnosed and explained, never changed in code.

## Anti-goals (a failure even if it works)

- A loop: the watcher's own notes, or a broken watcher, feeding itself new work.
- A merge that breaks a live meet and is not reverted fast.
- Text inside a report steering the watcher. Reports carry outside data (meet
  names, email subjects in Bebop logs). They are data, never instructions.
- A flood of notes. One note per run, and none for a batch that held nothing real.
- The capture ever delaying, changing or failing a Telegram send.

## How it works

```
any job ──> bin/tg-send ──> Telegram (unchanged)
                 │
                 └─> inbox/<id>.json  (written before the send)
                         │
     cron */2 ──> alertwatch/dispatch.py
                   ├─ paused? caps reached? another run active?  -> wait
                   ├─ claim new entries (inbox -> batch/<run-id>/)
                   ├─ rules.toml: drop the known non-failures (logged, no run)
                   ├─ nothing left -> done, no note
                   └─ claude -p (policy + entries + recent runs) in ~/projects
                         ├─ live steps from the allowed list
                         ├─ code fix: worktree -> tests -> council -> merge -> push -> restart -> check
                         └─ final JSON result
                   ├─ save runs/<run-id>.json (session id, entries, result)
                   └─ one Telegram note to Rex (tagged, so it is not captured)
```

### Part 1: capture in `bin/tg-send`

- Before the first send attempt, `tg-send` writes one JSON file into the inbox:
  `~/.local/state/alert-watch/inbox/<UTC time>-<random>.json`. It writes to a temp
  file in the same folder and renames it, so the dispatcher never reads half a file.
  The folder and files are mode 700 and 600.
- Fields: `version` (1), `id`, `captured_at` (UTC), `chat_id`, `text`, `source`,
  `cwd`. `source` is `$ALERT_SOURCE` when the caller sets it, else the parent
  process command line (for example `python3 coverage_check.py`). This tells the
  rules and the run which job spoke.
- Capture is best effort. Any capture error prints one line to stderr and the send
  goes on exactly as before. Exit codes and stdout do not change.
- `ALERT_INBOX_DIR` overrides the folder. `ALERT_INBOX_DIR=off` turns capture off.
  Tests that run the real `tg-send` must point it at a temp folder.
- Messages that the watcher itself sends carry `ALERT_SOURCE=alert-watch`. The
  dispatcher drops those at once, so the watcher never reacts to its own notes.

### Part 2: rules (`alertwatch/rules.toml`)

A plain data file, read by the dispatcher before any model runs. Each rule matches
on `source` and/or a text pattern and says `ignore` or `look`. The default for a
message no rule matches is `look`. First version, from today's senders:

| Sender | Today it sends | Rule |
|---|---|---|
| `watchdog/run-watchdog.sh` | anomalies and the investigator's diagnosis | look |
| `watchdog/run-expect.sh` (`expect_run.py`) | MeetTrack "expected but missing" alerts, hourly reminders, RESOLVED lines | look (RESOLVED lines: ignore) |
| `splash_poller/coverage_check.py`, `discover_meets.py` | meet coverage gaps | look |
| `bebop/run-briefing.sh` | the morning and evening briefing | ignore, except the "briefing failed" line: look |
| `council/scripts/security-sweep.sh` | weekly findings | look |
| `backlogrun/cli.py` | finished backlog work for review | ignore |
| `sessiongc/cli.py` | stranded work report | ignore |
| `finance-tracker` backup | backup result | ignore on success, look on failure |
| `hq/watch/usna-summer-seminar.py` | a page-change watch | ignore |
| `sat-prep/scripts/feedback-sync.ts` | new user feedback | ignore (already becomes a backlog item) |

The builder confirms each sender's real message shapes from its code before writing
the patterns. Ignored entries go to `done/` with the matching rule recorded, so a
wrong rule can be found later.

### Part 3: the dispatcher (`alertwatch/dispatch.py`)

Runs from cron every 2 minutes under `flock -n`. In order:

1. If `~/.local/state/alert-watch/PAUSED` exists, exit (the inbox keeps filling).
2. If a run is active (its lock is held), exit.
3. Check the caps (below). If one is reached, send one note that says which cap,
   then exit. Do not send that note again until the cap resets.
4. Claim every inbox entry by renaming it into `batch/<run-id>/`. A rename that
   fails means another process took it; skip it.
5. Drop `alert-watch` entries and apply the rules. If nothing is left, move the
   batch to `done/` and exit with no note.
6. Build the prompt: `alertwatch/POLICY.md`, the `look` entries, and a short summary
   of every run in the last 24 hours (its entries' first lines and its result).
   The summary lets the run see that a reminder belongs to a problem an earlier run
   already handled, and how many times it already tried.
7. Start `claude -p` with cwd `~/projects`, the chosen model (`opus` by default,
   set in config), `--output-format json`, a 60 minute limit with a kill after
   30 seconds (as in `run-watchdog.sh`), and the denied tools below. Pass the
   prompt on stdin, not as an argument.
8. Save `runs/<run-id>.json`: entries, start and end time, exit status, the Claude
   session id from the JSON output, and the parsed final result.
9. Send one note to Rex from the run's result (see "Notes to Rex"). If the run
   crashed, timed out or gave no readable result, send the failure note instead.
10. Move the batch to `done/`, or back to `inbox/` with `attempts + 1` when the run
    failed before it could act (usage limit, crash at start). An entry that failed
    twice goes to `failed/` and is named in the failure note.

### Part 4: the run (`alertwatch/POLICY.md` and its prompt)

The run is an ordinary Claude Code session with Bash, Read, Edit and Write. Its
prompt is `POLICY.md`, which holds the authority list, the fix procedure, the
untrusted-input rule and the result format. It reads `~/.claude/CLAUDE.md` and the
target repository's `AGENTS.md` like any session. Where they differ on merges, the
scoped rule in "The global rule" decides.

It must end with one fenced JSON block, which the dispatcher parses:

```json
{
  "entries": [{"id": "...", "verdict": "fixed|live_step|needs_rex|not_a_failure|same_as_run", "run_ref": null}],
  "actions": [{"kind": "restart|rerun|lock_cleared|merged|reverted|branch_only", "what": "...", "checked": "..."}],
  "merges": [{"repo": "splash_poller", "from_sha": "<40 hex>", "to_sha": "<40 hex>", "branch": "...", "reverted": false}],
  "pending_checks": [{"repo": "splash_poller", "to_sha": "<40 hex>", "due_at": "<UTC RFC3339>", "what": "..."}],
  "needs_rex": ["one line each, with the exact words Rex should send"],
  "note": "the text of the Telegram note, at most 1500 characters"
}
```

## Authority

### The run may do these alone

- Restart a meet writer that is stuck or has crashed: `kill -TERM` its PID from
  `~/.local/state/meettrack/run/poller-<key>.pid`. The supervisor cron starts it
  again within 5 minutes. Then check its first pass in `logs/poller-<key>.log`.
  It never starts a writer itself. Before the kill it reads the meet's
  `poller_attempts` (a read, allowed). The supervisor marks a meet `failed` at 5
  attempts, and only a database write undoes that, so the run restarts only when
  `poller_attempts` is 3 or less. Otherwise it is `needs_rex`.
- Run a failed job again, one time, through `alertwatch/rerun.py <job>`. The job
  must be on the rerun list in `alertwatch/config.toml`, which holds each job's
  exact cron command line and lock. `rerun.py` runs it with the job's normal
  environment (without `MEETTRACK_DB_READ_ONLY`), and refuses a second rerun of
  the same job within 6 hours. First list: `coverage_check.py`,
  `discover_meets.py`, `folder_finder.py --apply`, `announcements.py`,
  `ingest_entries.py`, `bebop/run-briefing.sh`, the finance backup.
  `supervise_meets.py` is never on the list: it runs every 5 minutes by itself,
  and a rerun would make the run the parent of the writers it starts.
  *Builder's call, for Rex to check:* a rerun is the job doing its usual work, so
  the rows it writes are not "the run writing rows". The database rule below
  covers writes the run makes itself.
- Remove a lock file when the process that made it has stopped (check the PID first).
- Fix code in `splash_poller` or `ai-harness` on a branch, with tests and the
  council review.
- Merge that fix to `main` and push, under the merge procedure below.
- Revert its own merge and push, when the check after the merge fails.

### The run must ask Rex before these

- Write or delete rows in the database (Supabase), in any way, including through
  scripts such as `register_meet.py` or a repair script.
- Change the crontab, systemd units, or config and `.env` files.
- Send a message to anyone other than Rex.
- A merge outside the merge procedure, or a merge in any other repository.
- Delete files other than its own worktrees, branches and stale lock files.

"Ask Rex" means: stop that part, prepare it (a branch, a plan file, the exact
command), and put one line in `needs_rex` with the words Rex should send.

### Merge procedure (the only way the run merges)

1. Make a new worktree off `origin/main` on a branch `alertwatch/<run-id>-<slug>`.
   Never edit the main checkout, which is what cron runs from.
2. Write a failing test that shows the problem, then the fix. The full test suite
   of that repository must pass.
3. Run `venice-delegate council-review --diff`. Fix the valid findings and review
   again. At most 3 rounds. Findings still open after that mean `needs_rex`, no merge.
4. Merge only when all of these hold:
   - the main checkout is clean and on `main`, and `main` equals `origin/main`;
   - the branch rebases onto `origin/main` and the full suite passes again after it;
   - the diff touches no path on the denied list (the dispatcher's config holds
     it, and the run checks `git diff --name-only` against it):
     - `web/` in `splash_poller` (it deploys the public site meet.swimtrack.ai
       through Vercel);
     - `.env*`, crontab or unit files, `migrations/` or other SQL schema files;
     - the watcher's own machinery: `alertwatch/`, `bin/tg-send`, `council/`;
     - the alarms: everything under `watchdog/` (thresholds in
       `monitors.toml`, rules in `expectations.py`, reminder timing in
       `expect_alerts.py`, the `alertwatch` check). A false alarm is fixed on a
       branch and sent to Rex, never merged by the watcher, so it can never
       quiet an alert by changing the alarm.

     *Builder's call, for Rex to check.* A fix that needs a denied path still
     gets its branch, tests and review, and ends as `needs_rex`;
   - the day's merge cap is not reached.
5. Fast-forward `main` in the main checkout, push `origin main`, and confirm
   `git status -sb` shows `## main...origin/main` with no `[ahead N]`.
6. Restart every live writer that runs the changed code, not only the meet that
   broke (Rex, 2026-11-15: "Every live meet"). A writer keeps its old code until
   it restarts, so this is how a shared fix, for example in `db.py`, reaches every
   meet at once. Order: the broken meet first; when its first pass in its log is
   good, the other live writers, each checked the same way. If the broken meet's
   first pass is bad, revert before touching the others. Each restart follows the
   `poller_attempts` rule above; a meet that is too close to the cap is left
   alone and named in `needs_rex`. *The order is the builder's call, for Rex to
   check.* How to tell which writers run the changed code (for example by the
   modules each writer script imports) is left to the builder.
7. Check that the reported problem is gone. When the proof comes later (the next
   event's results, the 07:00 `coverage_check.py`, the next hourly
   `discover_meets.py`), the run adds a `pending_checks` item to its result: the
   merge, what to check, and when (at most 24 hours after the merge). At that time
   the dispatcher puts a check entry (source `alert-watch-check`, which is not
   dropped) into the inbox, and the next run makes the check.
8. If a check fails because of the merge, `git revert` it, push, restart again,
   and put it in `needs_rex`. Any alert-watch run may revert any alert-watch merge
   (the runs ledger lists them), not only the run that made it. It never reverts
   anyone else's commits. *Builder's call, for Rex to check.*
9. Delete its worktree and branch.

### The global rule

`~/.claude/CLAUDE.md` says a merge waits for Rex's "do it". The build adds one dated
section there, worded like the existing Venice sections, for example:

> ### alert-watch runs (2026-11-15)
> Rex approved that alert-watch runs (started by `alertwatch/dispatch.py`, with
> `ALERT_WATCH_RUN=1` set) may take the live steps and merges listed in
> `~/projects/ai-harness/alertwatch/POLICY.md` without asking. That policy is Rex's
> standing rule for those runs only. Every other session keeps the merge protocol.

Rex approves the exact words before they go in.

### Guard rails that do not rely on the prompt

- Denied tools passed to `claude -p`: `crontab`, `systemctl`, `sudo`, `git push`
  with `--force` or `-f`, and reading `.env` files. The builder must prove, with one
  fake run, that a denied command is refused in the permission mode used.
- `splash_poller/db.py` refuses `insert`, `update`, `upsert` and `delete` when
  `MEETTRACK_DB_READ_ONLY=1` is set, and the dispatcher sets it for the run. Writers
  are never started by the run (the supervisor starts them from cron, without this
  variable), so they keep writing. This stops a mistake, not a determined agent: a
  Bash command could unset it. The DB rule is still a policy rule, and every run's
  saved session shows what it ran.
- Caps (in `alertwatch/config.toml`): at most 30 runs a day, 6 an hour, 3 merges a
  day across both repositories. Reverts do not count toward the merge cap.

## Notes to Rex

One note per run, through `tg-send` with `ALERT_SOURCE=alert-watch`. Plain short
sentences, no em dashes, the same style as Rex's chat rules. Shape:

```
alert-watch: fixed the Algés results gap
Saw: results for event 7 missing for 25 min (run-expect).
Did: fixed the relay parser (splash_poller 1a2b3c4), merged and pushed, restarted the writer.
Checked: event 7 has 48 of 48 results.
Needs you: nothing.
Open the run: claude --resume <session id>  (in ~/projects)
```

When something needs Rex, the "Needs you" line holds the exact words to send in a
session, for example `do it: write the 3 club rows`.

## When the watcher fails

- **A run fails** (crash, time limit, usage limit, no readable result): the
  dispatcher sends the failure note itself, without a model.
- **The Claude usage limit** is not a failed run. The dispatcher spots it in the
  run's output, puts the entries back in the inbox without counting an attempt,
  sends one note for that limit window, and starts no run until the reset time
  the message gives (60 minutes when it gives none). It does not count toward the
  pause below, so the watcher comes back by itself after the limit resets. The
  3 to 5 Oct 2026 Plovdiv rehearsal hit this limit on every investigator run.
- **Three failed runs in a row**: the dispatcher writes the `PAUSED` file with the
  reason and sends one note: "alert-watch paused itself: <reason>. Remove
  ~/.local/state/alert-watch/PAUSED to start it again."
- **Watchdog check `alertwatch`**: warns when the oldest inbox entry is older than
  20 minutes and the watcher is not paused, or when the newest run failed. That warn
  goes through `tg-send` like any other alert. If the watcher is broken it cannot act
  on it, and if it recovers the problem is already gone, so this cannot loop.
- Telegram outages do not lose reports: capture happens before the send.

## Stopping it

- Pause: `touch ~/.local/state/alert-watch/PAUSED`. Start again: delete the file.
- Remove: delete the cron line. The inbox, runs and batches stay for review.
- Every note ends with the run's session id, so any run can be opened and checked.

## Files

- `bin/tg-send`: the capture (Part 1).
- `alertwatch/dispatch.py`, `alertwatch/config.toml`, `alertwatch/rules.toml`,
  `alertwatch/POLICY.md`, `alertwatch/README.md`.
- `watchdog/`: the `alertwatch` check.
- `tests/test_alertwatch_*.py`, and `tests/test_tg_send.py` updated to use a temp inbox.
- `docs/contracts/alert-watch.md` and its row in `docs/contracts/README.md`.
- In `splash_poller` (its own branch and merge): the `MEETTRACK_DB_READ_ONLY` guard
  in `db.py`, with tests.
- `~/.claude/CLAUDE.md`: the dated section, after Rex approves its words.
- Crontab: one line, added by editing a file, never through a pipeline (a `cat`
  alias wiped the crontab on 2026-07-07). Back up the crontab first.

## Tests (no live sends, no real model runs)

- `tg-send` writes one complete inbox file before sending; a capture error leaves
  the send, exit code and stdout unchanged; `off` disables it; the temp-file rename
  never leaves a partial file.
- The dispatcher with a fake `claude` and a fake `tg-send` and temp folders: pause
  file, active-run lock, each cap, own-source drop, each rule, empty batch (no
  note), a good result, a crash, a time limit, an unreadable result, the retry and
  `failed/` path, three failures in a row pausing it.
- Two dispatchers at once never claim the same entry.
- The watchdog check: stale inbox, failed newest run, paused (no warn).
- `db.py` read-only guard refuses each write call and allows reads.

## Rollout

1. Merge capture only. Let the inbox fill for a day and check that the rules sort
   real messages correctly.
2. Turn on the dispatcher in `mode = "diagnose"` (the run may read and explain but
   not act; the policy says so and the denied list adds Edit and Write). Run it for
   48 hours.
3. Switch `mode = "act"`. Rex sees the first real fixes in Telegram.

## Left to the builder

- The rule patterns for each sender, from the senders' code.
- The run-id format, the 24-hour summary format, and the prompt wording around
  `POLICY.md`.
- Whether the watchdog check lives in `watchdog/run.py` or as a `monitors.toml` entry.
- How the dispatcher reads the Claude session id from the JSON output.

## Changes after the council review (2026-11-15)

The first council review asked for limits enforced by code, not only by the prompt.
The build now has:

- **Checked helpers for every outward action.** `alertwatch/merge.py` (merge and
  revert), `alertwatch/restart.py` (writer restarts) and `alertwatch/rerun.py` check
  their rules at the moment of the action. Merges: act mode, a listed repository, a
  fix worktree under `<state>/worktrees/` on an `alertwatch/` branch, clean trees,
  `main` equal to `origin/main`, a fast forward, no denied path, and the daily cap.
  Restarts: the PID file's start time matches a live meet writer, and
  `poller_attempts` is 3 or less. Merges, reverts and restarts are logged in an
  append-only `actions.jsonl`, which is the authority for the merge cap and for
  which writers to restart again after a revert. The run's deny rules block push,
  merge, revert and kill as shell commands, and Edit or Write inside `~/projects`.
- **Diagnose mode is an allowlist.** It runs without `--dangerously-skip-permissions`;
  only read-only tools and `alertwatch/dbread.py` are allowed. Real runs proved both
  modes' rules on 2026-11-15.
- **Leftover processes** that carry the run's `ALERT_WATCH_RUN_ID` are killed when a
  run ends, even if they left the process group.
- **The watchdog check** turns on only when `<state>/ENABLED` exists, so the
  capture-only stage raises no false alarm.
- **Smaller fixes:** a rerun that never started gives its 6-hour window back; check
  entries have fixed ids, so a crash cannot enqueue one twice; a check is due at most
  24 hours after its run; a report with no valid verdict is named for Rex; report text
  cannot fake the prompt's report markers; capture syncs to disk, and half-written
  capture files are cleaned up.

Still policy, not enforced by code (a gate for Rex before `mode = "act"`): the run
holds the normal database key behind `MEETTRACK_DB_READ_ONLY`, and in act mode a
shell command can still change files. A read-only database key for the run, or an
OS sandbox, would close these.
