# Current setup (living reference)

Last verified: 2026-11-27. This is the one place that says how Rex's machines and
tools fit together **now**. Update it in the same change whenever the setup
changes (a new app, a new link between machines, a new always-on job, a resize).
Older documents ([ARCHITECTURE.md](ARCHITECTURE.md), the
[Oct 2 runtime record](vps-decommission-2026-10-02.md)) are history and context.

## Machines

| Machine | What it is | Role |
| --- | --- | --- |
| **bebop-vm** | Ubuntu 24.04 arm64 VM (kernel 6.8.0-151) (Lima, `vz`) inside the Mac mini. Linux hostname `lima-vps`, Lima instance `vps`, user `dev` (passwordless sudo). Tailnet `100.99.202.95`, `bebop-vm.taila64e8f.ts.net`. 8 CPUs, 14 GiB RAM (since 2026-11-27), 4 GiB swapfile, 100 GiB disk. | Where all work runs: projects, T3 server, Claude and Codex sessions, cron, services. |
| **mini** | Mac mini M4, 16 GB, macOS Tahoe. Login `bebop_admin` (`ssh bebop_admin@mini`, `100.65.130.64`). Auto-login on, FileVault off, sleep off (Amphetamine), restarts after power loss. LaunchAgent `io.lima-vm.autostart.vps` starts the VM at login. | Host for the VM only. Rex uses it for nothing else, so the VM gets priority for memory. Not used for on-screen jobs. |
| **rexmb** | MacBook, M1, 16 GB. Claude, ChatGPT and T3 Code apps, Tailscale. | Rex's daily window into the VM. If on-screen (computer-use) jobs are set up, they run here, only while it is open. |
| iPhone, iPad | Tailscale clients. | Thin clients. |
| cai-laptop | Windows, someone else's laptop on the tailnet. | Not part of Rex's work setup. |

The VM is reachable **only over Tailscale** (ufw DROP, inbound on `tailscale0`
only; see [MESH-ONLY-LOCKDOWN.md](MESH-ONLY-LOCKDOWN.md)).

## How Rex works

- **T3 Code** is the main work app (since 2026-11-03). Its server runs on the VM:
  `t3 serve` v0.1.11 as the user service `t3code.service`, on `127.0.0.1:3773`,
  shared on the tailnet at `https://bebop-vm.taila64e8f.ts.net:8444`.
  The MacBook's T3 app connects to that server; the MacBook is a window.
  - **No environments are linked** (`t3 environment list`: none). So agents on
    the VM cannot start work on the MacBook. On-screen jobs on the MacBook need
    `t3 environment link` first.
  - T3's CLI can drive threads: `t3 thread list|start|send|show`.
  - Chief of Staff thread: not started yet.
- **Claude desktop app** (MacBook): each sidebar chat runs a CLI program on the VM
  (`~/.claude/remote/ccd-cli/<version>`, parent `~/.claude/remote/srv/.../server`).
  Programs idle for 24 h are stopped nightly by `idle-chats`
  ([setup/idle-chats](../setup/idle-chats/README.md)); typing in a chat resumes it.
- **Codex app** (MacBook): connects over Tailscale SSH and a relay
  (`codex app-server proxy`) to one shared Codex background server on the VM
  (`codex app-server --listen unix://`, started by the app; not managed by
  `codex app-server daemon`). Restart it with SIGTERM; the app starts a new one.
- **ChatGPT app**: Rex chats with "Steve" (a ChatGPT Dot), by typing only. Steve
  cannot touch the VM.
- **Terminal / tmux** sessions on the VM: a separate lane from the apps.

## Always-on services on the VM

| Service | How it runs | Tailnet address |
| --- | --- | --- |
| T3 server | user `t3code.service` | `:8444` |
| Finance tracker (Next.js) | user `finance-tracker.service`, `127.0.0.1:3000` | `https://bebop-vm.taila64e8f.ts.net` |
| Nutrition tracker | node `src/main.ts` in `~/projects/nutrition-tracker/server`, `127.0.0.1:3791` | `:8445` |
| Home Assistant | Docker container `homeassistant`, restart `unless-stopped`, `8123` | `:8443` (also plain `8123` on the tailnet) |
| earlyoom | system service; settings managed by the memory guard | |
| Backups | `restic-offsite.timer`, daily 03:30 | |
| Outside alarm | Cloudflare Worker `bebop-uptime` ([setup/uptime-worker](../setup/uptime-worker/README.md)), cron every 5 min; the VM (cron `bebop-uptime-ping`) and the Mac mini guard ping it; it messages Rex when a ping is late (house power or internet down). Watched by the VM watchdog. Added 2026-11-27. | `https://bebop-uptime.rex-651.workers.dev` (secret in an `Authorization` header; Cloudflare logs show it as REDACTED) |
| Cron jobs | `crontab -l` (about 25 lines). Debian cron ignores `CRON_TZ`; all times are server time, Europe/Lisbon. | |

## Service keys on the VM

