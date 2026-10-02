# Agent-attention nudge

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed command:** `/home/dev/.local/bin/agents once`

## Purpose and authority

**Default mode:** notification only. Every minute, inspect unattached tmux sessions and send one Telegram nudge when a session newly becomes `BLOCKED` or `WAITING`. Canonical de-duplication state is `~/.cache/agents/<session>`.

It may read tmux panes and the Telegram token file, create or remove its own state markers, and send the one edge-triggered nudge. It must not type into tmux, approve a permission, modify a session, or send repeat nudges while the same state persists.

**Secrets, names only:** `TELEGRAM_BOT_TOKEN`.

## Success and evidence

The intended success is one delivered nudge per newly needy unattached session, followed by no repeat until that session leaves and re-enters the state. Current evidence is only the state marker under `~/.cache/agents/`; cron discards stdout and stderr. A marker is not delivery evidence because the script writes it even if the send fails.

## Failure, escalation, and retirement status

There is no durable failure record or independent escalation channel. Keep this
loop as a **repair candidate**. Its measurable delivery goal is clear; missing
delivery evidence is not itself a reason to retire it. Consider retirement only
if the session-bridge overlap audit proves the same useful coverage exists
elsewhere. Do not retire it from this document.
