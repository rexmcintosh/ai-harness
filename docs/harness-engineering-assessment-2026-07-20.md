# Assessment: lopopolo/harness-engineering vs. our harness

**Date:** 2026-07-20
**Subject:** `github.com/lopopolo/harness-engineering` @ `226c8d3` (trunk), cloned to `~/projects/harness-engineering`
**Compared against:** our harness — `~/.claude/`, `~/wiki/`, `~/loom-runtime` (council, loom, bebop, watchdog, session-gc, diem, fixit), `~/projects/backlog/`

---

## 1. What his repo actually is

It is **not a tool**. Nothing runs. It is ~50,000 words of prose in 44 markdown
files, deliberately built as a *reference library an agent looks things up in* —
he calls it "a retrieval-optimized context bundle."

The idea: you point a coding agent at this repo *alongside* the system it's
supposed to improve. When the agent hits a decision the local codebase doesn't
answer, `AGENTS.md` routes it to exactly one of twelve essays, it reads that one,
and it goes back to work. The routing is the product as much as the prose is.

**The core argument.** Treat the model and the coding agent as a fixed black box.
Don't try to make the model smarter — improve the two things outside it that you
control: **context** and **tools**. Then curate the environment around those.

**The deeper goal**, which is the part worth stealing: an organization's real
operating knowledge — its quality bar, its procedures, its exception history, who
is allowed to approve what — lives in people's heads and old Slack threads. Model
weights contain only the public tip of that iceberg. Harness engineering is the
last-mile work of dragging the underwater part into the repository as retrievable
context, worked examples, types, tests, and executable constraints.

His slogan for it: **make the repository teach the agent.** And: *code is prompts
for future prompts* — a weak pattern left in the tree keeps shaping later agent
runs long after its original bug was fixed.

**The twelve theses**, compressed:

| Thesis | One line |
|---|---|
| Hold the worker constant | Fix model+agent for an "epoch"; requalify everything when it changes |
| Last-mile deployment | Get the private, changing process data to the agent |
| Give one agent the whole job | One trajectory owns decomposition → execution → proof → closure |
| Route context just in time | Big navigable store, **small active working set** |
| Make capabilities legible | A tool must survive discovery → selection → invocation → interpretation → repair → real verification |
| Make the repository teach | Canonical owners, repeated structure, completed migrations, examples, types |
| Autonomy inside explicit authority | Capability and permission are separate contracts |
| Prove the outcome | Match evidence to the *claim*; a green check proves only its own assertion |
| Turn feedback into infrastructure | Recurring corrections become the environment |
| Preserve coherence, own lifetime risk | Implementation is cheap; coherence and carrying cost are scarce |
| Run known work as a continuous loop | Settled work gets a runbook, a trigger, and durable state |
| Optimize measured effectiveness | Tokens and PRs are inputs; accepted outcomes are the objective |

Plus two executable procedures (`playbooks/`), a comparative eval method
(`evals/`), and a preserved source library.

---

## 2. What we've been building

We haven't been writing a theory. We've been standing up a **running personal
operating system** — and the unit isn't one repository, it's the whole surface:
31 projects, a 203-article wiki, 100 memory files, email, calendar, Telegram, a
swim-meet ingestion pipeline, three live websites, a solar install, and federal
contracting.

The interesting finding of this assessment is that **we independently built most
of the machinery he theorizes about.** This is a convergence, not a gap.

| His thesis | What we already run |
|---|---|
| Turn feedback into infrastructure | **loom** — nightly transcript distillation → wiki/memory/skills on a shadow branch; review one diff; `loom promote` |
| Run known work as a continuous loop | 8 cron loops: bebop 2×/day, watchdog /30min, session-gc /10min + weekly sweep, diem 3×/day, splash_poller /5min, agents /1min, security sweep weekly, loom nightly |
| Route context just in time | global `CLAUDE.md` + 13 skills + 7 commands + `MEMORY.md` index + 203-article wiki |
| Autonomy inside explicit authority | the merge protocol; watchdog "never touches prod"; the 3am-runner safety contract |
| Make capabilities legible | `council`, `session-gc`, `diem`, `agents` — real CLIs on PATH with `--help` |
| Keep review convergent | council v0.4.0: chair-arbitrated, risk-tiered, `COUNCIL_ENFORCE=0` advisory mode |
| Code reds need maintenance loops | session-gc's rule after it deleted live workspaces: *"mutations are only ever triggered by an explicit command or a stable clock, never by a fuzzy session-end event"* |

