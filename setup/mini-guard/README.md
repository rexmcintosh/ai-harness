# mini-guard

The Mac mini watches the Linux VM it hosts (`bebop-vm`, Lima instance `vps`), from
outside, every 2 minutes. If the VM is stopped, it starts it. Everything else it only
reports. Design: [docs/uptime-design-2026-11-27.md](../../docs/uptime-design-2026-11-27.md).

## What it checks

| Check | Becomes a message after | Action |
| --- | --- | --- |
| VM `Stopped` | at once | mode `start`: starts it on the second stopped check (via the existing `io.lima-vm.autostart.vps` launchd job), at most 2 starts in 24 hours, 10 minutes apart; then tells Rex the one command to run |
| Lima reports another status (`Broken`, unknown) | 4 minutes | none |
| VM running but `limactl shell` gets no answer (hung) | 10 minutes | none: a stop acts like a power cut today |
| T3, Home Assistant, finance or nutrition tracker gives no HTTP answer inside the VM | 6 minutes | none (systemd and Docker restart their own services) |
| VM fine inside, but the mini cannot reach it over Tailscale | 10 minutes | none |
| VM disk 90% or more; Mac disk under 10 GB free | at once | none |
| Mac running on its UPS battery | at once | none (low-battery clean stop comes later, after a drill) |

Slow-start problems are ignored for 10 minutes after the mini boots or the guard starts
the VM. Messages go out only when something changes, with one reminder every 6 hours and
a "FIXED" note when it clears. A problem that clears before its delay counts as a blip.

## Planned work

On the mini: `~/.local/bin/mini-guard pause 30 "memory change"` (at most 2 hours),
`~/.local/bin/mini-guard resume`. A pause hides outage messages and blocks starts. It
ends by itself; if the VM is still off then, the guard says so. Restart scripts should
also hold `~/.local/state/mini-guard/lock` while they work. See the
[restart runbook](../../docs/vm-restart-runbook.md).

## Files on the mini

| Path | What |
| --- | --- |
| `~/.local/bin/mini-guard` | the script (this folder's `mini_guard.py`) |
| `~/Library/LaunchAgents/io.bebop.mini-guard.plist` | the launchd job (every 120 s) |
| `~/.config/mini-guard/config.json` | `mode` (`observe` or `start`), `chat_id`, `telegram_token_file`, optional `healthcheck_url` (mode 600) |
| `~/.config/mini-guard/telegram-token` | the guard's own alert-only bot token (mode 600) |
| `~/.local/state/mini-guard/` | `state.json`, `last-tick.json` (heartbeat), `pause.json`, `outbox.json` (messages waiting for Telegram), `guard.log` |

`mini-guard status` prints the mode, the last check, any pause and waiting messages.
`mini-guard test-alert` sends a test message.

## The VM watches the guard

The installer makes `~/.ssh/mini_guard_read` on the VM and adds it to the mini's
`authorized_keys` limited to one command: print `last-tick.json`. The watchdog
(`check_mini_guard`) reads it every 30 minutes: unreachable mini, unreadable heartbeat,
or a guard that has not run for 10 minutes each get their own message.

## Install, update, remove (from the VM)

    setup/mini-guard/install.sh          # install or update; keeps config and state
    setup/mini-guard/install.sh --undo   # remove the job, the script and the SSH key line

A new install starts in `observe` mode with no token. To switch on starting:
set `"mode": "start"` in the config after the drills pass.

## Tests

`python -m pytest setup/mini-guard` runs every decision path with fake command output:
healthy, stopped then started then fixed, watch-only, start limit and its reset, failed
start, cooldown, booting mini, pause, expired and too-long pause, maintenance lock, hung
guest, service down with reminder and fix, blip, Tailscale-only problem, battery and
disk, broken Lima status, Telegram down (outbox), and the token kept off command lines.
