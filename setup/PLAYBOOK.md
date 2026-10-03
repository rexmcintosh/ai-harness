# VPS + Multi-Agent PR Review: Implementation Playbook

> **Historical deployment:** The Hetzner VPS was deleted on 2026-10-02 without a retained snapshot.
> Work now runs on `bebop-vm`, inside the Mac mini. Use the
> [current runtime record](../docs/vps-decommission-2026-10-02.md) for addresses, recovery and backup limits.
> The old VPS setup, public-IP fallback and mini-independent uptime claims below are superseded.


A concrete, executable plan for the @levelsio × Naval hybrid:
**Claude Code lives on a VPS → opens PRs → a Venice-powered council of AI reviewers critiques every PR → you merge → CI deploys to the same VPS.**

You stay solo. The agents argue. You ship from your phone.

> **📍 This playbook is Phase 1 of a larger plan.** It stands up the VPS + Venice
> review pipeline — where Claude Code writes and the council reviews. **Phase 2**
> wraps it in a four-node *personal compute mesh* (VPS + Mac Mini + MacBook +
> iPhone) so your work is reachable from any device, even with the Mini off.
> The design of record and the rest of the build live in
> [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — start there for the big
> picture. Related Phase 2 docs:
> [TAILSCALE](../docs/TAILSCALE.md) ·
> [MAC-MINI-SETUP](../docs/MAC-MINI-SETUP.md) ·
> [MIGRATION](../docs/MIGRATION.md) ·
> [DAILY-LOOP](../docs/DAILY-LOOP.md).

---

## Architecture

```
                       ┌──────────────────────────┐
   Termius (Mac/iPhone)│   tmux + mosh + ssh      │
        ──────────────►│   ~/projects/<repo>      │
                       │   $ claude               │  ◄── Claude Code (writer)
                       │                          │       commits + pushes branch
                       └──────────┬───────────────┘
                                  │ git push (feature branch)
                                  ▼
                       ┌──────────────────────────┐
                       │       GitHub             │
                       │   PR opened → Action:    │
                       │   .github/workflows/     │
                       │     venice-review.yml    │
                       └──────────┬───────────────┘
                                  │ fan-out
                ┌─────────────────┼─────────────────┐
                ▼                 ▼                 ▼
        ┌────────────┐    ┌────────────┐    ┌────────────┐
        │ Architect  │    │  Security  │    │ Simplifier │
        │ Opus 4.7   │    │ DeepSeek   │    │ Qwen 27B   │
        └─────┬──────┘    └─────┬──────┘    └─────┬──────┘
              └──────────────┐  │  ┌──────────────┘
                             ▼  ▼  ▼
                       ┌──────────────────┐
                       │  Aggregator      │
                       │  ── posts ──►    │  one consolidated PR comment
                       │  ── check ──►    │  pass/fail status
                       └──────────────────┘
                                  │
                                  ▼  you read, merge
                       ┌──────────────────────────┐
                       │ deploy.yml on push:main  │
                       │  ssh → VPS → pull → reload
                       └──────────────────────────┘
```

---

## Phase 0 — Decisions you've already made

| Question | Answer |
|---|---|
| Where does compute live? | One Hetzner VPS, always-on. Dev + staging + prod. |
| Who writes? | Claude Code (on the VPS) |
| Who reviews? | Venice AI council (3–4 models, different lenses) |
| Who merges? | You |
| Mobile? | Termius on iPhone, Mosh-backed |
| Deploy style | Naval-style PR gate → CI deploys on merge |

Open decisions left (default in **bold**):
- Anthropic billing for Claude Code: **Max subscription** vs API key vs Bedrock. Max is cheapest if you're heavy.
- One repo per project vs monorepo: **one repo per project** — keeps the review workflow simple.
- Domain/TLS: **Caddy** (auto-TLS) vs nginx+certbot. Caddy is one config file.

---

## Phase 1 — Provision the VPS

**Recommendation:** Hetzner **CCX13** (Dedicated vCPU)
- 2 dedicated AMD vCPU, 8 GB RAM, 80 GB NVMe, 20 TB traffic
- ~€12.49/mo
- Why dedicated, not shared: Claude Code runs long Python/Node processes; shared CPU steals throttle them unpredictably.
- Step up to **CCX23** (4 vCPU / 16 GB, ~€24/mo) if you'll run multiple Claude sessions in parallel or heavy builds.

**Region:** pick whichever is closest to *you*, not your users. Latency to the SSH session is what hurts. Falkenstein (DE) or Ashburn (US-East) typical.

**OS:** Ubuntu 24.04 LTS.

**Steps:**
1. Hetzner Cloud Console → Create Server → CCX13 / Ubuntu 24.04
2. Add your SSH public key during creation (so root login uses your key, not a password)
3. Note the public IPv4
4. Point a domain at it (`A` record `dev.yourdomain.com → <ip>`) — needed later for TLS

---

## Phase 2 — Harden + install base packages

Run `setup/bootstrap-vps.sh` once, as root, on the fresh VPS:

```bash
# from your laptop
scp setup/bootstrap-vps.sh root@<vps-ip>:/root/
ssh root@<vps-ip>
bash /root/bootstrap-vps.sh dev   # 'dev' = the non-root user it creates
```

What it does:
- Creates user `dev` with passwordless sudo and your SSH key
- Disables root SSH + password auth
- UFW firewall: 22/tcp, 80/tcp, 443/tcp, 60000-61000/udp (Mosh)
- Installs: git, build-essential, tmux, mosh, fail2ban, ufw, unattended-upgrades, jq, gh, python3-venv, pipx
- Installs Node.js 20 LTS (for Claude Code) via nodesource
- Installs Caddy (reverse proxy + auto-TLS)
- Configures unattended-upgrades for security patches
- Adds 4 GB swap if RAM < 16 GB
- Sets timezone UTC

After it finishes: `ssh dev@<vps-ip>` (root login is now disabled).

---

## Phase 3 — Install Claude Code

As `dev` on the VPS:

```bash
npm install -g @anthropic-ai/claude-code
claude login    # opens a device-flow URL — paste it into a laptop browser, sign in
claude          # smoke test in any directory
```

Optional but recommended:
- Install GitHub MCP for Claude so it can read issues/PRs natively:
  ```bash
  claude mcp add github
  ```
- Set a project-default `~/.claude/settings.json` permission allowlist so Claude doesn't pause on every `git`/`gh`/`npm` call. See `setup/templates/settings.json`.

---

## Phase 4 — Project conventions (per-repo)

Every repo Claude works in should include a `CLAUDE.md` that pins the workflow.

Copy `setup/templates/CLAUDE.md` into the project root. Key rules it sets:
- Never commit to `main` directly; always work on `feat/<short-slug>` branches
- After each logical chunk: stage, commit with a conventional-commit message, push
- When the feature is done, open a PR with `gh pr create --draft`
- Wait for the Venice review check; address comments; mark ready when satisfied
- Never merge — that's the human's job

---

## Phase 5 — GitHub repo setup

For each project repo:

1. Create the repo (private), push initial commit.
2. **Branch protection on `main`:** require status check `venice/review` to pass, require linear history, no force pushes.
3. Drop these into the repo:
   - `CLAUDE.md` (Phase 4)
   - `.github/workflows/venice-review.yml` (from `setup/templates/`)
   - `.github/workflows/deploy.yml` (Phase 7)
   - `scripts/venice_review.py` (from `setup/templates/`)
4. **Repo secrets** (Settings → Secrets and variables → Actions):
   - `VENICE_API_KEY` — from venice.ai dashboard
   - `DEPLOY_SSH_KEY` — a *deploy-only* SSH private key (different from your personal key) authorized on the VPS for the `dev` user
   - `DEPLOY_HOST` — your VPS IP or hostname
   - `DEPLOY_PATH` — e.g. `/home/dev/projects/<repo>`

---

## Phase 6 — The Venice review council

`setup/templates/venice-review.yml` triggers on every PR. It fans out to a panel of specialists, each with a different lens:

| Persona | Model | Looks for |
|---|---|---|
| **Architect** | `claude-opus-4-7` | Design coherence, abstractions, did the change belong here at all |
| **Bug hunter** | `gpt-5.2-codex` | Off-by-ones, null paths, race conditions, edge cases |
| **Security** | `deepseek-3.2` | Injection, secrets, auth bypass, unsafe deserialization |
| **Simplifier** | `qwen-3.6-27b` | Over-engineering, dead code, premature abstraction |

Each reviewer returns JSON: `{verdict: approve|comment|request_changes, blocking: [...], suggestions: [...]}`. The aggregator merges them into a single PR comment with collapsed sections per reviewer + a top-line summary, and sets the `venice/review` check status:

- All four `approve` → check passes green
- Any reviewer flags `blocking` items → check fails (you can still override and merge — it's a gate, not a wall)

**Why this design beats one big reviewer:** different models hallucinate differently. Disagreement is signal. If three approve and one screams about a SQL injection, that's the one to read carefully.

Tunable knobs (in `venice_review.py`):
- `MODELS_AND_PERSONAS` list — add/remove reviewers
- `MAX_DIFF_BYTES` — cap on diff size sent to each model (default 200 KB; large PRs get summarized)
- `BLOCKING_THRESHOLD` — how many reviewers must flag something for the check to fail (default: 1 security/architect, 2 others)

---

## Phase 7 — Deploy from merged PR

`setup/templates/deploy.yml` runs on `push: main` (i.e. after you merge a reviewed PR). It:

1. Checks out main
2. SSHs to the VPS using `DEPLOY_SSH_KEY`
3. Runs an idempotent deploy script in the project dir:
   ```
   git fetch origin main && git reset --hard origin/main
   ./scripts/deploy.sh   # repo-specific: install deps, build, reload service
   ```
4. Posts deploy status back as a GitHub Deployment

Repo's `scripts/deploy.sh` is the one place you write per-project deploy logic. Keep it dumb: a bash script that knows how to restart your specific app (systemd, `pm2 reload`, `caddy reload`, etc.).

**Rollback:** `ssh dev@vps "cd ~/projects/X && git reset --hard HEAD~1 && ./scripts/deploy.sh"`. That's the whole rollback story. Git is the source of truth.

---

## Phase 8 — Mobile (Termius + iPhone)

**Termius setup:**
1. Install Termius on iPhone + Mac. Sign in same account → hosts sync.
2. Add host: `dev@<vps>` with your SSH key.
3. Enable **Mosh** in the host's settings (Termius supports it natively). Mosh survives disconnects, sleep, network changes — the SSH equivalent of "it just works on the train."
4. Create a "Project" tab per active repo: a snippet that runs `cd ~/projects/<repo> && tmux new -As work && claude`. One tap → land in your live Claude session.

**tmux discipline:**
- One named session per project: `tmux new -As <project>`
- Inside: window 0 = Claude Code, window 1 = shell, window 2 = logs (`journalctl -fu <service>`)
- Sessions persist across disconnects forever; `tmux ls` shows what's running.

**The screenshot-paste problem (the @levelsio open question):**
There's no native fix yet. Two workarounds, pick one:

**Option A — iOS Shortcut + tiny upload endpoint** (best UX):
1. On the VPS, run a small auth-gated upload service (Caddy + a 30-line Python script) at `https://dev.yourdomain.com/upload`
2. iOS Shortcut: "Share Screenshot → POST to /upload → copy returned path to clipboard"
3. In Termius, long-press → paste → you get `/tmp/sc-abc123.png` ready to hand to Claude
4. Total taps: 3. Sketch in `setup/templates/upload-shortcut.md`.

**Option B — Termius SFTP** (works today, more taps):
1. Termius has a built-in SFTP browser.
2. Upload screenshot from Photos → drop in `/tmp/`.
3. Copy the path manually.

When Termius adds native image-paste, drop both.

---

## Phase 9 — Safety nets

You're editing real infrastructure from a phone. Belt and suspenders:

- **Hetzner snapshots:** automate daily via the Hetzner API or just the console schedule. Costs ~€0.50/mo for an 80 GB image.
- **Git is your undo:** every Claude commit is a revert point. The CLAUDE.md template enforces small, frequent commits.
- **Database backups (if you have one):** nightly cron → `pg_dump | restic backup -r b2:bucket` (or rclone to Backblaze B2 / R2). Restic dedupes — first backup ~minutes, subsequent ~seconds.
- **Secrets:** never in the repo. Use a `.env` file in `~/projects/<repo>/.env` owned `dev:dev 600`, loaded by the app. CI deploys never touch it.
- **`unattended-upgrades`** (installed by bootstrap) auto-applies security patches.
- **Off-VPS monitoring:** UptimeRobot free tier → ping your domain every 5 min, SMS on failure.
- **Kill switch:** in Termius, save a snippet `EMERGENCY: sudo systemctl stop <service>` so you can yank a misbehaving deploy from the lock screen.

---

## The day-to-day operating loop

1. Open Termius (laptop or phone). Tap your project's snippet.
2. You land inside the tmux session, Claude Code prompt waiting.
3. Type the bug or feature in English.
4. Claude works → commits to `feat/<slug>` → opens draft PR.
5. GitHub Action fires → Venice council reviews → posts a single PR comment within ~60s.
6. Read the comment. If the council flagged something real, tell Claude in the same session: "the security reviewer is right about X, fix it." Claude pushes a fixup commit; the action re-runs.
7. When green, mark PR ready, merge.
8. Deploy workflow ships it to the VPS. UptimeRobot confirms it's still up.

The whole loop runs from your phone if you want.

---

## What's in this repo to support all of the above

```
setup/
  PLAYBOOK.md                            ← you are here
  bootstrap-vps.sh                       ← one-shot VPS provisioning (Phase 2)
  templates/
    CLAUDE.md                            ← drop into each project repo (Phase 4)
    settings.json                        ← Claude Code permission allowlist
    venice-review.yml                    ← .github/workflows/ (Phase 6)
    venice_review.py                     ← scripts/ (Phase 6)
    deploy.yml                           ← .github/workflows/ (Phase 7)
    upload-shortcut.md                   ← screenshot-paste sketch (Phase 8)
```

Files are starters — read them, tweak the constants at the top, copy into your real project repos.

---

## Sequenced TODO (if you want to do this in one sitting)

1. ☐ Create Hetzner CCX13, point a domain at it
2. ☐ `scp` + run `bootstrap-vps.sh`
3. ☐ Install Claude Code on the VPS, log in
4. ☐ Set up Termius hosts on Mac + iPhone with Mosh
5. ☐ Pick one real project; create its GitHub repo
6. ☐ Drop the four template files in; add repo secrets (`VENICE_API_KEY` etc.)
7. ☐ Push a trivial PR end-to-end as a smoke test — confirm Venice review fires, deploy works
8. ☐ Tune the reviewer personas to your taste

Ballpark: a focused afternoon.
