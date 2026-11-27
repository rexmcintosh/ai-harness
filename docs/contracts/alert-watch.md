# alert-watch

**Contract:** v1.0 · **Date:** 2026-11-15 · **Entrypoint:** `python3 -m alertwatch.dispatch` (cron every 2 min, not installed yet) · **Capture:** `bin/tg-send`

## Purpose and authority

Every `tg-send` message is copied to `~/.local/state/alert-watch/inbox/` before it is sent. The dispatcher sorts new messages with `alertwatch/rules.toml` and starts one `claude -p` run for the ones worth a look. The run's authority is `alertwatch/POLICY.md`, approved by Rex on 2026-11-14 and 2026-11-15:

- alone: restart a stuck or crashed meet writer (the restart helper sends SIGTERM; the supervisor relaunches), rerun a listed job once, clear a stale lock, fix code in `splash_poller` or `ai-harness` and merge and push it after tests and the council review, revert an alert-watch merge whose check failed;
- never alone: database writes, crontab, units, config or `.env` changes, messages to anyone, merges outside the procedure or on denied paths.

Merges, reverts and restarts go only through checked helpers (`alertwatch/merge.py`, `alertwatch/restart.py`), which apply the rules at the moment of the action and append to `~/.local/state/alert-watch/actions.jsonl`; the run's deny rules block doing them directly. In `mode = "diagnose"` (the committed default) the run only reads and explains, with an allowlist of read-only tools. `mode = "act"` needs Rex's go and the dated section in `~/.claude/CLAUDE.md` (`alertwatch/README.md`).

It owns `~/.local/state/alert-watch/`. **Secrets, names only:** none in its own code; its notes go out through the MeetTrack bot token file `~/.config/meettrack/telegram.env` (config `tg_env`), read by `tg-send`. The run inherits the cron environment; `venice-delegate` and `splash_poller/config.py` load their own keys.

## Success and evidence

Success for a message: its run record in `runs/<run-id>.json` has `status: ok` and a parsed result whose entry verdicts cover it, and Rex got the note (`note_status: accepted`) when the run found something real. For a merge: its `actions.jsonl` line names SHAs that are on `origin/main`, and any `pending_checks` item later produced a check run. Inspect `runs/`, `done/`, `failed/` and `dispatch.log`.

## Failure, escalation, and gaps

A failed run (crash, time limit, unreadable result) sends a failure note from the dispatcher itself. Three failed runs in a row write `PAUSED` and send one note. A usage limit backs off until the reset and does not count as a failure. An expired Claude login (added 2026-11-27, after the 21-27 Nov pause) is not a failure either: one note says so, this applies only when the run's JSON proves no model call answered (any other login error is an ordinary failed run), the reports wait in the inbox, a run is tried again every `login_retry_s` (15 minutes), and the watchdog check warns by name until a run works. The watchdog `alertwatch` check (on once `ENABLED` exists) warns when the oldest inbox message is over 20 minutes old, the newest run failed or hit an expired login. A run that may have acted is never replayed.

Gaps: the database rule is enforced by policy plus `MEETTRACK_DB_READ_ONLY` in `db.py`, which a Bash command could unset. Jobs that call the Telegram API directly (`diem/report.py`, Romance Empire `tiktok_replies.py`) are not captured. Rex cannot reply to a note from Telegram; he opens the run with `claude --resume`.

## Retirement test

After four weeks of `act` mode: if most runs end in "needs you", or a merge had to be reverted twice for the same cause, turn it off and review.
