# DIEM Drain Engine — Design

**Date:** 2026-07-03
**Status:** Approved (brainstorm 2026-07-03)
**Problem:** Venice DIEM is a use-it-or-lose-it daily allowance that resets at 01:00 local. On evenings the operator is flying, asleep, or simply done for the day, unspent DIEM expires. The drain engine converts that surplus into standing-workload output — automatically, deterministically, and always behind the existing human gates.

## 1. Shape & principle

A `diem` CLI (new package `diem/` in this repo, second console script in the existing
`council` distribution — one pipx install to maintain) plus three plain crontab entries.

**diem owns only the queue, the clock, and the budget — it never implements workloads.**
Runners shell out to tools that already exist and are already trusted: `council review` /
`council ask`, `python -m loom.cli backfill`, and repo-provided pipeline commands. No
Claude tokens and no LLM judgment at night; judgment happens at queue-curation time
during the day (the operator or a Claude session banks items).

## 2. State & config

- **Config:** `~/.config/diem/config.toml`
  - `repos = [...]` — repo paths for auto-discovery
  - `checkpoints = [{time="21:00", floor=0.40}, {time="23:00", floor=0.15}, {time="00:15", floor=0.0}]`
  - `deadline = "00:50"`, `reset = "01:00"` (local time)
  - priority weights per type; per-type conservative seed estimates
  - `[telegram]` optional: `bot_token`, `chat_id` (reuses the existing bot)
  - `[cmd_whitelist]` — the only commands the generic `cmd` job type may run, keyed by repo
- **State:** `~/.local/state/diem/`
  - `queue/` — one JSON file per item (write-a-file = bank-an-item; no locking games)
  - `archive/` — completed and failed items with outcomes
  - `reports/YYYY-MM-DD.md`
  - `estimates.json` — per-runner moving averages: DIEM cost/job, duration/job
  - `reviewed.json` — per-repo last-reviewed SHA
  - `drain.lock`, `pause` marker, `drain.log`
- **Keys:** cron has no shell env, so `diem` reads `~/.env` directly and accepts
  **either** `VENICE_API_KEY` or `VENICE_KEY`. It injects the key into subprocess
  environments, which also makes `council` work under cron.

## 3. Queue

Item schema:

```json
{
  "id": "ulid",
  "type": "ask | review | images | backfill | cmd",
  "banked": true,
  "priority": 100,
  "payload": { "...type-specific..." },
  "created": "iso8601",
  "expires": "iso8601 | null",
  "attempts": 0,
  "max_attempts": 2
}
```

