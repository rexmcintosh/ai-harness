# Bebop briefings

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed entrypoint:** `bebop/run-briefing.sh`

## Purpose and authority

**Default mode:** change-producing communication. At 07:00 and 18:00 UTC, compose a Gmail and Calendar delta briefing and send it to the configured owner Telegram chat. The runner formats briefing content in Europe/Lisbon. Canonical cursor state is `bebop/state.json`; it advances only after a successful send.

It may read the fixed briefing prompt, Gmail and Calendar through the allowed MCP tools, and Loom’s `pending.json`. It may send one briefing or one failure notice. It does not reply to email, edit calendar data, change source repositories, or send to another recipient.

**Secrets, names only:** `TELEGRAM_BOT_TOKEN`; Gmail and Google Calendar connector credentials are resolved by the configured MCP. The chat identifier is configuration, not a secret.

## Success and evidence

Success is a `rc=0` `SENT` line for the scheduled mode and an advanced `last_run_epoch` in `bebop/state.json`. Inspect `bebop/logs/runs.log`, `bebop/logs/runs.log.err`, `bebop/logs/cron.log`, and `bebop/state.json`. The runner’s send receipt is the strongest current evidence; a process exit alone is insufficient. Proposed system check: exactly one provider-accepted briefing and one cursor advance per scheduled delivery. Human receipt and usefulness require separate owner feedback; neither is proved by send acceptance.

## Failure, escalation, and gaps

A failed compose or send exits nonzero and attempts a Telegram failure notice. Review the log directory after a missing or failed run. The notification uses the same delivery path that may have failed, so it is not independent escalation. The active cron command has no pointer to this contract.
