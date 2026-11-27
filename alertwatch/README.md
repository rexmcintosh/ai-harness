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
| `actions.jsonl` | every merge, revert and restart a helper made (the merge cap counts these) |
| `worktrees/` | the runs' fix worktrees (never inside a main checkout) |
| `ENABLED` | present = the dispatcher is installed; turns on the watchdog check |
| `PAUSED` | present = no new runs |
| `dispatch.log` | the dispatcher's own log (from cron) |

Open a run: `cd ~/projects && claude --resume <session id>`. Every note ends with
that line.

## Commands

```
python3 -m alertwatch.dispatch --dry-run   # list the inbox and how each message is sorted
python3 -m alertwatch.dispatch             # one dispatcher pass (cron does this)
python3 -m alertwatch.rerun <job>          # rerun a listed job once (the run uses this)
python3 -m alertwatch.restart restart <key> # stop a meet writer; the supervisor relaunches it
python3 -m alertwatch.restart history       # restarts in the last 24 hours
python3 -m alertwatch.merge merge <repo> <worktree>   # checked fast-forward merge and push
python3 -m alertwatch.merge revert <repo> <sha>       # revert an alert-watch merge
python3 -m alertwatch.dbread <table> [filters]        # read-only database rows
python3 -m alertwatch.gitinfo <repo>                  # recent commits and changed file names
```

The run never pushes, merges, reverts or kills by itself (those shell commands are
denied). The helpers check every rule at the moment of the action and refuse with
the reason when one fails.

Pause: `touch ~/.local/state/alert-watch/PAUSED`. Start again: delete the file.
The dispatcher pauses itself after 3 failed runs in a row and says so in Telegram. An expired Claude login, in a run whose JSON proves no model call answered, is not a failed run: one note says to log in again (on the VM run `claude`, then /login), the reports wait in the inbox, and a run is tried again every 15 minutes. A login error without that proof (after model work, on a timeout, or with no JSON) is an ordinary failed run, and its failure note says to log in.
Deleting `PAUSED` resumes it: the failures that caused the pause do not count again.
Capture off for one command: `ALERT_INBOX_DIR=off`.

## Rollout

1. **Capture.** Merging this branch turns capture on at once: every job calls
   `~/projects/ai-harness/bin/tg-send` from the main checkout. Let the inbox fill for
   a day, then check the sorting with `--dry-run`.
2. **Diagnose for 48 hours.** Install the cron line below with `mode = "diagnose"`
   (the committed default), and `touch ~/.local/state/alert-watch/ENABLED` so the
   watchdog check starts. A diagnose run has read-only tools only (an allowlist, not
   `--dangerously-skip-permissions`); every action is a "Needs you" line.
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

Rollback: `crontab ~/.local/state/alert-watch/crontab.before`, then
`rm ~/.local/state/alert-watch/ENABLED`.

### `~/.claude/CLAUDE.md` section (stage 3, needs Rex's approval of the words)

```
### alert-watch runs (2026-11-15)
Rex approved that alert-watch runs (started by `alertwatch/dispatch.py`, with
`ALERT_WATCH_RUN=1` set) may take the live steps and merges listed in
`~/projects/ai-harness/alertwatch/POLICY.md` without asking. That policy is Rex's
standing rule for those runs only. Every other session keeps the merge protocol.
```

## Guard rails

- `diagnose` mode: an allowlist only: Read, Grep and Glob (they honor the
  credential and `.env` deny rules), `ls`, `stat`, and the read-only helpers
  `alertwatch.dbread`, `alertwatch.restart history` and `alertwatch.gitinfo` (commit
  lines and file names, never contents). No `cat`, `grep`, `git log/show/diff`, `wc`
  or `date`: each can print a file's contents past the deny rules. Everything else is
  refused. Proved with real runs on Claude Code 2.1.317 on 2026-11-15, planted
  secrets included.
- Only messages to `watch_chats` (Rex's chat) are captured or claimed; a capture
  whose chat cannot be proven is never claimed and is deleted after an hour.
- The run gets a whitelisted environment, and credential files are denied in both modes.
- `act` mode: `--dangerously-skip-permissions` with deny rules passed by `--settings`
  (proved on 2026-11-15 to hold): no push, merge, revert or kill as shell commands
  (only the helpers), no Edit or Write inside `~/projects` (fixes are made in
  `<state>/worktrees/`), no crontab, systemctl, sudo, curl, wget, ssh, deploy CLIs,
  `tg-send`, `.env` reads, or `supervise_meets.py`. A shell command can still change
  files; the deny rules stop mistakes and obvious misuse, not a determined agent.
- `MEETTRACK_DB_READ_ONLY=1` for the run: splash_poller's `db.py` refuses writes.
  It stops a mistake, not a determined agent; the database rule is still a policy rule.
- Caps: 30 runs a day, 6 an hour (the dispatcher), 3 merges a day (the merge helper,
  from `actions.jsonl`). One run at a time, 60 minutes each; at the end every process
  that carries the run's `ALERT_WATCH_RUN_ID` is killed.
- The watcher's own notes and reruns are tagged (`ALERT_SOURCE=alert-watch...`) and
  dropped, so it never reacts to itself.
- The watchdog's `alertwatch` check warns when the inbox stops moving or a run fails.