That last row is his exact pattern — an incident becomes a durable golden
principle — and we got there the hard way, on our own, before reading any of this.

---

## 3. The real gaps

Each verified against the actual system, ordered by consequence — his own rule
for a review's findings.

### Gap 1 — Our authority contract has two competing owners, and they've drifted

This is the highest-consequence finding.

The merge protocol — the rule that governs what an agent may do to `main` —
exists in **two places**: `~/.claude/CLAUDE.md` (85 lines, 6 sections) and
`~/projects/AGENTS.md` (42 lines, 4 sections). They are not the same document.
`AGENTS.md` is an older, weaker copy. Diffed, it is missing:

- **the push requirement** — its step 2 says "execute the merge and report,"
  with no push and no `git status -sb` verification that `main` isn't `[ahead N]`
- **the scope-limiting clause** — *"The approval covers merging and pushing
  exactly the work in the block — nothing else. Never push other branches,
  unrelated local commits, or an unapproved dirty tree."* This is the entire
  safety boundary of the protocol, and it is absent
- **branch deletion at merge time**
- **the whole `session-gc` section**, including the golden principle about never
  triggering mutations from a fuzzy session-end event
- **the whole loose-end/backlog section**

So which file governs depends on which agent is reading. `AGENTS.md` is the file
**Codex** reads — and we route real work to Codex through the `delegate` skill,
with `bank-sales` running on the Codex CLI. That agent is operating under a
merge contract with no push step and no scope limit.

His domain-modeling thesis lists "competing semantic owners for one concept" as a
top warning sign, and his authority thesis insists a grant name its exact scope.
We have both defects at once, in the one document where it matters most.

The fix is not to sync them. It is to give the protocol **one** owner and make
the other a pointer.

### Gap 2 — The harness isn't versioned, so it can't be measured or rolled back

`~/.claude/.git` tracks **9 files**: `.gitignore`, `CLAUDE.md`, and 7 commands.
The `.gitignore` denies `/*` by default. That means **13 skills, `settings.json`,
agents, and hooks are all untracked**. Two commits total; last one 2026-07-18.

His effectiveness thesis says: *"Record the harness revision and compare the
dashboard across like jobs."* We have no revision to record. Concretely: if a
skill edit made things worse three weeks ago, there is no diff, no bisect, and no
revert.

Note one reason this hasn't been done: `settings.json` carries a live plugin
access token in the marketplace URL, which is exactly why `.gitignore` denies it.
That's a solvable problem (track a redacted template), not a reason to leave the
whole harness untracked.

This is the precondition for everything else on this list.

### Gap 3 — Everything accretes; nothing retires

loom's pipeline is `distill → route → weave → promote`. There is **no garbage
collection pass**. Grepping `loom/*.py` and all three prompts for
retire/obsolete/prune/supersede returns nothing but a `STALE_DAYS = 7` counter
for un-promoted commits.

Meanwhile the carrying cost is real and growing:

- **100** memory files; `MEMORY.md` is **99 lines / 1,786 words** and is loaded
  into the context of **every single session** — a fixed tax on every run
- **203** wiki articles
- **13** personal skills — of which, across **3,805 July transcripts**:

| Skill | Invocations |
|---|---|
| venice-ai, adjust-site | 6 each |
| new-site | 4 |
| grilling | 3 |
| ship-site, push-to-device | 2 each |
| skill-creator, nano-banana | 1 each |
| **topical-map-dfseo2, tidy, rollback-site, preview-site** | **0** |

`~/.claude/skills/dist/` isn't even a skill — it's a leftover `.skill` zip from an
`export-skill` run.

