# Attain Prep parent digest

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed command:** `npm run --silent digest:market -- --send`

## Purpose and authority

**Default mode:** change-producing email communication. Each Monday at 13:00 UTC, send a parent digest only for families with a live seat and record the send. The surrounding `CRON_TZ=Europe/Lisbon` directive is declared configuration but does not alter cron scheduling on this host. Canonical idempotency state is the `digest_sends` table, keyed by student and week.

It may read eligible family and student learning data, reserve or release a send, send the digest through Bento, and update the send record. It must not email families without a live seat, bypass opt-outs, or duplicate a reserved weekly send.

**Secrets, names only:** `BENTO_ATTAIN_SITE_UUID`, `BENTO_SHARED_PUBLISHABLE_KEY`, `BENTO_SHARED_SECRET_KEY`, `VITE_SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.

## Success and evidence

Current process evidence is one unique `digest_sends` reservation and a matching `sent ->` line after `transport.sendMail` succeeds. Inspect `sat-prep/tmp/digest.log`, the reservation row, and Bento provider records. The reservation is written before transport, and its timestamp does not prove delivery; a crash can leave a claimed slot without a send. The strongest outcome measure is provider acceptance or delivery for every intended, opted-in live-seat recipient, with no duplicate provider send.

## Failure, escalation, and gaps

The runner releases a reservation when `sendMail` throws, but a process crash after reservation is a recovery gap. Cron logs failures only; it has no owner alert. The cron command sources all of `~/.env` before the package script explicitly loads `.env.market`; the named secret list is not enforced custody. Do not infer delivery from a process exit, reservation row, or log line without Bento provider evidence.
