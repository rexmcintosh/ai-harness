# Attain Prep Bento sync

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed command:** `npm run --silent bento:market`

## Purpose and authority

**Default mode:** change-producing contact and lifecycle synchronization. Every ten minutes, synchronize each family’s permitted state and lifecycle events to Bento, write `bento_synced_at`, and send only planned, deduplicated lifecycle mail. Canonical app state is Attain Prep’s database; Bento is the delivery projection.

It may read family, profile, seat, activity, digest, feedback, ledger, user, and answer data; update Bento subscribers and tags; write sync state; and fire planned email events. It must honor opt-outs and reserve events before sending, and it must not invent family or learning records.

**Secrets, names only:** `BENTO_ATTAIN_SITE_UUID`, `BENTO_SHARED_PUBLISHABLE_KEY`, `BENTO_SHARED_SECRET_KEY`, `VITE_SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.

## Success and evidence

Success is a completed sync summary, matching `bento_synced_at` updates, and event records showing that each planned send fired once or was recorded quiet/already known. Inspect `sat-prep/tmp/bento-sync.log`, relevant database tables, and Bento subscriber and delivery records. The script has a dry-run mode for change review.

## Failure, escalation, and gaps

The cron and script locks avoid overlap. Failure is captured in the temporary log; no independent alert is configured. The package script explicitly loads `.env.market`; review that file’s scope before treating the named secret list as enforced custody. This is a documentation mismatch, not a claim that credentials are absent.
