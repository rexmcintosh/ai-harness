# MeetTrack v2 supply engine: discover resumed, two loops paused

**Record:** v1.1 · **Date:** 2026-09-18 (v1.0: 2026-09-09) · **Observed entries:** one active crontab line for `splash_poller/discover_meets.py` (hourly at :17) and two commented lines for `supervise_meets.py` (every 5 min) and `ingest_entries.py` (every 5 min) · **State:** discover active since 2026-09-18; supervise and ingest paused since 2026-08-15

This record holds a minimal run contract for the resumed discover loop and the open retirement question for the two loops that stay paused.

## Observed state

The crontab comment block says all three loops were paused on 2026-08-15 with the MeetTrack work on hold and the Supabase project also paused. A line added on 2026-09-18 says discover was resumed on the owner's instruction and that supervise and ingest stay paused. The discover line is uncommented; the other two are still commented, so cron does not run them. The code stays in `~/projects/splash_poller/`. The watchdog still tails `supervise.cron.log` and `ingest_entries.cron.log` in `watchdog/run.py`, counts poller processes through `watchdog/monitors.toml`, and keeps a Layer 3 MeetTrack absence alert; with those two loops paused the signals stay quiet. Remote database state was not checked.

v1.0 of this record asked for the contract to be written before any line was uncommented. Discover resumed first, so the section below closes that gap after the fact.

## Discover: run contract v1.0

**Purpose and authority.** Hourly at :17 UTC, read the public `live.swimrankings.net` root list, parse each upcoming live meet, and reconcile it into the `meet_registry` catalogue idempotently, so that a meet starting on a Saturday morning is known within the hour. It may read that public page and write `meet_registry` rows in the MeetTrack database. It must not launch pollers, ingest entries or results, or notify anyone: those belong to the two paused loops. `flock -n` on `~/.local/state/meettrack/discover.lock` skips an overlapping run.

**Secrets, names only:** the MeetTrack Supabase connection settings that `splash_poller` loads for its database client. They were not enumerated for this record.

**Success and evidence.** Success is a run that exits 0 and leaves `meet_registry` holding every meet on the live root list, with no duplicate rows. On 2026-09-18 at 23:15 UTC this could not be evidenced from the host: `splash_poller/logs/discover.cron.log` was empty with a last-modified date of 2026-08-16, which is consistent with a quiet success, with no run yet, or with a run that prints nothing on failure. Proposed measure: the script prints one summary line per run (rows seen, inserted, updated), so that an hourly line in the log is the evidence.

**Failure and escalation.** A failure lands only in the cron log. There is no owner notification, and the watchdog does not read this log. If the Supabase project is still paused, every run fails until it is resumed; that state was not checked.

## Supervise and ingest: still a retirement question

Correction A of the July assessment: a loop that cannot state a measurable success condition is a retirement candidate. While paused, these two loops have no active success condition, and a commented cron line is dormant surface area, which is the owner's stated removal target. Discover alone finds meets; without supervise and ingest nothing polls or ingests them, so the owner's resume of discover implies a decision on these two is coming.

1. If they resume: extend this record with their run contracts (scope, success condition, secrets, escalation) first, then uncomment the lines.
2. If they do not: remove the two commented lines from the crontab, and drop their entries from `watchdog/run.py` and `watchdog/monitors.toml`, in one separately approved scheduler change. Keep the repository.

Nothing here edits the crontab or the watchdog.