| Key | Where | Use |
| --- | --- | --- |
| Cloudflare (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, user token, verified 2026-11-27) | `~/.env` | `wrangler deploy` of Workers (e.g. `bebop-uptime`). It cannot create D1 databases or read billing. Site deploys do not use it (Workers Builds). |
| Alarm bot `@Alertbebopbot` token (alert-only, made 2026-11-27; separate from the Bebop bot so a leak can only spam) | Mac mini `~/.config/mini-guard/telegram-token` (600); Cloudflare Worker secret `TELEGRAM_TOKEN` on `bebop-uptime` | outside alarm and Mac mini guard messages to Rex (chat 7735693897) |
| bebop-uptime ping secret | `~/.config/bebop-uptime/secret` (600); same value as the Worker secret `PING_SECRET` and in the mini's `~/.config/mini-guard/config.json` | VM and mini pings, watchdog reads `/status` |
| App Store Connect API (team key `M59TFWW762`, App Manager, added 2026-11-27) | `~/.appstoreconnect/private_keys/AuthKey_M59TFWW762.p8` (600); key and issuer ids in `~/.appstoreconnect/config.env` | MeetTrack store page: text, screenshots, build choice, review notes. Helpers in `~/.appstoreconnect/tools/` (`asc.py` signs each call; `fill_text.py` reads `splash_poller/ios/APP_STORE.md`; `upload_shots.py`). Submitting for review stays Rex's step. |

## Memory

- 14 GiB RAM (raised from 12 on 2026-11-27), 4 GiB swapfile. Out-of-memory stops happened often in November.
- **Memory guard** ([docs/memory-guard.md](memory-guard.md)): earlyoom prefer/avoid
  lists and per-scope caps for agent and SSH sessions.
- **Browser helper stand-in** `mcp-lazy` ([setup/mcp-lazy](../setup/mcp-lazy/README.md)):
  the Playwright MCP starts only when a session uses it (Claude and Codex).
- **idle-chats** nightly at 04:00 (see above).
- **zswap** on since 2026-11-27 (compressed swap cache, zstd, zsmalloc, 20% pool):
  `zswap-settings.service`, files in [setup/zswap](../setup/zswap/README.md).

## Backups

- `restic-offsite` ([offsite-backup](../offsite-backup/README.md)): daily 03:30 to
  Backblaze B2, 30 daily + 6 monthly, each run verified by a test restore.
  Last checked success: 2026-11-26 (finished 05:25).
- Covers `/home/dev` only. Not a whole-machine image: system files and anything
  outside the home folder (for example `/opt`, `/var/lib`) need rebuilding first.

## Updates

- **Before any VM stop or restart, follow [vm-restart-runbook.md](vm-restart-runbook.md).**

- VM: Ubuntu packages through apt. **Run mutating apt commands inside tmux** (or
  detached): a network-daemon restart once killed an apt run over SSH and left
  apt broken for 12 days (`~/.config/zsh/apt-tmux-guard.zsh`).
- Kernel meta package is `linux-virtual`, so `linux-modules-extra` (zram) is not
  installed. zswap is built in.
- Mac mini: Homebrew (Lima, tools). To stop the VM from a script, check
  `limactl list --format '{{.Status}}' vps` instead of the exit code of `limactl stop`:
  on 2026-11-26 it exited with an error after the VM had stopped, the script quit,
  and the VM stayed off for about 12 hours. The fixed script is
  `~/restart-2026-11-26.sh` on the mini. Its exit trap starts the VM again after an
  error, but not if the script is killed or the Mac crashes.
  `limactl stop` still gives up after about 3 minutes, because the guest shutdown
  stalls (the journal stops mid-shutdown). The script runs `sync` in the guest first,
  so nothing is lost; the cause of the stall is not known yet.
- Lima 2.3.0 since 2026-11-27. Disk copy (APFS clone) from before the 14 GiB change:
  `~/lima-snapshots/vps-disk-2026-11-27` on the mini. Delete it after about two
  weeks (it grows as the VM disk changes).
- macOS Tahoe 26.7.3 is still to install (needs the Mac password). Automatic macOS installs are off; install
  minor macOS updates on purpose. A Mac restart restarts the VM (auto-login, then
  the LaunchAgent starts it).
- Claude Code, Codex and T3 do not update themselves; T3 shows update checks.

## Decisions

- **Home power and internet are accepted limits** (Rex, 2026-11-27). No second server
  elsewhere unless income starts to depend on uninterrupted uptime.
- **Power:** the house has a whole-house backup battery. The Mac mini is also on an
  Eaton 5E UPS, connected by USB (`pmset -g batt` shows it; macOS shutdown-on-battery
  settings are off). The router has no UPS yet; Rex plans one.
- **Starlink is being installed** as a backup internet line; how it fits in is not
  decided yet.
- Watchers must not share the fate of what they watch: the Mac mini guards the VM, the
  VM watches the mini's guard, the MacBook checks both while open (design agreed
  2026-11-27, not built yet).

## Pending decisions (2026-11-26)

- OpenBot: on hold. Rex may skip the always-on bot and use a T3 Chief of Staff
  thread instead, with on-screen jobs on the MacBook.
- Restart window: after the current build posts its merge recommendation.
