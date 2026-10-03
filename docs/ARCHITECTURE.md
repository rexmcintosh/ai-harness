# Architecture — Personal Compute Mesh

> **Historical deployment:** The Hetzner VPS was deleted on 2026-10-02 without a retained snapshot.
> Work now runs on `bebop-vm`, inside the Mac mini. Use the
> [current runtime record](vps-decommission-2026-10-02.md) for addresses, recovery and backup limits.
> The old VPS setup, public-IP fallback and mini-independent uptime claims below are superseded.


> **Status:** This is the design of record for **Phase 2** of the build.
> Phase 1 (Claude Code on a VPS + the Venice review council) is documented in
> [`../setup/PLAYBOOK.md`](../setup/PLAYBOOK.md). Phase 2 wraps that single VPS
> in a four-node mesh so your work is reachable from anywhere, on any device,
> even with your home machine powered off.
>
> **The VPS is not provisioned yet.** Anywhere this document says "the VPS,"
> the first-time setup lives in PLAYBOOK Phases 1–3 (provision → harden →
> install Claude Code). Do that before the mesh-wiring docs
> ([TAILSCALE.md](TAILSCALE.md), [MIGRATION.md](MIGRATION.md)).

---

## The one rule

If you remember nothing else, remember this:

> **Code lives in GitHub. Active sessions live on the VPS. The Mac Mini is a
> workstation. The MacBook and iPhone are thin clients.**

Everything below is just the consequences of that one sentence. When you're
unsure where something should go, re-read it.

---

## Why a mesh at all?

The problem this solves: **you want to start a piece of work on one device and
keep going on another — at home, on a train, on your phone — without ever
copying files around or worrying about which machine has "the real version."**

The naive setup (do everything on the Mac Mini at home) breaks the moment you
leave the house: the Mini might be asleep, your laptop has a stale copy, and
you end up emailing yourself zip files. The mesh fixes this by giving every
piece of state exactly **one canonical home**, and making that home reachable
from everywhere:

| Kind of state | Canonical home | Why there |
|---|---|---|
| Source code & history | **GitHub** | Survives any single machine dying. Already the hub of the Phase 1 review loop. |
| Running work (a Claude Code session, a half-finished thought, a dev server) | **VPS** | Always on, always reachable, independent of your home power/network. |
| macOS-only tasks (Xcode, screenshots, local apps, backups) | **Mac Mini** | The one node that *is* a Mac and never sleeps. |
| You, typing | **Whatever device is in your hand** | MacBook and iPhone hold no canonical state — lose one, lose nothing. |

The key idea for a newcomer: **"thin client" means the device is just a window.**
Your iPhone in Termius isn't *running* the work — it's looking at work running
on the VPS. Close the app, the work keeps going. Open it on the MacBook an hour
later, same session, exactly where you left it.

---

## The four nodes

```
                         ┌─────────────────────────────────────┐
                         │              GitHub                  │
                         │   Every repo · every active branch   │
                         │   The single source of truth for     │
                         │   all CODE. Daily push discipline.   │
                         └───────────────┬─────────────────────┘
                                         │  git push / clone / pull
                                         │  (over the public internet, HTTPS/SSH)
                                         ▼
   ════════════════════════ Tailscale private mesh (WireGuard) ════════════════════════
   ║   Every node below joins one private network. They reach each other by         ║
   ║   stable names (e.g. `vps`, `mini`) regardless of physical location or NAT.    ║
   ║   Nothing here is exposed to the public internet except the VPS's 22/80/443.   ║
   ║                                                                                ║
   ║   ┌──────────────────────────┐         ┌──────────────────────────┐           ║
   ║   │        VPS (Hetzner)     │         │      Mac Mini (home)     │           ║
   ║   │   ── ALWAYS ON ──        │         │   ── NEVER SLEEPS ──     │           ║
   ║   │                          │         │                          │           ║
   ║   │  tmux: one session/repo  │         │  Workstation when home   │           ║
   ║   │   └ Claude Code (writer) │         │  Backup target:          │           ║
   ║   │  ~/projects/<repo>       │         │   · Time Machine (local) │           ║
   ║   │  Opens PRs → Venice      │         │   · Backblaze (offsite)  │           ║
   ║   │  council reviews (Ph.1)  │         │  Home for macOS-only work│           ║
   ║   │                          │         │  (Xcode, native apps)    │           ║
   ║   └────────────┬─────────────┘         └────────────┬─────────────┘           ║
   ║                │                                     │                         ║
   ║                │   both reachable over Tailscale     │                         ║
   ║                ▼                                     ▼                         ║
   ║        ┌───────────────────────────────────────────────────┐                  ║
   ║        │           Thin clients (no canonical state)        │                  ║
   ║        │                                                    │                  ║
   ║        │   ┌──────────────────┐    ┌──────────────────┐    │                  ║
   ║        │   │  MacBook (travel)│    │  iPhone (travel) │    │                  ║
   ║        │   │  Termius → ssh   │    │  Termius → ssh   │    │                  ║
   ║        │   │  into VPS tmux   │    │  into VPS tmux   │    │                  ║
   ║        │   └──────────────────┘    └──────────────────┘    │                  ║
   ║        └───────────────────────────────────────────────────┘                  ║
   ════════════════════════════════════════════════════════════════════════════════
```

