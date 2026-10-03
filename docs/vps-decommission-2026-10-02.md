# Mac mini runtime and Hetzner retirement

Verified 2026-10-02. Rex deleted the Hetzner VPS and removed its Tailscale node.
Rex chose not to retain a Hetzner snapshot. The 7-day fallback period is over;
there is no rollback to the old server.

## Current entrypoints

| Purpose | Current target |
| --- | --- |
| Linux work, cron, user services, Claude/Codex, tmux | `ssh dev@bebop-vm`, `/home/dev/projects` |
| VM tailnet address | `100.99.202.95`, `bebop-vm.taila64e8f.ts.net` |
| Private cockpit | `https://bebop-vm.taila64e8f.ts.net` |
| Mac mini host | `ssh bebop_admin@mini`, `100.65.130.64` |
| Host-side VM management | `/opt/homebrew/bin/limactl`, instance `vps` |
| VM local hostname | `lima-vps` |

The Lima instance name `vps` and Linux hostname `lima-vps` identify the live VM.
They are not Hetzner dependencies. Keep those names, `/home/dev`, the `dev` user,
and the repository name `vps-tools`. The active `tag:server` SSH rule still serves
`bebop-vm`; do not remove it as part of retiring the old node.

The mini is now the compute host. Home power or internet loss interrupts access.
The old architecture documents' claims that work survives the mini being off no
longer apply. GitHub holds pushed code; it does not hold all runtime data.

## Cleanup completed

- Updated the active cockpit trusted hostname and public origin. Before the fix,
  the current URL returned HTTP 400; afterward `/` returned 302 and `/login` 200.
  Updated the saved login URL and retained preview configuration too.
- Removed `vps` and `vps-direct` SSH entries on the mini and MacBook. Both now
  reach `bebop-vm`; screenshot helpers use that target explicitly.
- Removed the old VPS connection and its cached remote tokens/facts from the
  MacBook's Claude desktop connection files. Kept the existing VM connection.
- Removed known-host entries for retired hostnames and addresses on the VM and
  both Macs. Retained current SSH keys because the migration reused the keypair.
- Removed the copied, inactive mini SSHFS mount units. The VM uses direct SSH,
  SCP or rsync to the mini. `/home/dev/mnt/mini` is not a live mount.
- Updated current memory notes and shell/config comments that identified this
  machine as the old Hetzner host.
- Removed about 1.7 GB of old x86 Claude remote helpers and Blender packages
  after verifying their active replacements are arm64.
- Moved cutover records from `~/migration-holding/` into
  `~/migration-archive/vps-to-mini-2026-09-22/`. Kept `mini-unique/`, the old
  crontab, logs and saved units. The archived shell helpers are non-executable.
  Those records are historical; never install that crontab over the current one.
- Corrected the finance backup cron entry to supply `USER=dev`. Its first
  post-cutover scheduled run had failed because the transfer helper requires
  `USER`, which cron does not supply. The schedule is unchanged. A real run with
  `USER` initially unset succeeded and verified the archive's SHA-256 on the mini.

Recoverable configuration copies on each edited device are under
`~/.local/state/vps-decommission/2026-10-02/`. These are private and can contain
credentials; do not commit or share them.

## Checks

- Tailscale lists `bebop-vm`, `mini`, the MacBook and personal devices; the old
  VPS is absent. Tailscale Serve proxies the cockpit to `127.0.0.1:8790`.
- `session-bridge.service`, `portfolio-cockpit.service`, and
  `attain-work-queue.timer` are active on the VM. No automation was rerun to send
  test mail or Telegram messages.
- Cai's laptop, the iPhone and iPad were offline, so their clients were not retested.
  Mosh 1.4.0 is installed on the VM for the iPad's existing mobile workflow.
- SSH from both Macs returns `lima-vps`. The screenshot function transferred a
  test PNG from each Mac, with exact-byte checks and without changing the
  clipboard. Both screenshot scripts pass zsh syntax checks. Interactive screen
  capture and the physical hotkey were not exercised remotely.
- Lima instance `vps` is running with 8 CPUs, 12 GiB memory and a 100 GiB disk.
  The mini auto-login account is `bebop_admin`; the VM autostart LaunchAgent exists.
  No reboot was performed during this cleanup; the earlier restart proof remains
  in the migration log.
- The mini reports sleep disabled, network wake enabled, automatic macOS OS
  installation disabled, and automatic download disabled. Security/configuration
  updates remain enabled. The previously listed root-owned desktop apps are gone.

- `earlyoom.service` is enabled and running with packaged defaults (`-r 3600`).
  The live user manager reports `OOMScoreAdjust=100`, `OOMPolicy=continue`.
  The old VPS's custom OOM-score and 5G/6G memory-limit claims are marked historical
  in the memory notes; they are not evidence of the current VM's configuration.

## Remaining decisions and owner action

Off-site backup target remains undecided. The finance archive now lands on the
same physical mini as the VM. It protects against some file errors, not loss of
that machine. No Hetzner snapshot is retained, by owner choice. Do not buy or
configure a new backup provider without the owner's selection.

DONE: Rex ran `sudo pmset -a autorestart 1` on the mini over SSH and supplied the
`pmset -g` output confirming `autorestart 1`. Restart after power failure is now
enabled. Sleep remains disabled and network wake remains enabled. A physical
power-loss recovery test was not performed.

When the offline iPad, iPhone or Cai's laptop is next used, confirm any saved
server connection uses `bebop-vm` or `100.99.202.95`, user `dev`. The iPad's August
Termius proof was for the deleted VPS. Its saved connection cannot be edited or
tested from this offline check.

Spotlight indexing remains enabled. It is not a migration blocker. No password
prompt or user-interface privilege escalation was triggered during this work.

## Recovery after retirement

Use the mini host and Lima to recover the VM. If the VM is running but tailnet
access fails, inspect its current local SSH port with `limactl list`; it changes
on VM restarts. Do not connect to the retired public IP `188.245.85.237`, old
Tailscale address `100.64.43.80`, or old hostname `vps`.

`setup/vps-to-mini/` contains historical provisioning/cutover scripts. Neither
`sync-home.sh` nor `activate-vm.sh` is an operational recovery procedure now.
Recovery needs current code, current secrets/data backups and the live schedule,
not the saved September cutover crontab.
