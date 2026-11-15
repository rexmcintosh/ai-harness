# alert-watch: Telegram reports become fixes

Every message sent through `bin/tg-send` is also written to a local inbox. A
dispatcher picks up new messages every 2 minutes and starts one Claude run. The run
decides which messages are real failures and handles them inside Rex's approved
authority: live steps (writer restarts, one rerun of a job, stale locks) and code
fixes that it tests, reviews with the council, merges and pushes. Rex gets one
Telegram note per run.

Design: [`docs/superpowers/specs/2026-11-15-alert-watch-design.md`](../docs/superpowers/specs/2026-11-15-alert-watch-design.md).
The run's rules: [`POLICY.md`](POLICY.md). Settings: [`config.toml`](config.toml).
What reaches a run: [`rules.toml`](rules.toml). Contract:
[`docs/contracts/alert-watch.md`](../docs/contracts/alert-watch.md).

## Where things are

All state is under `~/.local/state/alert-watch/` (mode 700; override `ALERT_WATCH_STATE`).

| Path | What |
|---|---|
| `inbox/` | captured messages waiting for the dispatcher (one JSON file each) |
| `batch/<run-id>/` | the messages a run is working on |
| `done/<run-id>/`, `failed/<run-id>/` | handled messages (pruned after 30 days) |
| `runs/<run-id>.json` | one record per run: messages, status, Claude session id, result |
| `state.json` | usage-limit backoff, cap notes, enqueued checks |
| `reruns.json` | when each job was last rerun |
| `PAUSED` | present = no new runs |
| `dispatch.log` | the dispatcher's own log (from cron) |

Open a run: `cd ~/projects && claude --resume <session id>`. Every note ends with
that line.

## Commands

```
python3 -m alertwatch.dispatch --dry-run   # list the inbox and how each message is sorted
python3 -m alertwatch.dispatch             # one dispatcher pass (cron does this)
python3 -m alertwatch.rerun <job>          # rerun a listed job once (the run uses this)
```

Pause: `touch ~/.local/state/alert-watch/PAUSED`. Start again: delete the file.
The dispatcher pauses itself after 3 failed runs in a row and says so in Telegram.
Capture off for one command: `ALERT_INBOX_DIR=off`.

## Rollout

1. **Capture.** Merging this branch turns capture on at once: every job calls
   `~/projects/ai-harness/bin/tg-send` from the main checkout. Let the inbox fill for
   a day, then check the sorting with `--dry-run`.
2. **Diagnose for 48 hours.** Install the cron line below with `mode = "diagnose"`
   (the committed default). Runs read and explain only; every action is a
   "Needs you" line.
3. **Act.** After Rex approves, add the section below to `~/.claude/CLAUDE.md`,
   set `mode = "act"` in `config.toml`, and merge that change.

### Cron line (stage 2)

```
*/2 * * * *  cd /home/dev/projects/ai-harness && /usr/bin/flock -n /home/dev/.local/state/alert-watch/cron.lock /usr/bin/python3 -m alertwatch.dispatch >> /home/dev/.local/state/alert-watch/dispatch.log 2>&1
```

Install it by editing a file, never through a pipeline (a `cat` alias wiped the
whole crontab on 2026-07-07):

```
crontab -l > ~/.local/state/alert-watch/crontab.before
cp ~/.local/state/alert-watch/crontab.before ~/.local/state/alert-watch/crontab.new
# add the line above to crontab.new with an editor
crontab ~/.local/state/alert-watch/crontab.new
crontab -l | diff ~/.local/state/alert-watch/crontab.before - # shows only the new line
```

Rollback: `crontab ~/.local/state/alert-watch/crontab.before`.

### `~/.claude/CLAUDE.md` section (stage 3, needs Rex's approval of the words)

```
### alert-watch runs (2026-11-15)
Rex approved that alert-watch runs (started by `alertwatch/dispatch.py`, with
`ALERT_WATCH_RUN=1` set) may take the live steps and merges listed in
`~/projects/ai-harness/alertwatch/POLICY.md` without asking. That policy is Rex's
standing rule for those runs only. Every other session keeps the merge protocol.
```

## Guard rails

- Deny rules passed with `--settings` (proved on 2026-11-15 to hold under
  `--dangerously-skip-permissions`): no crontab, systemctl, sudo, curl, wget, ssh,
  deploy CLIs, `tg-send`, force or delete pushes, `.env` reads, or
  `supervise_meets.py`. In `diagnose` mode also no Edit, Write, commit, push, merge,
  revert, kill or rerun.
- `MEETTRACK_DB_READ_ONLY=1` for the run: splash_poller's `db.py` refuses writes.
  It stops a mistake, not a determined agent; the database rule is still a policy rule.
- Caps: 30 runs a day, 6 an hour, 3 merges a day. One run at a time, 60 minutes each.
- The watcher's own notes and reruns are tagged (`ALERT_SOURCE=alert-watch...`) and
  dropped, so it never reacts to itself.
- The watchdog's `alertwatch` check warns when the inbox stops moving or a run fails.
