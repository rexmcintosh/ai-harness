# Attain Prep feedback sync

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed command:** `npm run --silent feedback:market`

## Purpose and authority

**Default mode:** change-producing queue handoff. Every 15 minutes, copy each new in-app feedback report into the shared backlog, commit and push that durable queue update, update source-report status, and best-effort notify Telegram. Canonical work intake becomes the item in `/home/dev/projects/backlog/backlog.yaml`; the originating report remains in Attain Prep’s `feedback_reports` table.

It may create only rendered feedback backlog items, commit the backlog repository, update report status, and send its notification. It must not work, merge, deploy, or delete the generated backlog item.

**Secrets, names only:** `VITE_SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `TELEGRAM_BOT_TOKEN`, and Git remote credentials managed outside the script.

## Success and evidence

Success is a new durable backlog item, a matching source report status, and the feedback-sync summary in `sat-prep/tmp/feedback-sync.log`. Inspect the backlog Git history, `backlog.yaml`, and the report row. The script’s lock prevents overlap; Git or Telegram failure leaves the report queued, so a zero process exit alone is not enough.

## Failure, escalation, and gaps

Lock contention skips a run. The script logs Git and Telegram problems but deliberately keeps rows queued for retry. It has no independent alert path. This is a cross-repository write and push on the clock; its authority should be enforced by the source script and backlog schema, not this document.
