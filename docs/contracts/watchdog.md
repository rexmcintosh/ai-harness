# Automation watchdog

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed entrypoint:** `watchdog/run-watchdog.sh`

## Purpose and authority

**Default mode:** report-only diagnosis, with notification. Every 30 minutes, collect health, freshness, process, disk, and configured Supabase rate signals. A clean poll records the result. An escalated poll gives a read-only investigator evidence and sends its diagnosis to the owner’s Telegram chat.

It owns `watchdog/logs/` and watchdog state. It may read the listed automation logs, system service state, disk state, and configured read-only Supabase checks. It must not modify production, repos, services, or monitored data. Canonical alert-suppression state is `watchdog/state.json` and metrics history.

**Secrets, names only:** `SUPABASE_SERVICE_ROLE_KEY`, `TELEGRAM_BOT_TOKEN`.

## Success and evidence

Success is a parseable `WATCHDOG_JSON` result plus a timestamped `runs.log` line; an escalation also needs a `SENT` result. Inspect `watchdog/logs/runs.log`, `watchdog/logs/runs.log.err`, `watchdog/state.json`, and `watchdog/metrics-history.json`. The current pre-check fails closed when it emits no JSON.

## Failure, escalation, and gaps

The runner records a meta-alert when the pre-check is malformed and exits nonzero on failed alert delivery. Telegram is the only configured owner channel. The README describes the boundary, but the contract is documentary until runtime checks enforce it.