The `preview-site` result is the sharpest signal: `adjust-site` (6) → `preview-site`
(**0**) → `ship-site` (2). The middle link of the site-flow chain is never used,
and we already know it has an unfixed `origin/HEAD` bug. The chain is broken in
practice and the harness has no mechanism that would ever tell us.

Corroborating signals, all pointing the same way:

- `backlog/archive.yaml` is `items: []` — **nothing has ever been archived**
- 2 memory files exist that `MEMORY.md` doesn't index at all
  (`bebop-telegram-chat-id.md`, `rex-travel-contact-barbara.md`) — and the first
  duplicates `rex-telegram-chat-id.md`
- the `code-review` plugin is installed but **disabled** in `settings.json`, while
  `CLAUDE.md` still routes to `/code-review`

His rule — *"retain, revise, or **remove**"*, and *"remove context or tooling
whose effect does not survive ablation"* — has no counterpart in our loop.

### Gap 4 — No invariant has a verifier

The memory schema declares `type: user | feedback | project | reference`. Actual
distribution across the 100 files:

```
 31  project      18  procedure  ←  not in schema
 15  feedback     14  fact       ←  not in schema
  9  reference     3  preference ←  not in schema
  8  user          1  decision   ←  not in schema
```

**36 of 100 files use a type that doesn't exist in the schema.** Nothing checks.

This is a small instance of his general rule: *"If it matters, it belongs in a
verifier owned by the repo."* We write rules as prose and then rely on the next
agent having read the prose.

### Gap 5 — Proof is assertion, not claim-matched evidence

Our merge-recommendation format has a `Verified:` field. It's free text.

His proof table maps each *class of claim* to the evidence that actually closes
it — browser behavior needs a real browser journey; deployment needs the
validated artifact running plus post-deploy health; a consequential remote change
needs a staged canary, cutover, recovery, and post-cutover checks.

We've already learned one of these by getting burned: `npm run preview` on real
workerd plus a curl of the assets before push, because `astro dev` masks a wrong
`assets.directory` in `wrangler.jsonc`. That is a textbook claim→evidence pair —
and it lives in a memory file, where it will be applied if and only if the agent
happens to recall it, rather than in a check that runs.

### Gap 6 — Our highest-leverage repos don't teach

`build-ai-automation-workflow` — which contains council, loom, bebop, watchdog,
and session-gc — has **no `CLAUDE.md` or `AGENTS.md`**. Neither does
`splash_poller`, which runs a live production ingestion pipeline on a 5-minute
cron. The two repos where an agent most needs local context have none, while
`romance-empire` has 424 lines of it.

### Gap 7 — Every key we own enters the agent's readable environment

`CLAUDE.md` instructs `set -a; . ~/.env; set +a` before any council call. `~/.env`
holds **14 keys** — Venice API + admin, Cloudflare account + API token, DataForSEO
credentials, and five Bento site UUIDs plus a shared secret key. A task that needs
only `VENICE_API_KEY` gets all fourteen.

His authority thesis is explicit here: keep credential custody outside the
trajectory, resolve narrowly-scoped keys at the action boundary, and *"secrets the
worker does not need should not enter its readable environment."* The clean fix is
for `council` to read its own key itself rather than requiring the session to
export the whole file — a small change to one tool that removes thirteen
unnecessary exposures.

---

## 4. Where we're genuinely ahead

Worth stating plainly, because the gap list above is one-directional and the
overall picture isn't.

**loom is more operationally mature than anything in his corpus.** His nightly
feedback-distillation is a *proposal* described in a conference talk. Ours is
running, with: idempotency via commit trailers and file markers, ledger rebuild
from git if state is lost, two shape lints plus a secrets sentinel on every
route, bundle bisection so one bad item doesn't reject the batch, transactional
promote (preflight → backup → atomic swap → merge, rollback on failure), bounded
per-run caps with a global deadline, and explicit no-silent-drop accounting
(`deferred` retries vs `rejected` surfaced until requeued).

**The merge protocol is a sharper authority contract than anything he's written
down.** Scoped to exactly the approved block; re-frame and wait if scope changes
after approval; push verified by `git status -sb` showing no `[ahead N]`; branch
deleted at the one moment merged-ness is known by construction.

