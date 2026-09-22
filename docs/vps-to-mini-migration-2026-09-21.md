# VPS to Mac mini migration: plan and runbook

Started 2026-09-21. Owner decision: retire the Hetzner VPS and run all VPS work on the Mac mini
("Bebop"), inside a Linux VM. The mini stops being a desk computer and becomes a server.

Status legend: DONE, NEXT, OWNER (needs Rex), OPEN (undecided).

## 1. Goal and end state

- Hetzner server cancelled and removed from the tailnet.
- One Ubuntu 24.04 arm64 VM on the mini does everything the VPS does today: cron jobs, user
  services, tmux sessions, Claude Code / Codex sessions, preview servers, `tailscale serve`.
- The VM is its own tailnet node. Phones, the MacBook and Cai's laptop reach it the same way they
  reach the VPS today (Tailscale SSH as `dev`).
- The macOS side of the mini runs only: Tailscale, Lima (the VM runner), and whatever must be on
  macOS (nothing identified so far).

## 2. Why a VM and not native macOS

The VPS workload is tied to Linux: about 40 crontab lines (Debian cron semantics, `flock`), three
systemd user units, about 240 files that hardcode `/home/dev`, and about 75 that reference the VPS
address. A VM with user `dev` (uid/gid 1000) and home `/home/dev` lets all of that move unchanged.
Native macOS would mean rewriting every job for launchd and every path for `/Users/...`.

Cost of the VM route: the CPU architecture changes from x86_64 to arm64, so compiled things
(`node_modules`, Python venvs, pipx venvs, uv Pythons, Playwright browsers) are rebuilt on the VM,
not copied.

## 3. Inventory (2026-09-21)

### VPS
- Ubuntu 24.04.4, x86_64, 2 cores, 7.6 GB RAM, 75 GB disk at 89%.
- `/home/dev` 44 GB. Real data to move is about 22 GB once `node_modules`, venvs, caches and build
  output are excluded: projects 14 GB (romance-empire 7.1, ai-harness 1.9, _archive 1.8,
  santa-amaro 1.3), `.claude/projects` 3.3, `.codex` 2.0, `.local/share` 1.2, small rest.
- Crontab groups: Bebop briefings (07:00/18:00 Lisbon); MeetTrack discover (hourly; supervise and
  ingest paused); watchdog (30 min); security sweep (Mon); diem drain (4x daily, UTC); `agents once`
  (every minute); loom absorb (02:00 UTC); session-gc snapshot (10 min) + sweep (Mon); superpowers
  preamble reapply (Mon); ultimate-portugal rent check (Tue) + writing engine (morning, poller,
  seo, refresh); swimtrack-website engine (morning, poller); Attain Prep feedback sync (15 min),
  Bento sync (10 min), parent digest (Mon 13:00 UTC); backlog-run (03:00 UTC); romance-empire
  engage scan, ops-tick (10 min), ops-compose (06:00); finance-tracker encrypted backup (Sun).
