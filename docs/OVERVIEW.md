# Building a One-Person AI Software Team — Project Overview

> **Historical deployment:** The Hetzner VPS was deleted on 2026-10-02 without a retained snapshot.
> Work now runs on `bebop-vm`, inside the Mac mini. Use the
> [current runtime record](vps-decommission-2026-10-02.md) for addresses, recovery and backup limits.
> The old VPS setup, public-IP fallback and mini-independent uptime claims below are superseded.


> A plain-English overview of what this project is and the thinking behind it.
> Written for a non-technical audience — concepts over technical specifics.
> For the how-to, see [`../setup/PLAYBOOK.md`](../setup/PLAYBOOK.md) (the build)
> and [`ARCHITECTURE.md`](ARCHITECTURE.md) (the design of record).

## The big idea

Most software gets built by a *team*: someone writes the code, someone else
reviews it, someone decides what ships, and someone keeps the infrastructure
running. This project asks a simple question:

**What if one person could run that entire operation — by putting AI in every
seat except the one that matters most: final judgment?**

The result is a setup where **an AI does the building, a panel of other AIs
reviews the work, and I stay in the chair that decides what's good enough to
ship** — all reachable from any device, anywhere.

## Where it came from

The design is a deliberate fusion of two people's philosophies. Neither is really
about technology — both are about *how one person can operate*. The project is
what you get when you take both seriously at once.

**Pieter Levels (@levelsio) — the solo operator.** Levels is the patron saint of
the one-person business: he builds and runs multiple profitable products entirely
by himself, no co-founders, no employees. His ethos rejects the idea that doing
something big requires a big organization. Stay solo and *automate* instead of
hiring. Keep it radically simple — he famously runs real businesses on a single
always-on server with boring, reliable tools. Work from anywhere, on any device.
Ship constantly, and use AI to multiply yourself. His fingerprint on this project
is the **form**: solo, simple, always-on, reachable from your phone, shipping fast.

**Naval Ravikant — the philosopher of leverage.** Naval's big idea is *leverage*:
the things that let one person's effort have outsized impact. His key insight is
that the most powerful modern leverage is **code and automation** — a
permissionless army that works for you around the clock at no marginal cost.
*"Code works while you sleep."* But the part that matters most here is his second
insight: once you have near-infinite leverage, the scarce and valuable human skill
becomes **judgment**. When execution is essentially free and unlimited, the whole
game is deciding *what's worth doing* and *what's good enough*. **Leverage
amplifies judgment.** His fingerprint on this project is the **philosophy**:
automation as leverage, and judgment as the one thing you keep for yourself.

Put them together and you get the operating model in one line: **Naval's leverage,
run in levelsio's style.** The AI agents are Naval's permissionless army; the solo,
one-server, phone-first, ship-fast packaging is levelsio's indie playbook.

## The three pieces

**1. An always-on "headquarters" in the cloud.**
Instead of work living on my laptop — asleep when I close the lid, stuck at home,
easy to lose — it lives on a single always-on machine in the cloud. Every device
I own is just a **window** into that one place. I can start a task at my desk,
glance at it from my phone on the go, and the work never paused; it was running in
the cloud the whole time. Nothing to copy, nothing to sync, nothing to lose.
*(This is levelsio's one-server, work-from-anywhere instinct made real.)*

**2. An AI that does the actual building.**
An AI agent does the hands-on work — writing and changing the software. Crucially,
it doesn't just hand me a finished thing; it **proposes** its work formally, the
way a junior team member submits work for review rather than pushing straight to
production.

**3. A "council" of AI reviewers.**
This is the heart of the project. Instead of trusting one AI to check the work, I
built a **panel of different AI models**, each reviewing from a different angle —
one judges the overall **design**, one hunts for **bugs**, one looks for
**security** problems, one flags **over-complication**. They review the same work
independently, and they *disagree*. That disagreement is the point: different AI
models have different blind spots, so a panel catches what any single reviewer
would miss. I read their combined verdict and decide. **The AIs argue; I
adjudicate.**

## Where the human fits

My role deliberately shrinks down to the **highest-value part**: steering and
final approval. The AI writes. The AIs review. **I decide what ships.** Nothing
goes live without a human "go."

This is the most direct expression of the whole philosophy — the literal
embodiment of *"leverage amplifies judgment."* Scale the execution infinitely with
AI, then funnel all of it through a single human point of judgment. That's the one
thing the automation isn't allowed to own.

## The guardrails — why this is trustworthy, not reckless

- **Everything is tracked and reversible** — every change is recorded, so anything
  can be traced or undone.
- **Nothing ships without review and a human green-light** — the AI can't push
  straight to production.
- **The whole headquarters is private** — a recent chapter of the project locked
  it down so it's reachable *only* by my own devices and invisible to the outside
  world.

Guardrails like these are what make it safe to hand the keys to AI without losing
control.

## The throughline

It's two things at once: **a workshop** (the always-on, reachable-anywhere
infrastructure) and **a way of working in it** (AI builds → AI panel reviews →
human approves → it ships). Solo in headcount, but with the checks and balances of
a real team. The north-star sentence that's guided it the whole way:

