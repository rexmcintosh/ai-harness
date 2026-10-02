# Romance Empire Notion ops loop

**Contract:** v1.0 · **Date:** 2026-09-18 · **Observed entrypoints:** `romance-empire/scripts/ops-tick.sh` (every 10 minutes) and `romance-empire/scripts/ops-compose.sh` (06:00 UTC)

## Purpose and authority

**Default mode:** owner-triggered actions plus a daily digest. The loop lets the owner run Romance Empire's day from one Notion table instead of from Claude sessions. The tick reads the Notion ops database, reconciles it with the repository's sources of truth, and carries out the action the owner selected by changing a row's Status. The compose writes the "Today" page and sends one Telegram line with the counts and the page link.

An owner Status change is the only launch signal. On it the tick may run `backlog-run approve` (merge, push, delete the branch), `backlog-run drop`, `backlog-run rework <id>` or `backlog-run work --only <id>`, and may record the result in the row. It commits only `ops/ops-state.yaml` and files its hooks wrote, only when the checkout is on `main`, and it never pushes by itself: the runner's `approve` is the only push in the loop. It never deletes Notion rows or state entries. On-demand sessions are capped by the loop's own config (`config/ops.yaml`: 4 per day, 20 USD each, as read on 2026-09-18).

As installed on 2026-09-18 the cron line passes no `--create-only` flag, so actions are live. The design's first-run safety mode (rows appear, nothing fires) is no longer in force.

**Schedule as installed:** the two lines sit under `CRON_TZ=Europe/Lisbon`, which this host's cron does not apply to scheduling (see the index). The compose therefore runs at 06:00 UTC, which is 07:00 in Lisbon during summer time, not 06:00 Lisbon as its script header says.

**Secrets, names only:** `NOTION_TOKEN` (the Romance Empire scanner connection). Both wrappers source all of `~/.env`, so observed custody is broader than this list. The Telegram line goes out through the existing sender.

## Success and evidence

Success for a tick is one `tick <timestamp>: created N updated N adopted N missed N acted N refused N orphans N errors 0` line in `~/.local/state/ops/tick.log` every ten minutes. Success for the compose is one `compose: Ops <date>: ... telegram sent` line per day in `~/.local/state/ops/compose.log`, with the Today page reachable at the logged link. Both shapes were present on 2026-09-16, 2026-09-17 and 2026-09-18. For an action, success is the row's Result carrying the command, the commit or run id, the cost and the log path, and the matching `ops: <key> -> <status>` commit in the Romance Empire repository.

A tick line alone proves only that the poller ran. `refused` and `missed` counts that stay above zero for days mean decisions are waiting on the owner, not that the loop is healthy. Proposed outcome measure: decisions on the Today page trend toward zero each day without a Claude session being opened for routine approvals.

## Failure, escalation, and gaps

The loop fails closed: a Notion error, a partial page, or a row whose key is unknown to both the state file and the providers makes that tick act on nothing and log it. An intent marker is written before every outward action and blocks that key if the action does not report back; markers never expire by themselves, so a stuck marker needs the owner or a session. A missing `NOTION_TOKEN` prints a notice and exits 0, which is a deliberate skip and not a success. `flock` on `~/.local/state/ops/lock` allows one poller at a time, and `backlog-run` exit 75 (runner busy) refunds the day's on-demand slot.

There is no independent escalation: a tick that stops running is visible only as a stale log and a Today page that does not change. Since 2026-09-18 the watchdog's shadow mode (`[jev_shadow]` in `watchdog/monitors.toml`) reads `tick.log` and `compose.log`, but only to record a second-opinion verdict for comparison; by its own rule it never alerts, so it is not an escalation path. The contract lives outside the owning repository (`romance-empire`), the same ownership gap recorded for the content engines and the engage scanner. The design is that repository's `docs/superpowers/specs/2026-09-09-notion-ops-loop-design.md`.
