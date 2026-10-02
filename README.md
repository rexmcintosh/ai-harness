# ai-harness

Formerly `build-ai-automation-workflow` (renamed 2026-09-18). Older docs use the old name.

Home of the VPS + Venice AI multi-agent PR review setup, plus the automation workflow built on top of it.

For current automation work, start with the [outcome-led assessment](docs/automation-ops-2026-09-05.md)
and [scheduled-job contracts](docs/contracts/README.md). They distinguish observed
runtime behavior from the historical architecture below.

> **New here? Start with [`docs/OVERVIEW.md`](docs/OVERVIEW.md)** — a plain-English,
> non-technical explanation of what this project is and the thinking behind it
> (the @levelsio × Naval inspiration, the AI-builds → AI-panel-reviews → human-approves model).

The dev infrastructure model: **Claude Code runs on a Hetzner VPS → opens PRs → a Venice AI council critiques every PR → you merge → CI deploys to the same VPS.** See `setup/PLAYBOOK.md` for the full architecture, provisioning steps, and day-to-day loop.

## Two phases

- **Phase 1 — VPS + Venice review** (`setup/`): stand up a Hetzner VPS where Claude Code writes, opens PRs, and a Venice AI council reviews each one before you merge + deploy. Start at [`setup/PLAYBOOK.md`](setup/PLAYBOOK.md).
- **Phase 2 — Personal compute mesh** (`docs/`): wrap that VPS in a four-node mesh (VPS + Mac Mini + MacBook + iPhone) so work is reachable from any device, even with the Mini off. Start at [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## docs/ — Phase 2 (the mesh)

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — design of record: the rule, the ASCII diagram, where each kind of work lives
- [`docs/TAILSCALE.md`](docs/TAILSCALE.md) — private mesh: install on all four nodes, SSH ACL/grants, verify
- [`docs/MAC-MINI-SETUP.md`](docs/MAC-MINI-SETUP.md) — never-sleep, Remote Login, Time Machine + Backblaze, Tailscale-at-boot
- [`docs/MIGRATION.md`](docs/MIGRATION.md) — get local projects into GitHub, then clone onto the host
- [`docs/DAILY-LOOP.md`](docs/DAILY-LOOP.md) — the operating manual (start / suspend / push / resume anywhere)
- `scripts/` — [`audit-projects.sh`](scripts/audit-projects.sh) (inventory) · [`migrate-project.sh`](scripts/migrate-project.sh) (migrate)

## Components (the AI layer)

- [`council/`](council/README.md) — multi-model Venice review panel. `review`/`ask`,
  plus `compare` (rank N candidate solutions — "waste tokens, save time") and `sweep`
  (autonomous repo-wide security research).
- [`watchdog/`](watchdog/README.md) — autonomous SRE: watches the system's own moving
  parts and proposes fixes to Telegram (never touches prod).
- [`fixit/`](fixit/README.md) — feedback → fix → ship: an issue becomes a PR into the
  council CI gate; you merge.
- [`bebop/`](bebop/README.md) — personal assistant: twice-daily Gmail+Calendar briefing.
- [`loom/`](loom/README.md) — session-learning pipeline: distills transcripts into woven
  learnings.
- [`backlogrun/`](backlogrun/README.md) — the 3am backlog runner: works `~/projects/backlog`
  items unattended on isolated branches, council-reviews them, and leaves a morning
  report; `approve`/`drop` are the human half.

> watchdog, `council compare`/`sweep`, and fixit were distilled from Naval's
> *AI Industrial Revolution* podcast — see
> [`docs/superpowers/specs/2026-06-16-industrial-features-design.md`](docs/superpowers/specs/2026-06-16-industrial-features-design.md).

## Layout

- `setup/PLAYBOOK.md` — implementation playbook (start here)
- `setup/bootstrap-vps.sh` — one-shot Ubuntu 24.04 VPS provisioning
- `setup/templates/` — files to drop into this repo (and any future project):
  - `CLAUDE.md` — agent conventions (branch/commit/PR discipline)
  - `settings.json` — Claude Code permission allowlist
  - `venice-review.yml` — GitHub Action that runs the review council
  - `venice_review.py` — the council itself (4 specialist personas, fan-out + aggregate)
  - `deploy.yml` — GitHub Action that ships merged PRs to the VPS
  - `upload-shortcut.md` — iPhone screenshot-paste workaround