- systemd user units: `session-bridge` (bun), `portfolio-cockpit` (gunicorn :8790, published by
  `tailscale serve` at https://vps.taila64e8f.ts.net), `attain-work-queue.timer` (2 min),
  `home-dev-mnt-mini.mount` (sshfs of the mini), `telegram-orphan-reaper` (retired). Linger is on.
- Long-running dev servers: finance-tracker Next on :3300, roam vite on :5173/:4179/:5180/:5181,
  ultimate-portugal `ts-tunnel` on :4321, `tailserve` http.server on :8321. Port 8791 serves a
  deleted worktree (stale; do not move).
- tmux session groups: finance-tracker, romance, splash-poller, swimtrack, move-to-bebop.
- Tooling: node 22.22.3 (nvm), bun 1.3.14, python 3.12 + uv python 3.11, pipx (`council` 0.5.0
  which also provides backlog-run, diem, session-gc, jev, notion-work-queue, portfolio-cockpit,
  venice-usage), gh 2.96, claude 2.1.278, codex 0.154.0. Notable apt: ffmpeg, pandoc, texlive,
  tesseract (eng+por), poppler, blender, xvfb, google-chrome-stable, caddy, earlyoom, fail2ban, ufw.
- Secrets: `~/.env` (about 50 keys), `~/.config/portfolio-cockpit.env`, per-repo `.env*` files,
  `~/.ssh`, `~/.gnupg` (finance backup recipient key), gh and Claude/Codex credentials.

### Mac mini
- M4, 10 cores, 16 GB RAM, macOS 26.6.2, 228 GB disk. SSH user `bebop_admin`.
- Power settings already server-like: sleep 0, autorestart after power loss 1, wake on network 1.
- FileVault off. Auto-login off. Other macOS accounts exist: `bebop_active`, `jane`.
- `~/Projects` on the mini (6 GB) holds repos that are NOT all on the VPS (for example
  a-million-worlds, liam_mobility, crypto-portfolio, lifeguard-camp-tracker, macmcintosh,
  combat-arms-transition, build-ai-automation-workflow). Treat as unique data until checked.
- Power: UPS to be installed; house has solar plus a 13 kWh battery. Remaining single point of
  failure is the home internet line.

## 4. Done so far (2026-09-21)

- DONE Disk: removed three iOS simulator runtimes and Xcode device-support files from the mini.
  Free space went from 21 GB to 72 GB. No Xcode projects exist on the mini.
- DONE `brew install lima` (2.2.0) on the mini.
- DONE VM created and running: `limactl create --name=vps --vm-type=vz --cpus=6 --memory=10
  --disk=100 --mount-none --containerd=none template://ubuntu-24.04`. The disk file is sparse.
- DONE Base provisioning inside the VM (`setup/vps-to-mini/provision-base.sh`, verified): UTC
  clock, user `dev` 1000:1000 with zsh and passwordless sudo, linger on, apt baseline, gh 2.101
  from the official repo, Tailscale 1.102.4 installed but not signed in. Lima's own login user
  held gid 1000; the script moves that group to 1001 first.
- DONE `limactl autostart enable vps` (LaunchAgent `io.lima-vm.autostart.vps.plist`). It starts
  the VM when `bebop_admin` logs in, so it still depends on auto-login (Phase A step 2).
- DONE Toolchain for `dev` (`setup/vps-to-mini/provision-dev.sh`, verified): node 22.22.3 via nvm,
  bun 1.4.2 with the `/usr/local/bin/bun` link, uv 0.12 + python 3.11, claude 2.1.278, codex 0.155.
- DONE First data copy (`setup/vps-to-mini/sync-home.sh`): 267,188 files, 21.9 GB, about 35 minutes.
  The copy goes through the mini: `ssh -J bebop_admin@mini -p <lima ssh port> dev@127.0.0.1`. The
  VPS key is in the VM's `~/.ssh/authorized_keys`. systemd user units and the crontab sit in
  `~/migration-holding/` on the VM and are NOT active there (verified: no crontab, no
  `~/.config/systemd`, no extra user units running).
- DONE Firewall on the VM: same rules as the VPS plus SSH from Lima's host-side subnet 192.168.5.0/24.
- DONE Rebuilds (`setup/vps-to-mini/provision-rebuild.sh` plus one-off steps recorded here):
  user site-packages (47, arm64), `pipx install` council 0.5.0 from main, `npm ci` OK in all 12
  Node projects, `ai-harness/.venv` and `swimtrack/editorial/.venv`, portfolio-cockpit release
  `20260921-vm-<sha>` built from main (installed VPS code equals main except `portfolio.json`; its
  original source worktree no longer exists), attain-work-queue release `20260912-f18697d` from
  the same wheel as the VPS. Neither service is started.
- DONE Logins work on the VM with the copied credentials: `claude -p` answers, `codex login status`
  OK, `gh auth status` OK, `git fetch` over https OK. (GitHub over SSH keys is not set up on
  either machine; remotes are https.)
- DONE Parity check: ai-harness test suite on the VM 1882 passed / 1 skipped, identical to the VPS
  (after installing `detect-secrets==1.5.0` into the venv). The VM runs it in 32 s, the VPS in 83 s.
- DONE Leftover x86_64 binaries handled: `~/.local/bin/zoxide` replaced by the apt package;
  `~/.claude/remote` (desktop-app helper binaries) moved to `~/migration-holding/claude-remote-x86`
  so the app fetches arm64 ones. Known and left alone: an archived venv under
  `~/.local/state/portfolio-activation/`.
- DONE Blender for santa-amaro-home-renovation: apt `blender` 4.0.2 (same version as the VPS) on
  the VM; `references-local/blender-home/.python` rebuilt for arm64 with the same pins
  (pillow 12.3.0, pillow_heif 1.7.0, pymupdf 1.28.2; old folder kept in
  `~/migration-holding/blender-python-x86`). Imports work and `model/verify_model.py` exits 0.
  That folder is git-ignored, so the final delta copy must not overwrite it: `sync-home.sh`
  excludes it from now on.

- DONE Autostart proven 2026-09-21 22:50 UTC: Rex restarted the mini; auto-login worked and
  `bebop-vm` was back on the tailnet about 40 s after the mini answered SSH, with nobody at the
  keyboard. Note: Lima's local SSH port changes on every VM start (`sync-home.sh` reads it live).
- DONE VM resized to 8 CPUs / 12 GiB (`limactl edit --cpus 8 --memory 12 vps`). The mini is
  server-only now, so macOS keeps 2 cores and 4 GB.
- DONE Tailscale on the mini is the Standalone variant (`io.tailscale.ipn.macsys`), not the App
  Store one, so no change of variant is needed. Replacing it with brew `tailscaled` would need
  sudo and would re-register the node under a new address; not worth it.
- DONE Mini trim: Rex deleted most desktop apps himself. Moved to the Trash: Bitwarden, Blender,
  ChatGPT, Conductor (3.1 GB, recoverable until the Trash is emptied). Removed the `Magnet` login
  item. Kept: Tailscale, Amphetamine, Safari, Visual Studio Code (Rex's way in if he ever logs on).
  macOS free memory went from 34% to 90%.
- DONE Mini-only data saved to the VM in `~/migration-holding/mini-unique/` (308 files, byte
  count verified equal): romance-empire `generated-images/covers/watercolor/` (untracked, 24 MB)
  plus a diff of its uncommitted AGENTS.md / CLAUDE.md edits, `Projects/a-million-worlds` (not a
  repo), and the Obsidian vault (4 MB). Every other repo in the mini's `~/Projects` has a GitHub
  remote, is clean and has nothing unpushed. Nothing in `~/Projects`, `~/Documents` or
  `~/Downloads` on the mini was deleted.
- OWNER Needs the Mac password (no passwordless sudo on the mini): delete the root-owned apps
  CleanMyMac, Keynote, Numbers, Pages; `sudo mdutil -a -i off` to stop Spotlight indexing; turn
  off automatic macOS update restarts.

- DONE Sessions / memory / references check (2026-09-21 late): file counts on the VM equal the VPS
  for `.claude/commands`, `.codex/sessions` (601), `.codex/skills`, `wiki` (4177), CLAUDE.md files
  (16); `.claude/projects` differs only by files written after the copy (8409 vs 8410 transcripts,
  336 vs 337 memory files) which the final delta brings over. Live tests on the VM: Claude reads
  the memory index and the global CLAUDE.md, lists the same skills and commands, resumes an old
  session by id, and shows the same connector states as the VPS (Slack, Supabase, common-room
  need sign-in on both). Two venvs that the copy skips on purpose were rebuilt:
  `~/loom-runtime/.venv` (via `loom/setup-runtime.sh`) and `~/.claude/skills/skill-creator/.venv`
  (pyyaml). Playwright chromium installed for arm64. After the final delta, re-run this check.

## 4b. Cutover log (2026-09-22, UTC)

- 08:44 VPS crontab saved to `~/migration-holding/crontab.vps.20260922T084434Z.txt` and removed.
  `session-bridge`, `portfolio-cockpit`, `attain-work-queue.timer` disabled and stopped;
  `tailscale serve reset`; all preview/dev servers killed. The 08:00 `diem drain` was mid-run
  (loom backfill, balance close to floor): left to finish, ended ok at about 09:05.
- 09:05 Final delta copy: 3,619 files, 601 MB. (The script's `crontab -l` step now fails on the
  VPS because the crontab is gone; the saved file from 08:44 is used. Removed the empty file it
  left, since `activate-vm.sh` picks the newest `crontab.vps*.txt`.)
- 09:12 `activate-vm.sh` on the VM: three units running, 36 cron lines installed, cockpit served
  at https://bebop-vm.taila64e8f.ts.net (302 to login), ACTIVATE-OK.
- 09:15 First scheduled jobs fired on the VM and logged clean: both engine pollers, Attain Prep
  feedback sync and Bento sync (16 families, 0 emails), `agents once` (6 runs), MeetTrack
  discover, work-queue tick "healthy". `session-gc snapshot` by hand: 8 worktrees snapshotted.
  session-bridge started and archived the two stale VPS topics.
- Still to observe: watchdog (next :30), diem 21:00 checkpoint, Bebop briefing 17:00 UTC, loom
  02:00, backlog-run 03:00, Attain Prep digest next Monday.
- VPS state: idle, cron empty, services disabled, tmux `romance-2` (shell) and this session only.
  Keep powered for 7 days (Phase G).
- Rex's three open chats, to resume on the VM with `claude --resume <id>` from the right folder:
  `3353706c-f59c-4c56-a420-e17dc8b669a6` (TypeSafe skill, in `~/projects`),
  `549300be-8015-48f0-ab86-d0c9c51be717` (original swimtrack app history, in `~/projects`),
  `a162af71-9db4-4363-89cc-f16bc896ddf4` (website flow, in `~/projects/swimtrack-website`).

## 5. Remaining phases

### Phase A: make the VM reachable and durable
1. DONE 2026-09-21 VM is on the tailnet as `bebop-vm` (100.99.202.95), tagged `tag:server`,
   Tailscale SSH on. The ACL lets member devices SSH in as `dev`. The VPS node itself is NOT
   allowed to SSH to `bebop-vm` by tailnet policy ("policy does not permit"), so VPS to VM copies
   keep using the jump through the mini. No ACL change is needed for that.
2. DONE 2026-09-21 Automatic login for `bebop_admin` is on (verified with `defaults read`). Rex also
   deleted the `jane` and `bebop_active` macOS accounts.
3. DONE Autostart proven with a real restart (see section 4).
4. NEXT macOS: stop automatic OS-update restarts; keep Amphetamine or replace it with `pmset`
   settings only (sleep is already 0).
5. DONE ufw as on the VPS. unattended-upgrades, earlyoom and fail2ban are installed from the apt baseline.

### Phase B: toolchain as `dev`
nvm + node 22.22.3, bun, uv + python 3.11, pipx, Claude Code, Codex, Playwright chromium.
`google-chrome-stable` has no Linux arm64 build: anything that calls Chrome by name must switch to
Chromium. Install texlive, blender, caddy only if a job is found that uses them.

### Phase C: first data copy (VPS keeps running)
rsync over Tailscale from the VPS to the VM as `dev`, preserving times and permissions, excluding
`node_modules`, `.venv`/`venv`, `.next`, `dist`, `.astro`, `.wrangler`, `__pycache__`, `~/.cache`,
`~/.npm`, `~/.nvm`, `~/.bun`, `~/.local/share/pipx`, `~/.local/share/uv`, `~/.local/share/claude`.
Include: `~/projects` (with git worktrees), `~/wiki`, `~/loom-runtime`, `~/tailserve`, `~/.env`,
`~/.ssh`, `~/.gnupg`, `~/.config`, `~/.claude`, `~/.codex`, `~/.local/state`, `~/.local/bin`
scripts that are plain files, `~/.zshrc` and friends, the crontab (saved to a file, NOT installed).
The copy is repeatable; a final delta pass runs at cutover.

### Phase D: rebuild what cannot be copied
- `~/.local/lib/python3.12/site-packages` is copied from the VPS but holds x86_64 wheels. Cron jobs
  that use `/usr/bin/python3` (splash_poller, finance backup) depend on it. On the VM: delete that
  folder and reinstall from `setup/vps-to-mini/vps-user-site-packages.txt` (49 packages).
- Node projects to `npm ci` (all use npm lockfiles): aris-management-website, attainprep-site,
  finance-tracker, flight-7-publishing, rmpeacockwriter.com, roam, romance-elliecalloway.com,
  romance-tessacross.com, sat-prep, swimtrack-coach, swimtrack-website, ultimate-portugal.
- Python venvs to recreate: `ai-harness/.venv`, `swimtrack/editorial/.venv`.
- pipx: `pipx install --force /home/dev/projects/ai-harness` (VPS metadata: python 3.12, no
  injected packages).
- Release dirs: build a venv with `pip install '.[cockpit]'` for portfolio-cockpit and the
  workqueue equivalent, under `releases/<name>`, then point `current` at it (see
  `cockpit/README.md`, `workqueue/README.md`). `~/.config/portfolio-cockpit.env` comes over in the copy.
- `pipx install` council from `~/projects/ai-harness` (this recreates the symlinked tools).
- Release directories built from venvs: `~/.local/share/portfolio-cockpit/current`,
  `~/.local/share/attain-work-queue/current`. Rebuild with their own install scripts.
- `npm ci` in each active Node project; project venvs for Python projects.
- Re-check logins: `claude`, `codex`, `gh auth status`, wrangler via `CLOUDFLARE_API_TOKEN`,
  Google OAuth token files used by Bebop.
- Run each repo's test suite and compare against the VPS baseline, not against zero failures.

### Phase E: cutover in one planned switch
Scheduled: morning of Tuesday 2026-09-22, started by Rex in a live session (no scheduled trigger).
Rex re-examined native macOS on 2026-09-21 and confirmed the Linux VM. Morning jobs to steer
around: 05:00 and 05:30 UTC engines, 06:00 UTC Bebop briefing, 15-minute pollers from 06:00 UTC.
If a morning run is already in progress when the switch starts, let it finish on the VPS first.
Finding on 2026-09-21: most jobs follow the working files. `agents once` watches the tmux
sessions, session-gc and backlog-run act on the repos in `~/projects`, loom reads the Claude
session transcripts, the engines commit into their repos. Running those on the VM while Rex still
works on the VPS would make the two copies drift apart. So the cutover is one switch, not a slow
group-by-group move:
1. Pick a quiet window. Not a Monday (Attain Prep parent digest 13:00 UTC, weekly sweeps) and not
   within a few minutes of 07:00 / 18:00 Lisbon (Bebop briefings).
2. Rex closes his sessions on the VPS. Commit or park work in progress.
3. On the VPS: save the crontab, then `crontab -r`; stop and disable `session-bridge`,
   `portfolio-cockpit`, `attain-work-queue.timer`; stop the dev servers.
4. Final `sync-home.sh` delta (minutes, not the first 35).
5. On the VM: repeat the CPU-specific rebuild only where lockfiles changed; move
   `~/migration-holding/systemd/user/*` into `~/.config/systemd/user/` (not the retired
   telegram-orphan-reaper; the mini sshfs mount unit still works unchanged), `daemon-reload`,
   enable the three units; install the saved crontab; re-create `tailscale serve` for :8790.
6. Verify, in this order: `session-bridge` answers on Telegram (only one poller may exist), the
   cockpit URL loads, one `agents once` and one `session-gc snapshot` run clean, `diem drain
   --checkpoint` dry path, the next Bebop briefing arrives, the next Attain Prep Bento sync log line
   is clean, the first 03:00 UTC backlog-run report looks normal.
7. Rex starts working on `bebop-vm` (`ssh dev@bebop-vm`, VS Code Remote-SSH to the same name).

### Phase F: address and name
About 75 files reference `100.64.43.80`, `vps.taila64e8f.ts.net` or `mesh-vps` (58 in ai-harness).
Preferred: at cutover remove the old node, rename the VM node to `vps`, and set its tailnet IPv4
to `100.64.43.80` in the Tailscale admin console, so no file changes. Verify the admin console
still allows editing a node's IPv4 before relying on it; fallback is a search-and-replace PR per
repo. Also check for anything outside the tailnet that points at the VPS public IP
188.245.85.237 (DNS records, webhooks, CI). None is known; the VPS has been mesh-only since
2026-06-05.

### Phase G: soak and decommission
- Run 7 days on the VM with the VPS powered on but idle (all cron commented, services stopped).
- Then: Hetzner snapshot as a last-resort archive, remove the node from the tailnet, clean the
  ACL, cancel the server, update CLAUDE.md files, memory notes and `vps-tools` docs that say
  "VPS" or "Hetzner".

### Phase H: mini host cleanup (owner approved removing what the setup does not need)
Only after Phase E. Review before deleting: `~/Projects` repos with no remote or unpushed work,
`~/Documents`, `~/Downloads`, the `jane` and `bebop_active` accounts, desktop apps (Xcode, Chrome,
MacWhisper, WhatsApp, Discord, VS Code). A full wipe is not needed.

## 6. Open decisions

- OPEN Off-site backup. Today the VPS and the mini back each other up by being in different
  places (the finance-tracker backup goes VPS to mini). After the move, that backup lands on the
  same physical disk as the data. Code is safe on GitHub; `~/.env`, finance data, romance-empire
  assets (7 GB) and `.claude` history are not. Needs an off-site target chosen by Rex, preferably a
  service he already pays for.
- OPEN Whether to keep the Hetzner snapshot after cancellation, and for how long.
- OPEN Cai's access: same shared `dev` user on the VM (no change) is assumed.

## 7. Rollback

Until Phase G the VPS is intact. To roll back any job group: comment it out on the VM, uncomment
it on the VPS. The VM can be deleted with `limactl delete vps` with no effect on the VPS.