Payloads: `ask` = question + panel; `review` = repo + (`--diff` | commit range);
`images` = repo + count (executes the repo's standing-order command); `backfill` =
max-targets; `cmd` = whitelisted command name + repo.

Banking, two equivalent ways: `diem queue add ask "question" --panel decision` from any
terminal, or any Claude session writes the JSON file into `queue/`. `diem queue list` /
`diem queue rm <id>` to inspect and prune. **Banked items always outrank discovered
items.** Dedupe by type-specific key (one review per repo per night; one images job per
standing order per night). `expires` lets a stale banked question die quietly instead of
burning DIEM a week later.

## 4. Auto-discovery (runs at each checkpoint, before draining)

- **Hygiene:** for each configured repo — commits on main since `reviewed.json`'s SHA →
  queue `council review` of that range; dirty working tree → queue `council review
  --diff`. Cap one review job per repo per night.
- **Publishing feedstock:** reads a *standing-order file* owned by the target repo
  (e.g. `romance-empire/.diem/standing-order.json`) declaring: target stock level, the
  candidates dir to count, and the command to run (the existing teaser pipeline with the
  pinned-identity technique). Stock below target → queue an `images` job for the
  shortfall. **No standing-order file → skip.** The drain never invents creative
  direction; open creative decisions (e.g. object-row direction) remain the operator's.
- **Content filler:** `loom backfill` is the infinite sink (already defined as the
  DIEM backlog weave). Queued in small chunks (`--max-targets 2-3` per job) so it soaks
  remaining budget without ever being the job that blows the deadline.

Default priority order: **banked → feedstock → hygiene → backfill filler.** Weights
configurable.

## 5. Scheduling & the 01:00 boundary

Three cron checkpoints, each running `diem drain --checkpoint`:

| Time  | Balance floor | Meaning |
|-------|---------------|---------|
| 21:00 | ~40%          | Drain surplus; leave a healthy reserve — operator may still be working |
| 23:00 | ~15%          | Probably done for the night; drain most of it |
| 00:15 | 0%            | Use-it-or-lose-it endgame |

Drain loop per checkpoint:

1. Read live DIEM balance (rate-limits endpoint; cross-check against the
   `x-venice-balance-diem` response header after every job).
2. If balance ≤ this checkpoint's floor → stop.
3. Pop highest-priority job. **Before starting:** skip it if estimated DIEM cost would
   punch through the floor, or if `now + estimated duration > deadline (00:50)`.
   Estimates come from `estimates.json` moving averages; conservative seeds on first
   runs (image edits seed ~3 min each).
4. Run it (subprocess, hard timeout at the 00:50 deadline as backstop), record actual
   cost/duration into the moving averages, archive the item, loop.

Nothing launches after the deadline: the 01:00 line is enforced by clock math, never by
judgment.

**Yield to the operator:** balance is re-read between jobs, so interactive evening use
pushes the balance toward the floor and the drain stops sooner — the human always wins
the race automatically. `diem pause` (until next reset) / `diem pause 2h` /
`diem resume` for explicitly quiet nights. The lockfile prevents overlapping
checkpoints; a long 21:00 drain makes the 23:00 one a no-op.

## 6. Safety gates

The drain is **read-and-stage only**:

- Review findings land as report files; image candidates append to the candidates dir;
  loom writes to its own store.
- It never commits, pushes, merges, publishes, or touches KDP. Every output sits behind
  the existing human gates (global merge protocol, romance-empire's gates — including
  the hard rule that no book ships without the human taste-read).
- `cmd` runs only commands whitelisted in config.
- Images flagged `x-venice-is-content-violation` are quarantined and noted in the
  report, never staged as candidates.

## 7. Reporting

- **21:00 ping** (Telegram, one line): `DIEM 38% left · 6 queued (2 banked) · tonight:
  2 reviews, 8 teasers, backfill.`
- **Morning report** after the final checkpoint: `reports/YYYY-MM-DD.md` — DIEM spent
  vs. expired, jobs run with links to outputs (review reports, candidate dirs, ask
  answers), failures, and what discovery found but didn't get to. Telegram gets a
  3-line summary + file path.
- `diem status` anytime: balance, hours to reset, queue depth, projected plan.
- Telegram unconfigured → file-only, no error.

## 8. Error handling

- Per-job failure: log, `attempts += 1`, requeue once (max 2), then archive as failed
  with the error surfaced in the morning report. A failed job never blocks the queue.
- Venice 429/5xx: exponential backoff between jobs.
- Balance endpoint unreachable: abort the checkpoint (**never drain blind**), note in
  report.

## 9. Testing & rollout

Application code → superpowers TDD applies (the carve-out exempts only the creative
pipelines it *calls*). Test rig: fake clock + fake Venice client + tmp state dir.
Coverage: floor/deadline math, priority + dedupe, yield behavior, pause, expiry,
estimate updates, discovery against fixture repos, `~/.env` key parsing (both names).

Rollout: this branch → `council review --diff` → Merge recommendation per the global
protocol → `pipx reinstall council` → propose the three crontab lines for explicit
approval before installing.

## 10. Open calibration points (defaults chosen, operator may tune)

- Floor percentages 40/15/0 and the 21:00 first checkpoint are starting guesses;
  tune in config after a week of morning reports.
- Stock targets per standing order live in the target repo's standing-order file, not
  in diem config.
