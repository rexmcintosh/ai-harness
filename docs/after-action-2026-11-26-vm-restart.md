# After-action review: bebop-vm restarts, 2026-11-26 and 2026-11-27

Reviewed with Astra 6.2 (restart procedure and root causes) and Fable 5.2 (the human
side and the agent's reporting). The resulting procedure is
[vm-restart-runbook.md](vm-restart-runbook.md).

## Goal

Raise the VM's memory from 12 to 14 GiB, install pending updates (69 Ubuntu packages and
kernel 6.8.0-151, Claude Code, Homebrew including Lima 2.2.0 to 2.3.0, macOS Tahoe
26.6.2 to 26.7.3), take an APFS disk clone as a quick undo, turn on zswap, then clean up.

## What happened

| Time (Lisbon) | Event |
| --- | --- |
| Nov 26, 20:17 | Plan agreed with Rex, including the macOS update. |
| about 21:30 to 21:55 | Ubuntu updates in tmux, Claude Code update, Homebrew formula upgrades, zswap unit installed. The agent commits work in ai-harness. |
| 21:59:49 | Last entry the VM's system journal kept from that boot. |
| 22:00:01 | session-gc snapshot cron starts writing WIP commits in 6 repos. |
| seconds before 22:00:10 | The agent posts "restarting now", then starts the script at once. |
| 22:00:10 | The mini script runs `limactl stop vps`. The agent's session ends with the VM. |
| 22:03:20 | `limactl stop` exits fatal after about 3 minutes; the VM shows `Stopped`. The script treats this as a failure and exits without starting the VM. |
| Nov 27, 09:55 | Rex opens Terminal on the mini. |
| 09:57 | The VM is running again. Downtime about 11 h 54 min. |
| 10:05 | The missed 03:30 backup starts catching up (takes about 2 hours). |
| 10:01 to 10:45 | The agent finds and repairs damaged git objects; Rex asks for the second restart. Two password prompts time out (Rex away). Rex chooses to skip macOS. |
| 11:06:14 | Attempt 2: guest `sync`, then stop. The running catch-up backup is killed. |
| 11:09:24 | The stop again gives up after about 3 minutes; status is `Stopped`, so the script continues: disk clone, 14 GiB, Lima 2.3.0, start. |
| 11:09:54 | VM up. Downtime about 4 minutes. 14 GiB, zswap on, all services up, no git damage. |
| 11:41:50 | A new backup run starts, after the review found the killed one. |

## Impact

- About 12 hours without Home Assistant, the finance and nutrition trackers, all cron
  jobs, alerts, the 03:30 backup and the 04:00 idle-chat job.
- An unclean shutdown: 10 empty git object files in ai-harness (the agent's last commit
  and a session-gc snapshot) and empty session-gc WIP refs in 6 repos. Real branches
  were never touched. Repaired by moving the bad files to
  `~/.local/state/restart-2026-11-26/git-quarantine/`, rebuilding the agent's branch from
  its last good commit and expiring its reflog. Every repo passes `git fsck --full`. No
  databases were damaged.
- No verified backup between 2026-11-26 05:25 and the 11:41 run on 2026-11-27.

## Root causes

1. **The script's failure path left the VM off** (high). It trusted the exit code of
   `limactl stop` over the VM's status, and its error exit never started the VM. The
   agent assumed "if the stop fails, the VM keeps running". It was the first VM stop in
   65 days, and the stop path was never tested.
2. **Everything that can notice an outage runs on the VM** (high). The agent, the alerts
   and the chat all went down with it. The mini's LaunchAgent only runs at login, and
   nothing on the mini watched the VM or told Rex.
3. **Nobody was watching, and Rex could not act** (high). The run went ahead at 22:00
   without asking whether Rex would watch. The "restarting now" notice came 3 seconds
   before the trigger, and his fallback was a log path rather than one command to run.
4. **The stop behaves like a power cut** (high). Both stops gave up after about 3
   minutes, and the guest's shutdown never finished (the journal ends before or during
   shutdown). Writes in flight were cut: session-gc at 22:00, then the backup at 11:06.
   The `sync` in attempt 2 prevented git damage, but it is one run of evidence.
5. **Nobody checked what was running before the second stop** (high). The agent itself
   had told Rex the backup would catch up at 10:05, then offered "run now" without
   saying it would kill that 2-hour job.
6. **Review went to the low-risk part** (medium). Astra reviewed the zswap plan, but no
   one reviewed the script that turns the server off.
7. **Too much in one unattended run** (medium). Five changes rode on one night-time stop.
8. **Reports ran ahead of evidence** (medium). "Back on the new kernel" came before the
   12-hour gap was found, and "no damage this time" came 30 minutes before the killed
   backup was found, and only because Rex asked for this review. The checks asked "is
   it up?" and not "what did I interrupt?".
9. **The password ask was buried** (medium). It sat in a later bullet, not under "What I
   need you to do", so Rex approved and left; two prompts timed out.

## What went well (keep)

- The boot audit of what restarts by itself: everything did come back.
- Ubuntu updates inside tmux, and slow safe work done while the VM was still up.
- A second-model review of the zswap plan caught a boot-ordering bug before it shipped.
- The Mac password was tested before use, kept in a mode-600 file, deleted after use,
  and never asked for in chat. After two timeouts the agent stopped and asked, rather
  than guessing.
- The agent said plainly that the outage was its mistake, and moved damaged files to
  quarantine instead of deleting them.
- Attempt 2 started just after a session-gc snapshot, flushed the guest first, and
  judged the stop by status. It cost 4 minutes and no damage.

## Open items

- Install macOS Tahoe 26.7.3, alone, with Rex present (needs the password).
- Find why the guest shutdown stalls for about 3 minutes (save Lima's host-agent and
  serial logs on the next stop; check root's journal of the stopped boots).
- Move the restart script into the repo, fix the gaps Astra found (one deadline for
  stop and polling, stop on a failed clone, fresh clone name), and add a "back" or
  "NOT back" Telegram message from the mini. The bot token is not on the mini today.
- Delete the disk clone `~/lima-snapshots/vps-disk-2026-11-27` on the mini around
  2026-12-11, after a verified backup.
