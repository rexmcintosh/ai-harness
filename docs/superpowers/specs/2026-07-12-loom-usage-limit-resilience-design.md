# Loom usage-limit resilience + headless plugin isolation

**Date:** 2026-07-12
**Status:** Approved design, pending implementation
**Branch:** `fix/loom-usage-limit-resilience`

## Plain-English summary

The nightly memory job (`loom absorb`) should handle "you're out of Claude usage
for now" gracefully instead of face-planting, and it should stop spawning the
background Telegram helpers that leak.

Today, when a distill call hits the Claude subscription usage limit, loom doesn't
recognise it. It treats the limit like a per-task failure, marks the task `failed`,
and keeps trying every remaining task — all of which hit the same limit. Each doomed
attempt still starts a Telegram plugin helper that gets orphaned. One exhausted
budget becomes hundreds of pointless attempts plus a pile of leftovers that later
exhausted memory and killed the user's tmux sessions.

We make loom **notice the limit, stop immediately, leave the unfinished work in the
queue for the next nightly run, and report the pause clearly** — and we make its
headless Claude calls **start without any plugins**, so nothing gets spawned or leaked.

## Background (the 2026-07-12 incident)

- The nightly `run-absorb.sh` (`python -m loom.cli absorb --live`, ~02:00 UTC) fired
  one headless `claude -p` distill per pending session — 243 targets.
- The account had already spent its ~5-hour subscription budget before 02:00. Every
  distill call returned **"You've hit your session limit · resets 5:10am (Europe/Lisbon)"**
  and exited non-zero. The run ended `distilled: 0, failed: 243`.
- The limit message is printed on Claude's **stdout**; loom's wrapper kept only
  **stderr** on failure, so loom's logs showed a blank `claude exited 1:` — invisible.
- Each rejected call still spawned its Telegram plugin MCP server during startup, then
  died before cleaning it up. ~28 orphaned helpers accumulated, consuming ~3.9 GB and
  ~2 cores, and eventually triggered an OOM that tore down the user's tmux server.

A separate safety net (a systemd user timer that reaps orphaned Telegram helpers every
10 min) is already deployed and stays as defense-in-depth. This spec fixes the source.

## Goals

1. Detect the subscription usage-limit response and stop the run early.
2. Never mark usage-limited targets as `failed` — leave them pending for the next run.
3. Report a limited run as a clear "paused" message, not "243 failed".
4. Run headless distill/weave calls with no plugins loaded (no Telegram helper spawned).
5. Stop discarding Claude's stdout on failure, so future failures are diagnosable.

## Non-goals (YAGNI)

- Re-running the same night after the reset time (chose: next nightly run).
- Retry/backoff for the usage limit or for generic distill errors.
- Moving the cron schedule.
- Fixing `CRON_TZ=Europe/Lisbon` not being honoured (real, but tracked separately).

## Design

Four focused changes in the `loom/` package, plus tests. One PR.

### A. `loom/llm.py` — surface the error, classify the limit, drop plugins

- **See the error.** In `run()`, when the subprocess exits non-zero, build the error
  message from **both stdout and stderr** (today only `stderr[:500]`). This alone
  restores diagnosability.
- **Classify the limit.** If the combined output matches a usage-limit pattern, raise a
  new `UsageLimitError(LLMError)` instead of a generic `LLMError`. Pattern set (case-
  insensitive), kept as a module constant so it's easy to update:
  `hit your session limit`, `usage limit`, `limit reached`, `resets`.
  Failure mode is safe: a missed match degrades to today's behaviour (generic failure,
  retried next run); it does not lose data.
- **Drop plugins.** `build_argv` appends `--settings <shipped settings file>` (see D).

### B. `loom/run.py` — abort on the limit, don't hammer

- In the distill loop's `try/except`, catch `UsageLimitError` **before** the generic
  `except Exception`. On catch: set `summary["limit_hit"] = True` and `break` the loop.
  Do **not** increment `failed`; do **not** advance state. Remaining targets stay
  pending and are picked up by the next nightly run.
- Generic `except Exception` keeps today's behaviour (`failed += 1; continue`) for real
  per-target errors.
- Seed `"limit_hit": False` in the initial `summary` dict.
- After the distill loop, short-circuit before the weave stage:
  `if summary["limit_hit"]: return summary` — the budget is gone, so there is no point
  weaving. Weave's own backend calls are additionally wrapped so a limit hit there sets
  `limit_hit` and stops that stage too.

### C. `loom/summary.py` — report the pause honestly

- `format_run_summary` / `build_summary` gain a `limit_hit` headline. When set, the
  Telegram message leads with:
  `⏸️ Paused — Claude usage limit reached; distilled N before pausing, rest deferred to next run`
  where N is `summary["distilled"]` (known at abort time). Counts render as today; on a
  limited night `failed` is 0.

### D. `loom/headless-settings.json` — no-plugins settings file

- New file committed in the `loom/` package (so it survives the runtime clone's
  `git reset --hard origin/main`). Contents disable the Telegram plugin — verified on
  2026-07-12 to suppress the helper while `claude -p` still returns normally:
  `{"enabledPlugins": {"telegram@claude-plugins-official": false}}`
- `build_argv` resolves it via `Path(__file__).parent / "headless-settings.json"` — no
  dependence on `$HOME` or absolute paths.
- **Implementation check:** distill needs no plugins at all. During implementation,
  spawn-probe whether disabling *all* installed plugins (not just Telegram) also
  suppresses their MCP servers, and widen the file if confirmed. Minimum bar shipped:
  Telegram disabled (already proven).

## Data flow

`absorb()` → per target → `backend.complete("distill", …)` → `llm.run()` →
`subprocess.run(claude -p … --settings headless-settings.json)`.
On a usage limit: `llm.run` raises `UsageLimitError` → the distill loop sets
`limit_hit` and breaks → `absorb` skips weave and returns → `run-absorb.sh` formats the
summary → Telegram shows the "paused" message. Unfinished targets remain pending.

## Error handling

- **Usage limit** → `UsageLimitError` → abort, leave pending, report paused. (New.)
- **Generic distill error** → `LLMError` → `failed += 1`, continue. (Unchanged, but the
  error message is now populated from stdout+stderr.)
- **Bad/oversized transcript** → still a generic failure; unchanged.

## Testing (TDD; extends existing `tests/loom/`)

- `test_llm.py` (new) / `test_backends.py`:
  - `build_argv` includes `--settings` pointing at the shipped file.
  - `run()` raises `UsageLimitError` when a mocked `subprocess.run` returns non-zero with
    a session-limit message on stdout.
  - `run()` raises plain `LLMError` on a generic non-zero exit, and the message now
    contains the captured stdout.
- `test_run.py`:
  - `absorb` stops the distill loop on `UsageLimitError` (fake backend raises on the 2nd
    target): asserts `distilled == 1`, `limit_hit is True`, `failed == 0`, remaining
    targets are still pending (state not advanced), and weave is skipped.
- `test_summary.py`:
  - `format_run_summary` renders the paused headline when `limit_hit` is set.

Follow the existing backend-mocking pattern used in `tests/loom/test_run.py`.

## Rollout

- Land on `fix/loom-usage-limit-resilience`, tests green via `.venv/bin/pytest tests/loom`.
- Merge to `main` via the standard review + merge protocol.
- The runtime clone picks it up automatically on the next nightly run (its
  `git reset --hard origin/main`). The orphan-reaper stays as a backstop.
