# MeetTrack v2 supply engine: paused, retirement recommendation

**Record:** v1.0 · **Date:** 2026-09-09 · **Observed entries:** three commented crontab lines for `splash_poller/supervise_meets.py` (every 5 min), `discover_meets.py` (hourly at :17), and `ingest_entries.py` (every 5 min) · **State:** paused since 2026-08-15

This is not a run contract. It records why no contract is active and what the owner must decide.

## Observed state

The crontab comment block says the loops were paused on 2026-08-15 with the MeetTrack work on hold and the Supabase project also paused. The three job lines are commented out, so cron does not run them. The code stays in `~/projects/splash_poller/`. The watchdog still tails `supervise.cron.log` and `ingest_entries.cron.log` in `watchdog/run.py`, counts poller processes through `watchdog/monitors.toml`, and keeps a Layer 3 MeetTrack absence alert; with the loops paused those signals stay quiet. Remote database state was not checked.

## Why this is a retirement candidate

Correction A of the July assessment: a loop that cannot state a measurable success condition is a retirement candidate. While paused, these loops have no active success condition. The one they had (every live meet discovered within the hour, and results ingested without overlapping ticks) depends on a Supabase project that is itself paused. A commented cron line is dormant surface area, and dormant surface area is the owner's stated removal target.

## Recommendation

1. The owner decides whether MeetTrack resumes. Until then, no run contract is written.
2. If it does not resume: remove the three commented lines and their header block from the crontab, and drop the MeetTrack entries from `watchdog/run.py` and `watchdog/monitors.toml`, in one separately approved scheduler change. Keep the repository.
3. If it resumes: write the run contract first (scope, success condition, secrets, escalation), then uncomment the lines. Contracts first.

Nothing here edits the crontab or the watchdog.