### VPS (Hetzner) — where active sessions live
- Always on. The home for **running work**: Claude Code sessions in `tmux`, dev
  servers, anything in progress.
- Reachable from any device, even when the Mac Mini is off. This is the whole
  point — your sessions don't depend on your house.
- Runs the Phase 1 loop: Claude writes on a feature branch → pushes → the Venice
  review council critiques the PR → you merge → CI deploys back to the VPS.
- One named `tmux` session per project. Disconnecting your laptop or phone does
  **not** stop the session; it keeps running and you reattach later.
- **Default spec:** Hetzner CCX13 (2 dedicated vCPU, 8 GB RAM). See PLAYBOOK
  Phase 1 for sizing.

### Mac Mini (home) — workstation + backup anchor
- Your primary keyboard-and-monitor machine **when you're at home**. Full macOS:
  Xcode, native apps, big screens.
- **Never sleeps** (configured in [MAC-MINI-SETUP.md](MAC-MINI-SETUP.md)) so it's
  always available as a Tailscale node and backup target.
- **Backup anchor for the mesh:** Time Machine (local disk) + Backblaze
  (offsite, continuous). If the VPS ever vanished, GitHub has the code and the
  Mini has everything else.
- The **only** place macOS-bound workflows run. The VPS is Linux; it can't build
  an iOS app or run a Mac app. Those stay here.
- It is **not** required for active sessions. It can be off and you keep working
  on the VPS from the road. It just can't be your backup anchor while it's off.

### MacBook + iPhone (travel) — thin clients
- Windows into the VPS, nothing more. Connect with **Termius** (an SSH/Mosh app)
  over Tailscale, attach to the project's `tmux` session, work.
- **Hold no canonical state.** Don't `git clone` "the real copy" here and edit it
  locally as the source of truth — if you do, you've broken the one rule and
  created a second place where code lives. (Cloning a repo to read or to run a
  quick local build is fine; just push or discard, never let it drift.)
- Lose the laptop or the phone → you lose a *window*, not any *work*. Buy a new
  one, install Termius + Tailscale, you're back.

### GitHub — the substrate
- Holds **every repo and every active branch.** Not just merged code — your
  in-progress feature branches live here too, pushed daily.
- This is what makes the VPS disposable and the laptop replaceable. The VPS is
  "just" a clone of GitHub plus running processes. Rebuild it from scratch and
  `git clone` everything back.

### Tailscale — the wiring
- A private network ("mesh") that all four nodes join. After setup, they reach
  each other by name (`vps`, `mini`) over an encrypted link, no matter whose
  Wi-Fi you're on or whether you're behind a router/NAT.
- Means you do **not** expose your Mac Mini to the public internet. The only
  publicly reachable ports anywhere are the VPS's `22` (SSH), `80`/`443` (web).
- Full install/verify steps: [TAILSCALE.md](TAILSCALE.md).

---

## Where does this work live? (decision table)

When you're about to start something and you're not sure which node, find the
row that matches:

| You want to… | Do it on | Because |
|---|---|---|
| Write/run/debug code, run Claude Code | **VPS** | Active sessions live on the VPS. Reachable anywhere, survives disconnects. |
| Keep a long-running process alive (dev server, training run, watcher) | **VPS** (in `tmux`) | The Mini might sleep or you might leave home; the VPS won't. |
| Edit from a coffee shop / on the train | **VPS via MacBook or iPhone** | Thin clients attach to the VPS session; the device is just a window. |
| Build an iOS/macOS app, use Xcode, run a Mac-only app | **Mac Mini** | The VPS is Linux. macOS work has only one home. |
| Take/annotate screenshots, scan, use local peripherals | **Mac Mini** | Physical/desktop tasks need the real Mac. |
| Store the canonical copy of any code | **GitHub** (pushed from the VPS) | Code never lives only on one machine. |
| Keep a backup of everything not in Git | **Mac Mini** (Time Machine + Backblaze) | The Mini is the backup anchor. |
| Save a file you'd cry to lose | **A Git repo, pushed** — or the Mini's backed-up disk | Those are the only two durable homes. The VPS and travel devices are not. |

