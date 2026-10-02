# Swimtrack website writing engine

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed entrypoints:** `swimtrack-website/engine/morning-run.sh`, `engine/poller-cron.sh`

## Purpose and authority

**Default mode:** deploy-capable, change-producing content workflow. Weekday morning work collects, judges, writes, previews, and digests. The weekday poller processes owner decisions. Both use the shared repository lock; the canonical editorial and decision state is in the Swimtrack repository’s `engine/` files and the decision backend.

The runners may make the content changes their prompts and decision processor authorize, including publication paths available to their credentials. They must not overlap a locked repository run. They exclude unrelated repositories and any action outside the engine’s prompt or decision data.

**Secrets, names only:** `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `BENTO_SHARED_PUBLISHABLE_KEY`, `BENTO_SHARED_SECRET_KEY`, `BENTO_SWIMTRACK_SITE_UUID`, `SWIMTRACK_DRAFT_APPROVAL_SECRET`, `N8N_WEBHOOK_URL`, and additional keys loaded by the poller from `~/.env`.

## Success and evidence

Success is a non-skipped locked run with a completed engine result, recorded in `~/projects/.session-gc/swimtrack-engine-morning.log` or `~/projects/.session-gc/swimtrack-engine-poller.log`, plus the matching `engine/last-run.json`, decision/verdict record, and deployed or draft state required by that decision. Test evidence is repository-owned; inspect its engine tests before changing a runner.

## Failure, escalation, and gaps

A lock conflict intentionally skips the tick. Neither runner has a dedicated alert. The poller sources the full `~/.env`, so observed credential authority is broader than the named need. This contract is outside the owning repository and is not an enforced publish gate.