**The council gate is broadly deployed.** `venice-review.yml` is present in
**all 17** repos that have CI — not a pilot, an actual standard. His feedback
thesis spends real effort on reviewer convergence; ours already has chair
arbitration, risk tiers, and an advisory mode.

**We're running his "last-mile deployment into the private process-data iceberg"
against a person rather than an organization** — a wiki of people, places,
companies, and decisions feeding an assistant that reads email and calendar. His
corpus has no equivalent of the *personal-assistant* case.

*Correction to an earlier draft of this assessment:* he does have a personal
**infrastructure** case, and it's the closest structural analogue to us in the
whole repo — see §6.

---

## 5. What to take from it — ordered

The single most valuable importable artifact is **`playbooks/improve-harness.md`**.
It is precisely the loop we don't have:

> baseline → earliest failed handoff → smallest owning intervention → native
> verification → **fresh rerun** → retain, revise, or remove

The fresh-rerun step is the part we'd instinctively skip and the part that does
all the work. His warning: *"A successful outcome supplies no evidence about an
instruction or tool the trajectory never used."* That sentence alone explains the
`preview-site` finding above.

**0. Collapse the two authority documents into one owner.** Highest consequence,
lowest effort. Make `~/.claude/CLAUDE.md` canonical and reduce
`~/projects/AGENTS.md` to a pointer at it — or generate one from the other. Right
now Codex is merging under a contract with no push step and no scope limit.
*~15 minutes.*

**1. Version the whole harness.** Extend `~/.claude/.gitignore` to track
`skills/`, `commands/` (already), and a secrets-stripped `settings.template.json`
— the live `settings.json` stays ignored because of the plugin token. Every
harness change then has a revision, a diff, and a revert. Precondition for
everything below. *~30 minutes.*

**2. Give loom a garbage-collection pass.** A fourth prompt beside
distill/route/weave that proposes **retirements**: memories contradicted by later
ones, wiki articles describing behavior that no longer exists, skills with zero
invocations. Emit candidates to the same shadow branch for the same one-diff
review. This is the missing half of the feedback loop and it directly attacks the
`MEMORY.md` tax.

**3. Give each of the 8 cron loops a versioned contract.** Copy the homelab
pattern in §6: a checked-in file per loop declaring scope, default mode
(report-only / change-producing / deploy-capable), authoritative sources,
required validation, approval and abort conditions, and summary schema. The cron
entry shrinks to a pointer. This is where watchdog's "never touches prod" and the
backlog's 3am-runner safety contract should actually live.

**4. Route lessons by maturity, not just by location.** loom currently routes a
lesson to a *place* (wiki / memory / skill) — so every lesson lands as prose. His
feedback thesis routes by how settled the lesson is: prompt tweak → runbook or
skill → reviewer/eval → type, API, or tool → **lint, test, or policy check** →
architecture migration. Some of what we're writing as prose should be checks.
`swimtrack-no-explicit-any-sweep` sitting in the backlog is already an instance of
the bottom row — enable the rule, repair the population, leave a ratchet — we just
didn't have a name for why it was the right shape.

**5. Write the memory-schema verifier.** ~20 lines, run in loom's promote
preflight. The smallest possible instance of "if it matters, it belongs in a
verifier," and it fixes Gap 3 today.

**6. Run `playbooks/repository-review.md` against `build-ai-automation-workflow`.**
Our highest-leverage, least-taught repo, and the one whose failures cascade into
every other loop.

**7. Add claim-matched proof to the merge recommendation.** Replace free-text
`Verified:` with claim → evidence, using his table as the menu.

**8. Add MLD as a sensor.** loom distills what the agent *did*; it never captures
what the agent *noticed* — where it was blocked, what it guessed at, what tool it
wished existed. His MLD framework (Mistakes, Learnings, Desires) is a short
end-of-session self-report treated strictly as telemetry for the harness builder,
corroborated against the trajectory before anything is promoted. We have the
distillation machinery already; this is one more input to it.

---

