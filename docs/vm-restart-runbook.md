# Restarting bebop-vm: runbook

Use this every time the Linux VM (`bebop-vm` / `lima-vps`, Lima instance `vps` on the
Mac mini) is stopped or restarted: memory or CPU changes, Lima or macOS updates, kernel
updates, or a plain restart. It comes from the
[after-action review of 2026-11-26/27](after-action-2026-11-26-vm-restart.md), where a
3-minute stop problem turned into a 12-hour outage.

The one rule: **the VM must end Running, and Rex must know whether it did.** Everything
that can notice an outage (the agent, the alerts, the chat) lives on the VM, so while it
is off, nothing on it can help.

## Facts to plan around

- `limactl stop vps` gives up after about 3 minutes with `did not receive an event with
  the exiting status`, even though `limactl list` then shows `Stopped`. Both stops on
  2026-11-26/27 did this. The guest journal shows the shutdown never finishes, so
  **treat every stop as a power cut** until the cause is found: flush writes first, and
  expect half-written files from anything still writing.
- The mini's LaunchAgent starts the VM only at login (`RunAtLoad`). If a script leaves
  the VM stopped and the mini stays up, the VM stays stopped.
- Writers to avoid: the session-gc snapshot cron (every 10 minutes, at :00, :10 ...),
  the restic backup (`restic-offsite.service`, daily 03:30, about 2 hours, catches up
  after a missed run), cron jobs (`crontab -l`), running T3 threads and builds.
- macOS updates and other Mac admin steps need Rex's Mac password. It comes through
  T3's one-use secret box, never through chat.
- T3 resumes the agent's thread by itself when the VM is back. Rex does not need to send
  a message to wake it.

## Before

1. **Pick a time when Rex can watch.** Ask: "Can you watch for 15 minutes, starting at
   <time>?" If not, wait. No first-time or unattended night runs.
2. **Tell Rex what will stop** (T3, Home Assistant, the trackers, cron, alerts) and how
   long. Give him one tested recovery step he can do himself: on the mini, open Terminal
   and run `/opt/homebrew/bin/limactl start vps`. Not a log path.
3. **Ask for anything that needs Rex up front**, under "What I need you to do", while he
   is still at the screen: the Mac password, approvals. Never leave it in a later bullet.
4. **Check what is running and would be cut off**, and say so to Rex before he decides:
   - `systemctl --user status restic-offsite.service` (`activating` means a backup is
     running; wait for it, then check `~/.local/state/restic-offsite/last-success.json`
     shows today's run with `verified: true`),
   - active T3 threads and builds, tmux sessions, `crontab -l` for jobs due during the
     window.
5. **Check what comes back by itself**: enabled user and system services, Docker restart
   policies, the T3 service, Lima autostart, Mac auto-login (FileVault off).
6. **Do slow, safe work while the VM is still up**: Ubuntu updates inside tmux (never
   over a bare SSH session), Homebrew upgrades except Lima, Claude Code updates.
7. **Have a second model review the restart script itself**, not only the risky change
   it carries. Keep the script in the repo, not only on the mini.
8. **One risky change per stop.** Bundle only steps that cannot leave the VM off.
9. **Send "starting in 2 minutes, reply stop to cancel", then wait** the 2 minutes.
10. **Tell Rex to expect outside-alarm messages** if the VM or the mini will be off for
    more than about 15 minutes: a `DOWN` from the Cloudflare `bebop-uptime` alarm, then
    `UP` when it is back. Pause the mini guard too: `~/.local/bin/mini-guard pause 30
    "restart"` on the mini.

## During

1. Start the stop just after a session-gc snapshot finishes (for example at :02), never
   at :00.
2. Flush the guest: `limactl shell vps -- sudo sync` must succeed.
3. Stop, then judge by **status, not the exit code**:
   `limactl list --format '{{.Status}}' vps`. Use one deadline for the stop command and
   the polling together. If it is still not `Stopped` at the deadline, save Lima's
   host-agent and serial logs (`~/.lima/vps/ha.stderr.log`, `serialv.log`) before a
   forced stop.
4. Take the disk clone only when the status is `Stopped`:
   `cp -c ~/.lima/vps/disk ~/lima-snapshots/vps-disk-<date>`. If the clone fails, skip
   every later change and start the VM again.
5. Make the change (for example `limactl edit vps --memory 14`), then start the VM.
   Every exit path of the script, including errors, must run `limactl start vps` unless
   the Mac itself is restarting. A trap does not cover a killed script or a Mac crash,
   so the deadline in "Before" step 2 still matters.
6. Run the macOS update on its own, with Rex present.

## After

1. **State the downtime from the clocks first** (`uptime`, the mini's log), before
   saying anything is fine.
2. **Check what was interrupted**, not only what is up:
   - the last backup result (not "timer active"); if a run was cut off, start one with
     `systemctl --user start --no-block restic-offsite.service` and follow it to
     `verified: true`,
   - `systemctl --failed` and `systemctl --user --failed`,
   - `git fsck --full` in repos, and empty files under `.git/objects` and `.git/refs`
     (`find ~/projects -path '*/.git/*' -type f -empty`). Move bad files to a
     quarantine folder; never delete them.
3. Check the services answer: T3 (from the MacBook), Home Assistant, the finance and
   nutrition trackers, and the next session-gc snapshot and cron run.
4. Check the change itself: `free -m`, `uname -r`, zswap (`/sys/module/zswap/parameters`).
5. **Ask Rex what he saw** and whether he had to do anything.
6. Update [SETUP.md](SETUP.md), and file open items in `~/projects/backlog/`
   (propose, then confirm). Do not leave them under "Also found".