**Anti-patterns (these break the one rule):**
- ❌ Editing the "real" copy of a repo on your MacBook and treating that as truth.
- ❌ Leaving a finished day's work only in the VPS's working directory, unpushed.
- ❌ Running a macOS-only workflow on the VPS and being surprised it fails.
- ❌ Exposing the Mac Mini directly to the internet to reach it from the road —
  that's what Tailscale is for.

---

## Operating principles

1. **Code in GitHub, always.** Every repo has a GitHub remote. Every active
   branch is pushed. The discipline is **daily**: before you walk away,
   everything is on GitHub. The full habit is in [DAILY-LOOP.md](DAILY-LOOP.md).
2. **Sessions on the VPS, always reachable.** Work happens inside named `tmux`
   sessions so it outlives any disconnect and any device.
3. **The Mini is a workstation, not a dependency.** Great to use at home, but
   nothing in your daily loop should *require* it to be on — except its job as
   backup anchor.
4. **Travel devices are disposable windows.** No canonical state, ever.
5. **Private by default.** Tailscale for node-to-node. Public exposure limited to
   the VPS's 22/80/443. (You've confirmed the VPS is fine for all your data, so
   there's no "keep this off the VPS" carve-out — but the network stays private
   anyway, because there's no reason not to.)

---

## What happens when a node is offline?

Understanding the failure modes is the fastest way to internalize the design.

| Offline node | What still works | What you lose |
|---|---|---|
| **Mac Mini off** | All active development. You work on the VPS from any device. | macOS-only tasks; backups pause until it's back. **No code or session is lost.** |
| **MacBook lost/dead** | Everything. Switch to iPhone or any other machine. | A window. Re-install Termius + Tailscale on a replacement. |
| **iPhone lost/dead** | Everything. Use the MacBook. | A window. |
| **VPS down** (rare; you'd rebuild) | GitHub still has all code; the Mini still has backups. | Running sessions (in-progress, unpushed work since last push). This is *why* daily push discipline exists — it caps the loss at "since this morning." |
| **GitHub down** (rare, usually brief) | Local clones on the VPS keep working; commit locally. | Pushing/PRs until it's back. Code is safe in the VPS clone meanwhile. |
| **Home internet down** | The VPS and your work are unaffected — reach them over cellular from the iPhone, or tether the MacBook. | Access to the Mini until home network returns. |

The pattern: **no single node failing can destroy work**, because code is in
GitHub and everything else is backed up on the Mini. The worst case (VPS dies
mid-session) costs you at most one day, by design.

---

## How the pieces get built (and where each is documented)

This file is the map. The rest of Phase 2 wires it up, in order:

1. **[ARCHITECTURE.md](ARCHITECTURE.md)** — this document. The design of record.
2. **[`../scripts/audit-projects.sh`](../scripts/audit-projects.sh)** — run on the
   Mini to inventory `~/Projects/`: which are Git repos, which have remotes,
   which are dirty. Drives what needs migrating.
3. **[TAILSCALE.md](TAILSCALE.md)** — join all four nodes to the private mesh.
4. **[MAC-MINI-SETUP.md](MAC-MINI-SETUP.md)** — never-sleep, SSH, Time Machine,
   Backblaze, Tailscale-at-boot.
5. **[MIGRATION.md](MIGRATION.md)** + **[`../scripts/migrate-project.sh`](../scripts/migrate-project.sh)**
   — take a local project, ensure a GitHub remote, push everything, clone to the
   VPS, open a `tmux` session.
6. **[DAILY-LOOP.md](DAILY-LOOP.md)** — the operating manual: start work, suspend,
   what to push before walking away, resume on any device.

Prerequisite for steps 3–6: the VPS exists and runs Claude Code
([`../setup/PLAYBOOK.md`](../setup/PLAYBOOK.md) Phases 1–3).

---

## Glossary (for the newcomer)

- **VPS** — Virtual Private Server. A Linux computer you rent in a data center,
  always on, reachable over the internet. Here: one Hetzner box.
- **Node** — any one machine in the mesh (VPS, Mini, MacBook, iPhone).
- **Tailscale** — software that stitches your devices into one private,
  encrypted network so they can talk by name from anywhere. Built on WireGuard.
- **tmux** — a "terminal multiplexer" on the VPS. It keeps your shell sessions
  (and the programs in them, like Claude Code) running even after you disconnect.
  You "attach" to reconnect to a still-running session.
- **Termius** — an SSH/Mosh client app for Mac and iPhone. Your "window" into the
  VPS.
- **Mosh** — "mobile shell," an SSH alternative that survives network changes and
  sleep (great on a phone or train).
- **Thin client** — a device that displays/controls work running elsewhere, while
  holding no important state of its own.
- **Canonical** — "the one true copy." The home a piece of state actually lives
  in; every other copy is derived from it and disposable.