## 6. The one document to read first

`docs/domain-modeling/homelab.md` (3,484 words) is **his personal homelab repo**,
and it is a far closer match to our VPS than anything else in the corpus:
declarative infra, cron-scheduled automation roles, generated monitoring config,
a Tailscale-reachable device, and one operation — upgrading the remote-access
daemon on a robot vacuum — that can destroy the very path needed to recover it.

Three mechanisms from it are worth taking almost verbatim:

**Versioned automation contracts.** This is the highest-value single idea for us.
His scheduled prompts stay *slim* — they name a role and point at a **versioned
contract checked into the repo**, which declares: owned scope and exclusions;
**default mode** (report-only / change-producing / build-only / deploy-capable);
authoritative sources to inspect; required validation and evidence; commit and PR
behavior; human approval and abort conditions; and the summary schema.

We run **8 cron loops** — bebop, watchdog, loom, session-gc ×2, diem, splash_poller
×3, agents, security sweep — and **not one has a contract like that.** Their
authority lives implicitly in shell scripts and prompt text. watchdog "never
touches prod" is a sentence in a README, not a declared mode. This would give
every loop one reviewable, versioned place that says what it may do.

**Report-only by default.** His documentation-freshness role compares operator
docs against actual config and monitoring, names each stale page plus the
contradicting source and a concrete correction — and **cannot edit, branch, or
open a PR** without an explicit follow-up. That is exactly the loom
garbage-collection pass recommended above, already designed. We can copy the
shape rather than invent it.

**Risk as a state machine.** The vacuum upgrade is split into *separate commands* —
build, canary-deploy, canary-authenticate, canary-access-check, production-backup,
promote, production-verify, canary-cleanup — not flags on one permissive deploy.
Compare our `ship-site` / `rollback-site` pair, which is the same instinct at
lower resolution. Also directly stealable: remote shell scripts must put their
body in a `main` function invoked once at the very end, so a **truncated transfer
leaves only unexecuted definitions** instead of a half-run operation. We push
scripts over SSH across the tailnet; we have no such rule.

---

## 7. The eval layer — where the "vibes vs. evals" thread lands

A separate session worked the "token merchants promote skills/subagents/loops with
no evals" critique and designed a six-component eval layer (success definition,
golden set, grader, trigger, baseline+A/B, ledger). Its open question was *which
mechanism to start with*. This audit answers it, and adds two corrections.

### Start with council — it already has a labeled golden set

council is the right first mechanism on every axis: highest stakes (it gates
merges in **all 17** CI repos), frequently run, and — critically — its output
carries a **deterministic assertion**: `blocking: true | false`. That means the
MVP grader is programmatic, not an LLM judge, which sidesteps the "validate your
grader" problem entirely.

And the fixtures already exist. They are currently **misfiled as memory prose**:

| Case | Council finding | Ground truth | Verified against |
|---|---|---|---|
| aris PR #1 | missing `width`/`height` | false | `dist/client/work/index.html` shows `width="1536" height="1024"` |
| aris PR #1 | no WebP | false | `_astro/` — 2.9 MB PNG → 52–276 kB WebP |
| swimtrack-website PR #11 | ROOT not declared | false | declared at line 12 |
| swimtrack-website PR #11 | engines not pinned | false | already pinned |
| swimtrack-website PR #11 | `.env` not gitignored | false | already gitignored |

Five labeled cases, all with the correct answer *"should not block,"* each with a
recorded reason. Both PRs are merged and their diffs retrievable via `gh pr diff`.
PR #11 was blocked **4×** by these.

### The sharpest instance of the critique is our own council upgrade

council v0.4.0 — chair arbitration plus full-file-context grounding — was designed
**specifically to kill this failure class**. Nobody ever re-ran the five cases that
motivated it. We believe the fix works because the design sounds right.

That is precisely the tweet's complaint, inside our own stack, on our highest-stakes
mechanism. It is also ~30 minutes of work to close, because the golden set is
already written down.

(Related: `monthly-bidding` is still pinned to `council-v0.2.0`, the version with the
known behavior. A fix that shipped without verifying propagation.)