> **You stay solo. The agents argue. You ship from your phone.**

That one line *is* the hybrid — levelsio's *solo* and *ship from your phone*
wrapped around Naval's *agents as leverage* doing the arguing, so your judgment is
the only human input left.

## The transferable lessons — the part worth discussing as a group

1. **Treat AI as a team, not a tool.** The leverage isn't one super-assistant —
   it's *roles*: a builder, a panel of critics, and a human judge.
2. **A panel beats a single reviewer.** Because models fail differently, a
   diversity of reviewers catches more than any one model, however good.
3. **Keep the human in the highest-leverage seat.** Automate the writing and the
   checking; reserve human attention for judgment and approval.
4. **Make the work location-independent.** When your work lives in one always-on
   place, every device becomes interchangeable and nothing is ever stuck on the
   other computer.
5. **Guardrails are what make automation trustworthy.** Versioning, review gates,
   and a required human "go" are what let you scale with AI without losing control.

---

## Appendix — the tools, and why

A slightly more technical companion: the actual stack, and the reasoning behind
each pick. The guiding principle was *boring and reliable everywhere, except the
AI layer* — keep the foundation dull and proven so the only novel moving parts are
the ones doing the new work.

### The AI layer (the genuinely novel part)

- **Claude Code** — the agent that does the building. *Why:* it doesn't just
  autocomplete — it plans, edits files, runs commands, and drives the repo end to
  end. It can operate as a teammate, not a typing aid.
- **Venice AI** — the inference platform behind the reviewer council. *Why:* one
  API gives access to many *different* models, and it's privacy-first. For a review
  panel, the **diversity of models is the whole point** — so a provider that
  offers a spread of them is more valuable than any single "best" model.
- **The council** (custom-built) — a small tool that fans a change out to a panel
  of model "personas" (design, bugs, security, over-complication) and synthesizes
  their verdicts into one. *Why:* nothing off-the-shelf did multi-model,
  multi-perspective review boiled down to a single human-readable verdict, so I
  built it. It deliberately uses a *mix* of models (Claude, GPT, DeepSeek, Qwen)
  because they fail differently.

### The home base (infrastructure)

- **Hetzner Cloud** — the always-on server. *Why:* dedicated-CPU performance at a
  fraction of the big clouds' price. Long-running AI sessions need CPU that isn't
  being silently throttled, and Hetzner gives that cheaply.
- **Ubuntu LTS** — the operating system. *Why:* boring, stable, universally
  supported. The foundation should be the least interesting thing in the stack.
- **GitHub + GitHub Actions** — the code's permanent home and the automation
  engine. *Why:* it's the source of truth that survives any single machine dying;
  its "pull request" flow is a natural review gate; and Actions automatically runs
  the review council on every change and ships approved work.

### Access from anywhere

- **Tailscale** — a private network that quietly links all my devices. *Why:* it
  makes the server reachable privately from any device with **no public doors and
  no fiddly key management**. It's what makes "every device is just a window" real
  — and it's what let me recently lock the server down to my devices only.
- **tmux** — keeps work sessions alive on the server. *Why:* the work keeps running
  when you disconnect, so you can start at your desk and resume on your phone in
  the exact same place.
- **Termius + Mosh** — a mobile-friendly way to connect. *Why:* one tap drops me
  into a live session from my phone, and the connection survives roaming and sleep
  — so it genuinely "just works on a train."

### Keeping it safe

- **Firewall + auto-ban + automatic security updates** — basic host hygiene.
  *Why:* small attack surface, brute-force attempts get banned, and the machine
  stays patched without me thinking about it.
- **What got removed:** a public web server (Caddy) that was no longer serving
  anything real got retired; any *private* web endpoint needed in future will be
  published over the mesh instead of the public internet. *Why:* the best security
  move is usually removing surface area, not adding locks.

**The throughline of the choices:** single server, privacy by default, proven and
unglamorous foundations — so the only "exotic" tools are the AI ones actually
doing the new work.
