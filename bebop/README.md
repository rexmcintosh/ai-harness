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