### Correction 1 — n=1 buys regression detection, not effect estimation

The thread's MVP ("record one baseline, run it before the next change") and Ryan's
`evals/` are **not the same standard**, and the difference matters.

His method explicitly lists *"one stochastic rollout treated as representative"* as
an **invalid result**. Valid comparison demands predeclared hypotheses, matched
worlds, randomized run order, condition-blind grading, escrowed seeds, and separate
recording of whether the intervention was *available*, *retrieved*, *invoked*, and
*relevant*.

That doesn't make the MVP wrong — it makes its claim narrower than the tweet's:

- **n=1 + deterministic grader → regression detection.** "Did this change break the
  five cases we know the answer to?" Valid, cheap, worth doing today.
- **Ryan's apparatus → effect estimation.** "Does chair arbitration actually beat
  the simpler design?" Expensive, and the only thing that answers the tweet.

Buying the first and reporting the second is exactly "vibes with a number stapled
on." Name which one each eval buys.

### Correction 2 — the thread's six components are missing a seventh: retirement

Ryan's rule: *remove context or tooling whose effect does not survive ablation.* An
eval layer with no retirement trigger becomes another accreting mechanism — Gap 3
again, with a dashboard. The layer has to be able to delete things.

And the cheapest eval we own is **already free and unread**. One grep over 3,805
July transcripts gave "invoked" counts for all 13 skills and found 5 at zero — no
fixtures, no grader, no golden set, no token bill. That is arguably the real MVP:
it has already produced removal candidates.

The blind spot underneath it: we have **no retrieval instrumentation for context at
all.** "Invoked" is measurable for skills and tools; for the 100 memory files and
203 wiki articles it is not. That is what makes the `MEMORY.md` tax impossible to
either justify or trim.

### Where the two threads converge

The thread's component #1 (*write what "good" means as checkable statements*) and
the homelab mechanism in §6 (*a versioned contract per automation declaring scope,
mode, required validation, and summary schema*) are **the same artifact**.
Recommendation 3 therefore does double duty: writing a contract for each of the 8
loops *is* writing the eval layer's success definitions.

---

## 8. Known limits of this assessment

- Static inspection plus usage counts from 3,805 July transcripts. No harness
  intervention was run and re-run, so nothing here is a measured effect — by his
  own eval standard these are leads, not findings.
- Skill invocation counts come from grepping `"skill":"…"` in session JSONL and
  will undercount any skill loaded by other means.
- His repo is two commits old (root commit + "Teach the environment to remember").
  It is a fresh, actively-forming corpus, not a settled one.

---

# Addendum — 2026-07-23: LifeOS as a third candidate, and the rebuild-vs-continue decision

**Provenance.** LifeOS was researched independently (repo contents, release
history, star/fork/commit signals). The "continue vs. start over" recommendation
below was then handed to a cross-provider adversarial review (Codex / Sol, xhigh)
with an explicit brief to *refute* it. The review did not rubber-stamp — it
materially changed the verdict, and the change is recorded here rather than
quietly absorbed. The §5 "harvest patterns only" instinct survives for
harness-engineering; it does **not** survive for LifeOS.

## 9. The third candidate — Daniel Miessler's LifeOS

The question that prompted this addendum: *is there a stronger foundation to start
over on, instead of continuing to build on what we've pieced together?* Two
candidates were on the table — Lopopolo's harness-engineering (§1) and Miessler's
**LifeOS** (`danielmiessler/LifeOS`).

Unlike harness-engineering, **LifeOS is real, running software.** Verified from the
repo:

| Signal | Value |
|---|---|
| Stars / forks / open issues | 16,873 / 2,288 / 1 |
| Last push | 2026-07-22 (active) |
| Release cadence | 5 releases in 11 days; v7.0.0 "Bitter Pill" (breaking) → v7.1.1 |
| Substance | executable TypeScript under Bun — `InstallEngine.ts` (26.6 KB), `DeployComponents.ts` (20.9 KB), hook/skill/settings installers |
| Governance | 10 contributors, but `danielmiessler` = 647 commits vs. next at 4 — single-maintainer BDFL |

