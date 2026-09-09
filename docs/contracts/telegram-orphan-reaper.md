# Telegram MCP orphan reaper

**Contract:** v1.0 · **Date:** 2026-09-09 · **Observed units:** `~/.config/systemd/user/telegram-orphan-reaper.timer` → `telegram-orphan-reaper.service` → `~/.local/bin/telegram-orphan-reaper.sh` · **State:** dormant

## Observed state

The unit files exist, but no `timers.target.wants` link exists under `~/.config/systemd/user/`, so the timer is not enabled. The persistent timer stamp `~/.local/share/systemd/timers/stamp-telegram-orphan-reaper.timer` was last touched 2026-08-26 10:16 UTC, so the timer last elapsed then. Its log `~/.local/state/telegram-orphan-reaper.log` records 132 reap events between 2026-07-13 and 2026-08-23. On 2026-09-09 the only `bun` process on the host was `session-bridge`, so no orphan existed at inspection time. The inspecting session could not run `systemctl --user`; runtime state is inferred from disk.

## Purpose and authority (when enabled)

**Default mode:** local process cleanup. Every 10 minutes, and 5 minutes after boot, kill any `bun` process whose working directory is under `claude-plugins-official/telegram` and which has no live `claude` ancestor. Such processes are Telegram MCP servers leaked by ended Claude Code sessions; they hot-loop on `getUpdates` and each holds roughly 130 MB. Enough of them caused an out-of-memory kill of the tmux server on 2026-07-12.

It may read `/proc`, send `SIGKILL` to matching orphans, and append to its own log. It must not touch a server that still has a `claude` ancestor, any process outside the plugin directory, or anything over the network.

**Secrets:** none.

## Success and evidence

Success is measurable: after each tick, zero Telegram MCP `bun` processes exist without a live `claude` ancestor. Evidence is one log line per reap and the absence of such processes in `pgrep -x bun` plus `/proc/<pid>/cwd`. The timer stamp proves the last elapse.

## Failure, escalation, and disposition

There is no notification. The failure mode is silent leak accumulation until memory pressure; the watchdog checks disk, not memory, so nothing else catches it. This loop can state a measurable success condition, so Correction A does not make it a retirement candidate on that rule. It is dormant, which needs an owner decision:

- **Re-enable** with `systemctl --user enable --now telegram-orphan-reaper.timer` if orphaned Telegram MCP servers still appear. The last reap was 2026-08-23, three days before the timer stopped.
- **Remove** the two unit files and the script if no orphan appears by 2026-09-25, thirty days after the timer stopped. Check with `pgrep -x bun` and `readlink /proc/<pid>/cwd`. This follows the owner's stated preference to remove unused surface area rather than keep it.

Neither action is taken by this document; both are scheduler edits that need separate approval.
