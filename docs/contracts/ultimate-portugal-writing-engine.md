# Ultimate Portugal writing engine

**Contract:** v1.1 · **Date:** 2026-09-09 · **Observed entrypoints:** `ultimate-portugal/engine/morning-run.sh`, `engine/poller-cron.sh`, `engine/seo-run.sh`, `engine/refresh-run.sh next`

v1.1 adds the weekday refresh slot installed in the crontab on 2026-09-07. v1.0 (2026-09-05) covered the first three entrypoints.

## Purpose and authority

**Default mode:** deploy-capable, change-producing editorial workflow. The weekday morning run performs the engine’s headless workflow; the poller processes decisions; the Monday SEO transaction decides, acts through its checker, and reports to the owner. Canonical editorial state is the Ultimate Portugal `engine/` data and decision records.

The weekday 10:00 UTC refresh slot asks `scripts/refresh-queue.mjs` which one article is due and in which mode, then runs a headless session for that article under a three-hour timeout. Diagnose records whether a live page is still true and still competitive; refresh rewrites a page the diagnosis condemned into `drafts/refresh/` for one-click review. Neither mode publishes on its own. Canonical refresh state is `engine/refresh/ledger.json`; the session stamps its own outcome there, so the ledger, not the wrapper exit code, is the record of what happened.

The jobs may mutate and publish only work authorized by their engine prompts, decision data, and SEO transaction. Shared writer locking prevents conflicting edits. They must not act on unrelated repositories or continue past a failed SEO transaction stage. The refresh slot must not start while a flag fix holds that page's lock, while the Monday SEO run holds its lock, or while another refresh session holds the engine refresh lock; in each case it records a skip in the ledger and emails the owner instead.

**Secrets, names only:** `VENICE_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `BENTO_SHARED_PUBLISHABLE_KEY`, `BENTO_SHARED_SECRET_KEY`, `BENTO_ULTIMATEPORTUGAL_SITE_UUID`, `DRAFT_APPROVAL_SECRET`, `DATAFORSEO_USERNAME`, `DATAFORSEO_PASSWORD`, `APIFY_TOKEN`, plus additional keys loaded by the poller from `~/.env`. The refresh wrapper exports its keys by name from `~/.env`, which is the narrow custody the other entrypoints lack.

## Success and evidence

Success is a completed, non-skipped lock-held run plus the matching engine decision, revision, deployment, or owner-report record. For the refresh slot, success is a `START`/`END rc=0` pair in the log plus a ledger state the session landed itself (`refresh_queued`, `no_change`, `ready`, `failed`, or `skipped`) and one owner email for any non-idle exit. An idle day is a `SKIP next` line with no email. Inspect `~/projects/.session-gc/writing-engine-morning.log`, `writing-engine-poller.log`, `writing-engine-seo.log`, `writing-engine-refresh.log`, the Ultimate Portugal `engine/` state, `engine/refresh/ledger.json`, and the SEO transaction report. A process exit does not prove a publish or email reached its recipient.

## Failure, escalation, and gaps

Morning and SEO runs have timeouts; lock contention is a skip or bounded wait. There is no common alert for failed scheduled runs. The refresh slot is the exception: the wrapper mails the owner through `scripts/engine/bento-mail.mjs` on every non-idle exit and writes `MAIL-FAILED` to the log when that send fails. The 2026-09-08 run shows the path working: the session failed because the Venice key returned HTTP 402 (spend limit exceeded), the ledger recorded it, and the mail was accepted with status 200. The poller imports the whole `~/.env`, which exceeds narrow secret custody. The refresh wrapper's header says cron runs `next` twice a weekday; the installed crontab has one slot at 10:00 UTC, a documentation mismatch to resolve in the owning repository. Contract ownership belongs with this site, not this automation repository.
