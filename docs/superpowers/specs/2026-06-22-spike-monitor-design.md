# Spike monitor — design of record (2026-06-22)

> **Why:** Over the weekend splash_poller had a restart storm (supervise `relaunched=12`,
> ~33 poller processes) that blew through a pile of Supabase writes. The watchdog ran
> every 30 min the whole time and logged "all checks ok" — because it only detects
> *failures* (error words in logs), is blind to *volume*, and has no Supabase visibility.
> This adds rate/spike detection. Approved by Rex: build Layers 1+2+3; read-only Supabase OK.

## The shift

From **"did something error?"** to **"is something doing far more than normal?"** Reuses the
watchdog's existing triage / flap-suppression / Telegram escalation untouched — a spike is
just another `CheckStatus`.

## Layer 1 — local rate checks

Read *counts* (not error words) from sources that already emit them, window them, and alert
on hard budgets:
- **supervise log** counters `relaunched` / `launched` (summed over the recent tail).
- **ingest log** counter `ingested`.
- **process count** — live `poller.py` processes (`ps`).

## Layer 2 — Supabase visibility (where the cost lands)

Each poll, read **row counts** for the hot tables via PostgREST
(`HEAD ?select=* , Prefer: count=exact, Range: 0-0` → `Content-Range: …/<total>`), read-only.
Compare to the previous reading (stored in `metrics-history.json`) to get **rows/hour**, and
alert on a per-hour budget. Hot tables (verified live): `results` (~184k), `splits` (~309k),
`swimmers` (~28k). Credentials: `SUPABASE_SERVICE_ROLE_KEY` from env (sourced by the runner
from splash_poller's `.env`; never committed). URL + table list + budgets live in
`watchdog/monitors.toml` (non-secret config-as-data).

## Layer 3 — provider-side backstop

A Supabase **spend cap + usage alert** in the dashboard (project `moaagxigxjyuuqygoqfg`). The
seatbelt that stops spend even if the VPS is down. Manual dashboard step — documented in the
README; not code.

## Detection method

**Hard budgets first** — no cold-start, no tuning, value immediately. Every metric value is
**logged on every poll even when ok**, so we bank a baseline and can add statistical
thresholds (×k over trailing median) later. Thresholds are deliberately generous and marked
tunable; calibrate from the logged history.

Initial budgets (tunable in `monitors.toml`):
| metric | warn | crit |
|---|---|---|
| supervise `relaunched` (window) | 8 | 20 |
| supervise `launched` (window) | 15 | 40 |
| `poller.py` processes | 12 | 25 |
| ingest `ingested` (window) | 500 | 5000 |
| Supabase rows/hour (per hot table) | 20000 | 100000 |

## Architecture

- **`watchdog/metrics.py`** (pure, unit-tested):
  - `sum_counter(log_text, key, *, lines)` — sum `key=<int>` over the last `lines` lines.
  - `count_matches(text, pattern)` — count matching lines (process count from `ps`).
  - `check_budget(name, value, *, warn_at, crit_at, unit)` → `CheckStatus`.
  - `parse_count_header(content_range)` — PostgREST `Content-Range` → total int.
  - `check_rate(name, current, prev, elapsed_s, *, warn_per_hour, crit_per_hour, unit)` →
    `CheckStatus` (rows/hour from the delta; first run with no prev → ok, just records).
- **`watchdog/monitors.toml`** — non-secret config: log-counter monitors, process monitors,
  Supabase url + tables + budgets. (`monitors.example.toml` committed; real one too — no secrets in it.)
- **`watchdog/run.py`** — `collect(now, prior_metrics)` now returns `(statuses, new_metrics)`;
  `main()` loads/saves `metrics-history.json` (gitignored) and logs every metric value.
- **`watchdog/run-watchdog.sh`** — sources the Supabase key from splash_poller's `.env`
  (path overridable via `WATCHDOG_ENV_FILE`) so the pre-check can query counts.
- Supabase HTTP count lives in a thin `supabase_count()` collector (uses `requests`); the
  parsing (`parse_count_header`) is the tested part.

## Out of scope (flagged separately)

The splash_poller **restart-storm root cause** (supervise relaunching pollers in a loop) is a
real bug. The monitor will *alert* on it; it won't *fix* it. Tracked as its own task.

## Boundary

Unchanged: the watchdog alerts and proposes. No auto-throttle / circuit-breaker in this pass
(alert-only until detection is trusted). The escalation carries a kill command on a silver platter.
