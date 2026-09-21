# Bebop — personal assistant (Claude Code edition)

Bebop is Rex's personal assistant, rebuilt on **Claude Code** after retiring the Hermes agent
(2026-06-06). Same Telegram bot (`@Bebopmac_bot`), new brain. This folder is the foundation we
build the rest of the assistant on.

## Why this exists

Hermes (Nous Research's agent) worked, but it metered tokens at a ~$1,000/month-equivalent pace.
Claude Code gives the same primitives — MCP integrations, memory, skills, Telegram delivery —
while interactive work runs on the Max subscription and unattended scans run on the cheap model
over deltas only. Measured cost of a briefing run: **~$0.07** → roughly **$4–5/month** for the
twice-daily briefing.

## What's built (slice 1 of 4): the twice-daily briefing

The "spine" — a scheduled, headless Claude Code run that scans Gmail + Calendar and delivers a
glanceable digest to Telegram. Everything else (alerts, knowledge-base tending, taking actions)
will reuse this exact machinery.

```
cron (07:00 + 18:00 Lisbon)
  └─ run-briefing.sh <morning|evening>
       ├─ reads state.json  → "emails since last successful run" (delta window)
       ├─ claude -p <prompt> --model haiku --dangerously-skip-permissions
       │     ├─ Gmail  search_threads (important only, since delta)
       │     ├─ Calendar list_events (today / tomorrow)
       │     └─ Telegram reply → sends digest to Rex (chat 7735693897)
       ├─ if the agent answers FAILED (or crashes): wait 90s, run it ONE more time
       ├─ logs token cost to logs/runs.log (`attempts=2` marks a run that needed the retry)
       └─ advances state.json ONLY on success (failed run never skips email)
```

## Files

| File | Purpose |
|------|---------|
| `prompts/briefing-morning.md` | Morning briefing instructions (today's schedule + important new email). The thing to tune. |
| `prompts/briefing-evening.md` | Evening wrap + tomorrow preview. |
| `run-briefing.sh` | Runner. Computes the delta window, invokes headless Claude, logs cost, manages state, pings on failure. |
| `backlog_line.py` | Builds the one backlog line the morning briefing carries. Stdlib + PyYAML, reads the backlog file and nothing else. |
| `state.json` | Last successful run (epoch + iso). Gitignored. The delta mechanism. |
| `logs/runs.log` | One line per run: mode, rc, result, cost, tokens. Gitignored. Watch this to track cost. |

## Run manually

```bash
./run-briefing.sh morning      # or: evening
```

## Schedule (installed via crontab)

```
CRON_TZ=Europe/Lisbon
0 7  * * *  .../bebop/run-briefing.sh morning
0 18 * * *  .../bebop/run-briefing.sh evening
```

## Key decisions / gotchas (learned the hard way)

- **Headless MCP needs `--dangerously-skip-permissions`.** `--allowedTools` alone does NOT
  authorize MCP tool calls non-interactively — the agent gets silently blocked and hallucinates an
  "auth issue." The flag is acceptable here: Rex's own VPS, fixed/benign prompt, narrow toolset.
- **Telegram cutover.** Claude Code's telegram plugin was repointed from `@JaneVal_bot` to
  `@Bebopmac_bot` (token in `~/.claude/channels/telegram/.env`); allowlist is
  `~/.claude/channels/telegram/access.json` (`dmPolicy: allowlist`, Rex = `7735693897`). The Hermes
  gateway (`hermes-gateway.service`) was stopped + disabled to free the token. `~/.hermes/` is left
  on disk as a fallback for a few days.
- **Cost driver.** Most token cost is the MCP tool schemas loaded into context each run, not the
  output. If cost creeps, scope the run to only Gmail/Calendar/Telegram via `--mcp-config` +
  `--strict-mcp-config` (deferred — current cost is already trivial).
- **Billing caveat (verify empirically).** Whether headless/scheduled `claude -p` on Max counts as
  subscription use or is metered separately was unresolved at build time. Watch `logs/runs.log`
  cost + the Max usage dashboard over the first week.

## Roadmap (the other 3 slices)

2. **Proactive alerts** — same scan, event-triggered pings instead of fixed schedule.
3. **Tends the knowledge base** — route worth-keeping items into the RexBrain wiki (`~/wiki/`) + memory.
4. **Takes actions** — draft replies / schedule / update Notion, gated by approve-via-Telegram.
   (Needs a persistent Telegram listener for true two-way — a follow-up to the send-only briefing.)

## Slow connector start and the single retry

The claude.ai Gmail and Calendar connectors are listed by name when the headless session
starts, but on a slow start they cannot be called for the first 30 to 45 seconds. The
agent looks the tools up about ten times, gives up, and answers `FAILED: ... tools
unavailable` (2026-08-26 18:00 and 2026-09-18 18:00; the 2026-08-22 07:00 run got through
on its 11th lookup). Nothing was wrong with the accounts or the connection.

`run-briefing.sh` therefore runs the agent again once, after `BEBOP_RETRY_DELAY` seconds
(default 90), when the first answer is `FAILED`, a parse error, empty, or a non-zero exit.
`state.json` has not advanced, so the retry covers the same email window. A second
failure sends the usual failure ping. `BEBOP_MAX_ATTEMPTS=1` turns the retry off; any
other value means 2, so the runner can never loop.
Tests: `tests/test_bebop_runner.py` (fake `claude` and `tg-send`, temp state and logs).

## The backlog line (morning only)

`backlog-run` works the queue at 03:00 UTC. It leaves finished work `in_review` for Rex to
approve and parks anything needing a human decision as `held`. Neither state pinged him, so
items sat for weeks. The **morning** briefing now carries one line about it:

```
Backlog: 3 wait for your review (oldest 18 days: review-complaint-sweep). 1 new on hold since yesterday. Look: backlog-run report
```

- **Built in code, not by the model.** `backlog_line.py` prints the finished string;
  `run-briefing.sh` appends it to the composed briefing *after* the agent has answered and
  before the send. The model never sees it, so it cannot drop it, shorten it or reword it.
  (The loom line is different: it goes in through the prompt and is asked to pass it
  through verbatim.)
- **Silent when nothing is waiting.** No `in_review` items and no new hold means an empty
  string and no line at all. A line that appears every morning stops being read.
- **Evening is untouched.**
- **Not stapled to a failure ping.** If the briefing failed to compose, the failure ping
  goes out alone. That ping is an alarm; an ordinary-looking backlog nag under it would
  make a broken morning read like a normal one. The items keep until tomorrow.
- **Fail open, always.** Missing file, broken YAML, an unexpected shape, a crash or a hang
  cost the line and nothing else. The helper always exits 0 and prints nothing it is unsure
  of; the shell wraps it in `timeout` (`BEBOP_BACKLOG_TIMEOUT`, default 10s) and `|| true`.
- `runs.log` gains `backlog_line=1|0` — whether the line was in the message that was sent.

### How the two numbers are worked out

| Clause | Rule |
|--------|------|
| *N wait for your review* | items with `status: in_review`. |
| *oldest N days: `<name>`* | age from `worked` if present, else `created`; name is the id without its leading `YYYY-MM-DD-`. Dropped when no waiting item has a usable date. |
| *N new on hold since yesterday* | items with `status: held` whose `worked_at` stamp is less than 24 hours old. Older records with no stamp: `worked` date is today (UTC). |

`backlogrun.cli` writes two fields in the same write that sets the status: `worked` (a UTC
**date**) and `worked_at` (a UTC **timestamp**, added 2026-09-21). The line uses the stamp,
so it does not depend on when the runner fires: a hold from a 03:00 UTC run, a 22:00 UTC run
or a run that crosses midnight is reported on the next morning and only that one. A record
with no stamp falls back to "`worked` is today", which is exact only while the runner fires
after midnight UTC and before the briefing. A hold placed by hand (`backlog-run hold`)
writes neither field and is never counted: the clause under-reports rather than guesses.

Environment: `BEBOP_BACKLOG_FILE` (default `~/projects/backlog/backlog.yaml`),
`BEBOP_BACKLOG_NOW` (ISO date, injectable "today" for tests), `BEBOP_BACKLOG_TIMEOUT`
(seconds, default 10, never more than 30).

The helper runs only after the briefing composed, so it can never delay the failure ping.
The line is left out when it would push the message past 4000 characters (Telegram's limit
is 4096), and the item name in it is cleaned to one printable line of at most 60 characters.
Tests: `tests/test_bebop_backlog_line.py` (the builder) and `tests/test_bebop_runner.py`
(the append, the modes, the fail-open paths).