**But architecturally it is an overlay, by its own declaration.**
`LifeOS/SKILL.md`: *"the whole point is 'bolt on, don't take over.'"* It covers
exactly one slice — the **personal-state / control plane**:

- **`USER/`** — a canonical identity tree (goals, people, preferences, projects),
  symlinked in, separating personal data from repo code.
- **TELOS** — a "current state → ideal state" intake interview that populates it.
- **Pulse** — an operator-facing dashboard.
- **Consent-gated hook install** — shows the exact `settings.json` diff, backs up,
  waits for an explicit yes.

It has **no** council, no loom, no cron-ops, no multi-repo CI, no MCP integration.
Its Telegram-notification capability is still on the *roadmap* — i.e., behind Bebop
today. On the axis of "run your life's infrastructure," it covers ~20% of our
components and none of our differentiator.

**The trap in that last sentence** — and the thing the adversarial review caught —
is that "~20% of components" is a component-count argument masquerading as an
architecture argument. It may be 20% of the parts but plausibly **80% of daily
operator interaction**, which is where "clunky and unpolished" is actually *felt*.

## 10. The reframe — one question was actually two

The original framing ("rebuild or continue?") is mis-posed, because the system is
two planes with different answers.

**The operational plane** — council (17 repos), loom, 8 cron loops, MCP wiring, CI
— **continue; do not rebuild.** Settled on both sides of the review. These
subsystems encode hard-won operational semantics a greenfield rewrite would only
rediscover through incidents (session-gc's "never mutate on a fuzzy session-end
event" was paid for with a deleted live workspace). harness-engineering is at most a
*requirements spec* for such a rebuild; it is not runnable, so "start over on it"
stays a category error (§1). LifeOS has no equivalent to any of these; wholesale
replacement is defeated.

**The personal-state plane** — 100 memory files, 203 wiki articles, identity, the
operator surface — **is genuinely contestable, and the honest answer is: measure,
don't decide by taste.** The §5 "harvest the pattern only" verdict was too
conservative here. Three concessions force the upgrade:

1. The clunkiness we feel most is plausibly the personal/operator surface — exactly
   LifeOS's domain.
2. Our consolidation plan (§5, and the eval-layer/consolidation slate) specifies
   *operations* — retire, instrument, extract — but never names a **target
   information architecture**. You cannot retire intelligently without deciding
   where surviving information belongs. LifeOS ships an opinionated target (the
   `USER/` schema) plus migration UX today.
3. A greenfield personal layer supplies a **forcing function** in-place cleanup
   lacks: every legacy artifact must justify crossing an import boundary or it
   doesn't come along. In-place consolidation has no such boundary and drifts toward
   indefinite renovation.

So the personal-plane decision is not "adopt LifeOS" and not "harvest only." It is:
**run a bounded, reversible bake-off** (§12) between "consolidate in place" and
"pinned-LifeOS `USER/` behind an adapter," and let the numbers pick. This is the
eval discipline the §7 thread already argued for — applied to the one decision where
taste was about to substitute for measurement.

**Shape of the winning migration, if LifeOS wins:** a *strangler* — keep the engine,
replace the cockpit. council, cron execution, MCP, CI, and loom's transactional
machinery stay. The personal memory/control layer is replaced behind an adapter.
loom's outputs then route *by semantics*, not by place: personal canonical state →
`USER/`; long-form reference → wiki; settled procedures → versioned repo contracts;
enforceable lessons → tests / lints / policy checks; transient observations →
TTL-backed event history.

## 11. Two corrections that hold regardless of the bake-off outcome

**Correction A — separate two kinds of "retire"; one of them is sequenced too
early.** The consolidation slate treats retirement as a single step that "falls out
of" the contract exercise. Two distinct retirements hide in that word:

- **Loop retirement** — a cron loop that can't state a measurable success condition
  is a retirement candidate. Contract-driven, needs no usage telemetry, can stay
  early.
- **Context retirement** — deleting memory files, wiki articles, zero-invocation
  skills. This is sequenced *before* instrumentation, and that's the bug. §8 of this
  assessment admits invocation counts *undercount* access paths and that nothing was
  rerun; the per-file retrieval grep across the transcript corpus doesn't exist yet.
  Deleting context against blind telemetry is how you remove the obscure-but-critical
  thing.

Reorder so context retirement sits *after* retrieval instrumentation:

> **version/snapshot → contracts (+ loop-retirement) → retrieval instrumentation →
> context retirement → extract shared spine**

Retirement of context is a *consequence* of measurement, not an opening move. This
does not weaken the "contracts first" keystone — a contract *is* a success
definition, i.e. the front half of "instrument."

**Correction B — if LifeOS is adopted, wall it off.** It is a fast-churning
(breaking releases days apart), single-maintainer, Bun/TypeScript, symlink-dependent
upstream. Pin a specific version behind our *own* compatibility contract; never
couple live subsystems directly to it. "16k stars, shipped today" is momentum, not
stability. Its installer writing into a live `settings.json` (8 crons + custom hooks
already wired) is only safe because it is consent-gated and can be exercised against
a fresh isolated profile first — which is exactly how the bake-off must run it. (The
earlier "installer blast-radius" objection was overstated for the same reason: a
consent-gated installer against a throwaway profile is inspectable as a patch.)

## 12. The bake-off — a cold-runnable experiment

Decide the personal-state plane on measured outcomes, not taste. Reversible by
construction: isolated profile, no writes to the live harness until a decision.

**Preconditions (both arms):**

- **Snapshot/version the harness first.** This is Gap 2 / recommendation 1 and a
  hard precondition — without it there is no baseline to compare against or revert
  to.
- **Land the retrieval instrumentation.** Grep the ~3,805-session transcript corpus
  for `Read` calls on `*/memory/*.md` and `~/wiki/**` → per-file retrieval counts.
  This is the baseline for two metrics below and the fix for Correction A.

**Two arms, same inputs:**

- **Arm A — consolidate in place.** Curate the existing memory/wiki into a defined
  target schema; no new dependency.
- **Arm B — pinned-LifeOS `USER/` behind an adapter.** Install a pinned LifeOS
  version into an isolated Claude Code profile (never over the live `settings.json`).
  Populate `USER/` from *canonical personal state only* — not all 203 wiki articles.
  Keep existing memory/wiki behind read-only compatibility adapters. Make Bebop the
  first and only consumer for the trial.

**Metrics (predeclared — so this buys more than one stochastic rollout; cf. §7
Correction 1):**

| Metric | How measured | Direction |
|---|---|---|
| Briefing quality | Blind A/B rating of N matched morning/evening briefings | higher |
| Context load ("MEMORY.md tax") | Tokens of personal-state context loaded per session | lower |
| Duplicate / conflict count | Contradictory or redundant personal facts surfaced | lower |
| Update friction | Steps / time to record one new durable personal fact | lower |
| Operator intervention | Manual corrections per week | lower |

**Decision rule:** adopt Arm B only if it beats Arm A on briefing quality *and*
context load without regressing the others. Otherwise consolidate in place and
harvest only the `USER/`-separation *pattern*. Either way, the losing arm's profile
is deleted — the experiment retires itself, honoring the §7 seventh component.

**Out of scope:** council, loom internals, cron-ops, CI, MCP, and the swim / finance
/ SDVOSB projects. The bake-off touches the personal-state plane only.

## 13. Net answer to "should I start over?"

No — with one precise exception. The operational plane is not a rebuild candidate on
any reading; it is the differentiated core and the thing neither alternative
possesses. harness-engineering is a quarry for ideas, not a foundation. LifeOS is
real and, on the personal-state plane alone, a serious enough option that the
choice should be *settled by the bake-off in §12 rather than by instinct.* The
"clunky" feeling is real, but the fix is 90% gardening on the foundation we have
(contracts → instrument → retire → spine) and at most 10% a reversible, measured
swap of the cockpit. Clunky-but-ours-and-running beats polished-but-covers-20%; the
only honest way to know if that inequality flips for the personal layer is to
measure it.
