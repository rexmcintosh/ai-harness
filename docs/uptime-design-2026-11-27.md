# Uptime design: keeping the home setup up and healing itself

Status: proposal, 2026-11-27, awaiting Rex's approval. Nothing here is built yet.
Reviewed by Codex (Sol 6.2, engineering) and Fable 5.2 (whole system and owner side);
both reviews were read-only and checked live facts. Background: the
[after-action review](after-action-2026-11-26-vm-restart.md) and the
[restart runbook](vm-restart-runbook.md).

## Principles

1. **A watcher must not share the fate of what it watches.** The VM cannot report its own
   outage; the mini cannot report a dead house.
2. **"Up" is not "working".** Check results (a verified backup, cron ran, services answer),
   not only that a process exists.
3. **Fix only what is certain; alert on the rest.** Start a VM that is confirmed
   `Stopped`. Never stop a running VM automatically until a clean shutdown is proven,
   because today every stop acts like a power cut.
4. **One decision-maker per action**, one lock, limits that survive reboots, and pauses
   that expire.
5. **Few messages, each one useful**: what broke, what was done, and whether Rex must act
   (with one command if so). Silence is checked by a weekly "all well" line.

## Live findings from the reviews (2026-11-27)

| Finding | Severity | Action |
| --- | --- | --- |
| Tailscale keys for the Mac mini and the MacBook expire **2026-11-30 ~10:55 UTC** (the VM's tagged key does not). After that the MacBook cannot reach the VM, and the mini drops off the tailnet. | high | Rex turns off key expiry for both machines in the Tailscale admin console before Nov 30. |
| alert-watch has been **paused since 2026-11-21** after 3 runs failed in about a second each ("no readable result"). The watchdog treats a paused watcher as fine, so nobody was told for 6 days. | high | Diagnose and fix the failing runs; make "paused" an alert. |
| alert-watch is in `diagnose` mode, and its POLICY.md does not allow service restarts or `systemctl`. | high | AI fixing is not available today; plan around a plain script (step 7). |
| A stale backup alerts nobody (the backup has no notification). | high | Add a stale-backup alert (step 3). |
| The VM's "power-cut" stops are probably caused by **launchd**: the VM runs inside the login LaunchAgent (`limactl start vps --foreground`), and launchd ended the job about 5 seconds after the stop began, while the guest was still shutting down. `limactl stop` then waits 3 minutes for an exit event that never comes. | high | Prove a clean shutdown in a drill with Rex present (step 5). Candidates: a longer, finite `ExitTimeOut` on the job, or a guest-initiated `systemctl poweroff`. |
| The Eaton UPS is visible over USB, but all macOS UPS shutdown thresholds are off and the UPS sleep setting is 1 minute. Behaviour on battery is untested. | medium | UPS drill before any UPS automation (step 8). |
| `limactl` is not on the PATH for plain SSH or launchd. | medium | Absolute paths everywhere. |

## Version 1 (proposed build order, smallest first)

1. **Today.** Rex turns off Tailscale key expiry for the mini and the MacBook. Fix
   alert-watch's failing runs, and alert when it is paused.
2. **Outside check-in.** Built 2026-11-27 as the Cloudflare Worker `bebop-uptime`
   (`setup/uptime-worker/`) on Rex's existing Cloudflare account, in place of the
   healthchecks.io account first planned. Two checks: the VM pings every 5 minutes
   (a plain "alive" ping, so one failing check never reads as "server down"); the mini
   guard pings after each completed tick. Silence for 15 minutes past the ping interval
   sends a Telegram DOWN, and UP when it returns. The VM watchdog checks that the
   Worker's own 5-minute cron keeps running. This is the only watcher that
   does not share the house's fate, and it also tells which machine went quiet. No
   inbound port opens.
3. **Stale-backup alert** on the VM: no verified backup in 30 hours sends a message.
4. **Mac mini guard** (launchd job every 2 minutes, script in the repo, state on the mini):
   - **Observe-only first**, then **start-only**: it starts the VM only after repeated,
     explicit `Stopped` results. `Broken` or unknown status: alert, no action.
   - Probes in order: Lima status, then the local SSH path (`limactl shell vps -- true`
     or the forwarded port), then services from inside the guest (T3 on 3773, Home
     Assistant, finance, nutrition, disk, memory, backup activity). Tailscale is
     checked separately: "Tailscale down, VM fine" is its own message and never causes
     a restart.
   - A suspected hang (about 10 minutes of failed guest probes, after a 10-minute boot
     grace) sends an alert with one command for Rex. No automatic stop in version 1.
   - Shared lock with planned maintenance; pause file with a maximum of 2 hours; the
     guard says "pause ended, VM still off, starting it".
   - At most 2 automatic starts per rolling 24 hours, counted before each attempt and
     kept across reboots; then alert and stop acting.
   - Bounded probes (timeouts), absolute paths, explicit `HOME`, `LIMA_HOME`, `PATH`.
     Does not compete with the login LaunchAgent: same lock, boot grace, and it asks
     launchd to start the existing job rather than starting a second copy.
   - Its own heartbeat (step 2) and a completed-tick file the VM can read.
   - Alerts through a **dedicated alert-only Telegram bot** whose token sits in a 0600
     file on the mini, never in process arguments, plists or logs. Local incident log;
     bounded retries when Telegram is down. (The current `tg-send` puts its token in
     curl arguments and assumes Linux, so the mini gets its own small sender.)
5. **Clean-shutdown drill** with Rex present and a verified backup first. Prove the guest
   reaches a completed poweroff (serial log evidence). Only after this passes may the
   guard restart a hung VM, and the runbook drops the "power cut" warning.
6. **Weekly "all well" line**, Monday morning: uptime, last verified backup, guard
   heartbeat, disk, automatic actions this week. If it does not arrive, something is
   broken.
7. **Service probe-and-restart script** on the VM (no AI): probe each service, one
   `systemctl --user restart` (or Docker restart for Home Assistant, which has no health
   check today), then a message. systemd's own `Restart=` stays the first line.
8. **UPS**, after a drill (pull the UPS plug for 5 minutes, watch what the mini does):
   "on battery" and "power back" messages; a low-battery clean stop based on measured
   runtime, with a persistent "off on purpose" hold so the guard does not start the VM
   again until AC power is stable. Put the router and modem on backup power.

## Later or dropped

- **T3 escalation**: later, capped at one open thread per problem, carrying the
  diagnosis and the exact words for Rex. T3 lives on the VM, so it cannot cover a VM
  outage.
- **AI fixes** through alert-watch: only after alert-watch works again and Rex approves a
  new policy that covers service restarts.
- **MacBook check**: dropped. It is often asleep, and the outside check-in covers it.
- **Internet-line (Starlink) change messages**: dropped; test Starlink by checking real
  access instead.
- **Outside-alert mailbox** (Cloudflare Worker for webhooks): deferred; alerts from
  those services already reach email.

## Drills (before we rely on it, and every quarter or after a macOS or Lima update)

1. Guard paths in dry-run mode under launchd (not from a shell): stale heartbeat, bad
   PATH, full disk, Telegram refusing, failed probes. One alert and one recovery notice.
2. With Rex watching: stop the VM by the runbook and do not start it. The guard must
   start it and message within 5 minutes.
3. Stop the guard. The outside check-in and the VM must both report it.
4. Expired pause, concurrent maintenance, a mini reboot, and the start limit.
5. Stop Tailscale on the mini: a Tailscale message, no restart.
6. Kill one service: it comes back, with one message.
7. UPS: pull the plug for 5 minutes; record what happens; later, the full low-battery
   path.
8. After each drill: disk integrity (`git fsck`), service health, the next verified
   backup.
