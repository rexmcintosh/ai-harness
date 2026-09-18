# Scheduled-loop contract index

**Inventory date:** 2026-09-05, refreshed 2026-09-09. **Contract format:** v1.0. These documents record observed configuration and intended operating boundaries. They do not change, validate, or enforce a cron job, timer, or external service.

The 2026-09-05 inventory came from `crontab -l` and `systemctl --user list-timers --all`. The 2026-09-09 refresh could not execute either command (the unattended runner denied both), so it used the newest crontab backup that session-gc wrote, `~/projects/.session-gc/crontab.backup.20260907150315` (2026-09-07 15:03 UTC), plus the systemd unit files, enablement links, and timer stamps on disk. The refresh found two cron entries added after 2026-09-05 and one dormant user timer; all three now have a row below. See "Re-verifying this inventory" for the confirmation step. A lifecycle may have more than one trigger. A successful process exit proves only that the entrypoint exited successfully; the evidence named in its contract is needed for the stated outcome.

Local evidence resolves the scheduler timezone: cron `3.0pl1-184ubuntu2` schedules all user jobs in the daemon timezone, and this host reports `Etc/UTC`. Its local `crontab(5)` says a user-set `TZ` affects only the child process, not scheduling. Therefore every cron time below is UTC; `CRON_TZ` entries are recorded as declared environment only. The same behavior is described in the [Ubuntu manual for the cron 3.0pl1 family](https://manpages.ubuntu.com/manpages/jammy/man5/crontab.5.html); do not substitute a Cronie or systemd-cron manual, which describes a different implementation.

| State | Schedule as installed | Entrypoint(s) | Lifecycle disposition | Contract or reason |
|---|---|---|---|---|
| active | 07:00, 18:00 UTC; declared `CRON_TZ=Europe/Lisbon` not scheduler-effective | `/home/dev/projects/ai-harness/bebop/run-briefing.sh morning`; same with `evening` | Bebop briefing | [bebop-briefings.md](bebop-briefings.md) |
| paused | every 5 min; hourly; every 5 min | `/home/dev/projects/splash_poller/supervise_meets.py`; `/home/dev/projects/splash_poller/discover_meets.py`; `/home/dev/projects/splash_poller/ingest_entries.py` | MeetTrack v2 supply engine | Paused in crontab since 2026-08-15. Remote database state was not checked. No run contract is active. Retirement recommendation: [meettrack-supply-engine.md](meettrack-supply-engine.md). |
| active | every 30 min | `/home/dev/projects/ai-harness/watchdog/run-watchdog.sh` | Automation watchdog | [watchdog.md](watchdog.md) |
| active | Monday 04:00 UTC; declared `CRON_TZ=Europe/Lisbon` not scheduler-effective | `/home/dev/projects/ai-harness/council/scripts/security-sweep.sh` | Council security sweep | [council-security-sweep.md](council-security-sweep.md) |
| active | 08:00, 21:00, 23:00, 23:40 UTC | `/home/dev/.local/bin/diem drain --checkpoint` | DIEM checkpoint drain | [diem-drain.md](diem-drain.md) |
| active | every minute | `/home/dev/.local/bin/agents once` | Agent-attention nudge | [agents-nudge.md](agents-nudge.md) |
| active | 02:00 UTC | `/home/dev/loom-runtime/loom/run-absorb.sh` | Loom session learning | [loom-absorb.md](loom-absorb.md) |
| active | every 10 min snapshot; Monday 08:00 UTC sweep | `/home/dev/.local/bin/session-gc snapshot`; `/home/dev/.local/bin/session-gc sweep` | Session worktree hygiene | [session-gc.md](session-gc.md) |
| active | Monday 07:30 UTC; declared `CRON_TZ=Europe/Lisbon` not scheduler-effective | `/home/dev/projects/ai-harness/setup/superpowers-slim-preamble/reapply.sh` | Superpowers preamble reapply | [superpowers-preamble.md](superpowers-preamble.md) |
| active | Tuesday 07:15 UTC; declared `CRON_TZ=Europe/Lisbon` not scheduler-effective | `/home/dev/projects/ultimate-portugal/scripts/verify-rents.mjs` | Ultimate Portugal rent verification | [ultimate-portugal-rent-verification.md](ultimate-portugal-rent-verification.md) |
| active | weekdays 05:00 UTC; every 15 min 06:00–21:00 UTC | `/home/dev/projects/swimtrack-website/engine/morning-run.sh`; `/home/dev/projects/swimtrack-website/engine/poller-cron.sh` | Swimtrack website writing engine | [swimtrack-writing-engine.md](swimtrack-writing-engine.md) |
| active | weekdays 05:30 UTC; every 15 min 06:00–21:00 UTC; Monday 09:00 UTC; weekdays 10:00 UTC (added 2026-09-07) | `/home/dev/projects/ultimate-portugal/engine/morning-run.sh`; `/home/dev/projects/ultimate-portugal/engine/poller-cron.sh`; `/home/dev/projects/ultimate-portugal/engine/seo-run.sh`; `/home/dev/projects/ultimate-portugal/engine/refresh-run.sh next` | Ultimate Portugal writing engine | [ultimate-portugal-writing-engine.md](ultimate-portugal-writing-engine.md) (v1.1) |
| active | every 15 min | `cwd=/home/dev/projects/sat-prep`; `npm run --silent feedback:market` | Attain Prep feedback sync | [attainprep-feedback-sync.md](attainprep-feedback-sync.md) |
| active | Monday 13:00 UTC; declared `CRON_TZ=Europe/Lisbon` not scheduler-effective | `cwd=/home/dev/projects/sat-prep`; `npm run --silent digest:market -- --send` | Attain Prep parent digest | [attainprep-parent-digest.md](attainprep-parent-digest.md) |
| active | 03:00 UTC | `/home/dev/.local/bin/backlog-run work` | Backlog runner | [backlog-run.md](backlog-run.md) |
| active | every 10 min | `cwd=/home/dev/projects/sat-prep`; `npm run --silent bento:market` | Attain Prep Bento sync | [attainprep-bento-sync.md](attainprep-bento-sync.md) |
| active | 05:30 UTC; declared `CRON_TZ=UTC` (added by 2026-09-07) | `/home/dev/projects/romance-empire/scripts/engage-scan.sh heron-creek` | Engage scanner (TikTok comment pilot) | [engage-scanner.md](engage-scanner.md) |
| external | daily after activation and startup + 5 min | `launchpadlib-cache-clean.timer` → `/usr/lib/systemd/user/launchpadlib-cache-clean.service`; enabled by the root-owned link in `/etc/systemd/user/timers.target.wants/` | OS-owned cache cleanup | It deletes cache files older than 30 days under `~/.launchpadlib`. It is not a project automation loop and has no project owner or contract here. |
| dormant | every 10 min when enabled; no enablement link on disk; last elapsed 2026-08-26 10:16 UTC | `~/.config/systemd/user/telegram-orphan-reaper.timer` → `telegram-orphan-reaper.service` → `~/.local/bin/telegram-orphan-reaper.sh` | Telegram MCP orphan reaper | [telegram-orphan-reaper.md](telegram-orphan-reaper.md); owner decision: re-enable or remove |
| dormant | daily when enabled; no enablement link on disk | `/usr/lib/systemd/user/systemd-tmpfiles-clean.timer` | OS-owned temp cleanup | Unit file shipped by systemd, not linked from any `timers.target.wants`, and absent from the 2026-09-05 timer listing. Not a project loop; no action. |

Not loops, listed so nobody re-discovers them: `~/.config/systemd/user/session-bridge.service` is a persistent service (restart-always, wanted by `default.target`), and `home-dev-mnt-mini.mount` / `.automount` are mount units. They have no schedule and are outside this index.

## Inventory totals

- 25 active cron entries, grouped into 16 lifecycle contracts.
- 3 commented, explicitly paused MeetTrack entries, grouped into one paused lifecycle with a written retirement recommendation.
- 1 active user timer, explicitly external to this project.
- 1 dormant user timer with a contract and a pending owner decision; 1 dormant OS timer unit, out of scope.

## Loop-retirement review (Correction A)

The July assessment's Correction A: a loop that cannot state a measurable success condition is a retirement candidate. Flag it; never retire it from a document. This table applies that one test to every lifecycle above. "Repair" means the success condition is clear but the evidence or delivery for it is missing.

| Lifecycle | Contract | Measurable success condition | Retire? |
|---|---|---|---|
| Bebop briefing | bebop-briefings.md v1.0 | Yes: one `SENT` line with `rc=0` and one cursor advance per scheduled slot. | No. |
| MeetTrack v2 supply engine | meettrack-supply-engine.md (retirement record) | None while paused; the prior condition depends on a paused Supabase project. | **Candidate.** Owner decides resume or remove; see the record. |
| Automation watchdog | watchdog.md v1.0 | Yes: a parseable `WATCHDOG_JSON` result and `runs.log` line per poll, plus `SENT` on escalation. | No. |
| Council security sweep | council-security-sweep.md v1.0 | Yes: `rc=0` with a logged finding count and coverage. | No. Repair: the Telegram send failure is ignored. |
| DIEM checkpoint drain | diem-drain.md v1.0 | Yes: a checkpoint summary appended with each run item `ok`. | No. |
| Agent-attention nudge | agents-nudge.md v1.0 | Yes: one delivered nudge per newly needy unattached session. Delivery is not evidenced. | No. Repair candidate; reconsider only after the session-bridge overlap audit. |
| Loom session learning | loom-absorb.md v1.0 | Yes: `absorb rc=0`, an atomically written `pending.json`, and a reviewable shadow-branch diff. | No. |
| Session worktree hygiene | session-gc.md v1.0 | Yes: a snapshot ref for every dirty eligible worktree; a fresh weekly report that deletes nothing. | No. |
| Superpowers preamble reapply | superpowers-preamble.md v1.0 | Yes: `applied` or `already applied`, valid hook JSON, zero exit in the log. | No. Note the dependency on an unversioned plugin cache path. |
| Ultimate Portugal rent verification | ultimate-portugal-rent-verification.md v1.0 | Yes: exit 0 only when every applicable cell passes; drift surfaces as a proposal. | No. Repair: no owner handoff for a proposal. |
| Swimtrack website writing engine | swimtrack-writing-engine.md v1.0 | Yes: a non-skipped locked run with `engine/last-run.json` and the matching decision record. | No. Move the contract to the owning repository. |
| Ultimate Portugal writing engine | ultimate-portugal-writing-engine.md v1.1 | Yes: per-slot run records; for refresh, a session-landed ledger state and one owner email per non-idle exit. | No. Move the contract to the owning repository. |
| Attain Prep feedback sync | attainprep-feedback-sync.md v1.0 | Yes: a new durable backlog item with a matching source report status. | No. |
| Attain Prep parent digest | attainprep-parent-digest.md v1.0 | Yes: one unique weekly reservation per student and provider acceptance for every intended recipient. | No. |
| Attain Prep Bento sync | attainprep-bento-sync.md v1.0 | Yes: `bento_synced_at` updates and each planned send fired once or recorded quiet. | No. |
| Backlog runner | backlog-run.md v1.0 | Yes: a run record, a review file, and an item transition consistent with the outcome. | No. |
| Engage scanner | engage-scanner.md v1.0 | Yes: `notion: N row(s) created` and `wrote N target(s)` per run, with the rows in Notion. | No. Move the contract to the owning repository. |
| Telegram MCP orphan reaper | telegram-orphan-reaper.md v1.0 (dormant) | Yes: zero Telegram MCP `bun` processes without a live `claude` ancestor after each tick. | Not by this rule. **Dormant since 2026-08-26**; owner decides re-enable or remove by 2026-09-25. |
| launchpadlib cache clean | none (external) | OS-owned. | Out of scope. |
| systemd-tmpfiles-clean | none (external, not enabled) | OS-owned. | Out of scope. |

Result: no active project loop fails the Correction A test. The two flagged entries are both already stopped (one paused, one dormant) and each needs one owner decision, recorded in its document.

## Re-verifying this inventory

Run on `mesh-vps` as `dev`:

```
crontab -l | diff - ~/projects/.session-gc/crontab.backup.20260907150315
systemctl --user list-timers --all --no-pager
```

An empty diff and a timer list in which only `launchpadlib-cache-clean.timer` is active confirm the table. Any other line is a new or changed loop and needs a row here before it is treated as covered.

## Cross-cutting gaps and retirement candidates

| Finding | Evidence | Disposition |
|---|---|---|
| Cron entries do not point to these contracts. | The active crontab commands invoke entrypoints directly. | Mismatch. Add pointers only in a separately approved scheduler change. |
| `CRON_TZ` comments and legacy documentation conflict with the installed scheduler’s UTC behavior. | Installed `cron 3.0pl1-184ubuntu2`, local `crontab(5)`, and host `Etc/UTC`; Bebop README labels 07:00/18:00 Lisbon while its runner uses Europe/Lisbon for displayed time. | Documentation ambiguity. Classify each schedule as owner-local or UTC-budget before changing any schedule. |
| Several jobs load broad `~/.env` despite narrow needs. | Ultimate Portugal poller; Swimtrack poller; Council security sweep; Loom runner; Engage scanner wrapper. The Attain Prep digest cron also sources `~/.env` before its package script loads `.env.market`. The Ultimate Portugal refresh wrapper is the counter-example: it exports named keys only. | Mismatch. Narrow secret custody at each entrypoint before treating the contract secret list as an access control. |
| `agents once` has no durable delivery result. Cron discards stdout/stderr, and its state file records a transition even if Telegram delivery fails. | `/home/dev/.local/bin/agents`; crontab redirection to `/dev/null`. | Repair candidate. It can state a measurable delivery goal, so it is not a loop-retirement candidate on that rule alone. Reconsider retirement only after an overlap audit shows another loop covers the same owner-attention need. |
| Security-sweep delivery is best effort. | `council/scripts/security-sweep.sh` ignores `tg-send` failure. | Mismatch. Its scan result can exist while the owner receives no summary. |
| Content-engine and scanner jobs are deploy-capable or write to external services by their current entrypoints, but their contracts are outside their owning repos. | Swimtrack and Ultimate Portugal engine runners; Romance Empire engage scanner. | Ownership gap. Move each contract to its owning repository, then leave a pointer here; do not create two canonical contracts. |
| Two stopped entries carry a pending owner decision. | MeetTrack lines commented since 2026-08-15; reaper timer not enabled since 2026-08-26. | Decision needed. Each document states the two options and the check that decides between them. No scheduler edit here. |
| The 2026-09-09 refresh is based on a two-day-old snapshot. | The runner denied `crontab -l` and `systemctl --user`. | Operator confirms with the two commands under "Re-verifying this inventory". |
