# Old-model-era overconstraint audit — site-flow skills, grilling, push-to-device, bebop pipelines

Audit date: 2026-07-25. Framework: Anthropic "new rules of context engineering" for Claude-5-gen
models — coercive hard rules, ALL-CAPS mandates, long never-lists, worked output examples, and
repeated instructions now hurt; give intent + interfaces + judgment room. Operational/safety
contracts (exact command sequences, incident-derived rules, merge/rollback gates, FAILED-signal
rules) stay rigid and are NOT flagged.

Files audited (read-only):
- /home/dev/.claude/skills/adjust-site/SKILL.md
- /home/dev/.claude/skills/preview-site/SKILL.md (+ references/preview-url.md, references/review-checklist.md)
- /home/dev/.claude/skills/ship-site/SKILL.md (+ references/pr-and-merge.md)
- /home/dev/.claude/skills/rollback-site/SKILL.md
- /home/dev/.claude/skills/new-site/SKILL.md (+ references/interview-guide.md, concept-brainstorm.md, astro-setup.md, subagent-brief.md, comparison-dashboard.md)
- /home/dev/.claude/skills/grilling/SKILL.md
- /home/dev/.claude/skills/push-to-device/SKILL.md
- /home/dev/.claude/projects/-home-dev/memory/bebop-morning-briefing-pipeline.md
- /home/dev/.claude/projects/-home-dev/memory/bebop-evening-wrapup-pipeline.md

Cross-cutting note (not counted as per-file findings): the incident-derived line "Workers
static-assets sites have no Pages project / never run `wrangler pages ...`" appears in 7 places
across the suite. Cross-FILE repetition is justified — each skill loads standalone. Only
within-one-load-unit repetition is flagged below.

---

## 1. adjust-site/SKILL.md — verdict: clean (1 minor finding)

The branch-decision matrix, adapter-detection table, preflight commands, and the "never edit
main directly" rule are all operational contracts — KEEP. The "Never in this phase" section is a
phase-boundary contract tied to the global merge protocol — KEEP.

**Finding 1.1 — SOFTEN (minor)**
> "If no description was given, ask what the change is — one plain-text line, nothing more."
Reason: "nothing more" micro-prescribes the asking format; the intent is just "don't turn it
into an interview."
Replacement: "If no description was given, ask briefly what the change is."

---

## 2. preview-site/SKILL.md (+ references) — verdict: minor (2 findings)

Commit/push mechanics, "never force-push", "Do not guess an adapter", "Never merge from here",
and the failure notes are operational — KEEP. review-checklist.md is a clean 6-line interface —
KEEP as-is. preview-url.md is per-adapter operational procedure with real gotchas (build before
`versions upload`, branch-alias slug rules) — KEEP.

**Finding 2.1 — SOFTEN**
> "### 5. Get the preview URL — via the adapter
> Follow `~/.claude/skills/preview-site/references/preview-url.md`. Summary:
> - **workers** — reuse the branch's open PR or create one ... If no comment appears, fall back to `npx wrangler versions upload` ..."
Reason: the three-bullet "Summary" re-explains most of the reference — duplicated instructions
in the same skill; the reference is the source of truth and the summary can drift from it (it
already omits the "build first or you ship a stale preview" caveat the reference carries).
Replacement: "Follow references/preview-url.md for the adapter's exact steps (workers → PR check
+ bot comment; netlify → deterministic deploy-preview URL; pages → deployment list). Each has a
manual dashboard fallback."

**Finding 2.2 — SOFTEN (borderline KEEP)**
> "Workers static-assets sites have **no Pages project**: `wrangler pages ...` returns nothing and `*.pages.dev` aliases do not exist. Never run Pages commands unless the adapter is `pages`."
Reason: stated in SKILL.md step 4 AND again in preview-url.md — duplicate within one skill. The
rule itself is incident-derived and stays; keep it once (in the SKILL, since the reference isn't
always loaded) and drop the restatement in preview-url.md's workers section down to a clause.
Replacement (in preview-url.md): fold into the intro sentence — "There is no Pages project for
these sites (see SKILL.md step 4)."

---

## 3. ship-site/SKILL.md (+ references/pr-and-merge.md) — verdict: clean (1 borderline finding)

This skill is almost entirely merge-protocol safety contract: check-watch procedure, retry
window, fail-stop, approval-void-on-new-commits, exact merge commands per approved method. All
KEEP. The repeated "never merge on a weaker or implied signal" mirrors the global protocol —
acceptable rigidity for the one irreversible step.

**Finding 3.1 — SOFTEN (dedupe only; content stays rigid)**
> The Merge recommendation block template appears in full in three places: ~/.claude/CLAUDE.md,
> ship-site/SKILL.md step 4, and pr-and-merge.md section 3.
Reason: triple copies of the same exact-format block will drift (they already differ slightly —
SKILL.md's copy hardcodes "Risk: <blast radius — production redeploys on merge>" and drops the
Push: line that CLAUDE.md's canonical block includes). Safety content stays rigid — the fix is
one canonical copy, not softer wording.
Replacement: keep the block only in CLAUDE.md (canonical) + pr-and-merge.md (the executable
reference); SKILL.md step 4 says "Post the block exactly as specified in the global merge
protocol (`~/.claude/CLAUDE.md`), then stop."

---

## 4. rollback-site/SKILL.md — verdict: clean (0 findings)

Break-glass procedure: single push gate with explicit-yes wording, revert-only rule, "Never
`git reset --hard` + force-push on the live branch", instant-host-rollback fallbacks, adapter
verification. Every hard rule here is an operational/safety contract for the one flow that
pushes directly to live. Nothing to cut. The one ALL-CAPS-free, reason-attached style ("Speed
matters: the site is broken now") is exactly the intent-plus-contract shape the new guidance
wants.

---

## 5. new-site/SKILL.md — verdict: minor overconstraint (3 findings)

The hosting/adapter section (no `@astrojs/cloudflare`, SESSION KV rationale, no-Pages warning),
state machine, phase gates, and the parallel-spawn/namespace rules are operational — KEEP. The
"Questions: batch them" stance is genuine operator preference — KEEP (but see 6.1: it conflicts
with the reference it loads).

**Finding 5.1 — CUT**
> Phase 3 step 2: the full 24-line worked `index.astro` code block ("```astro ... <Layout title=\"Homepage Concepts\"> ... {directions.map(d => ( <a href=... style=\"display: block; padding: 1.5rem; ...\" ...```")
Reason: worked output example of a trivial throwaway navigator page, down to inline padding
values — a Claude-5 model writes this from one sentence, and the pinned styles anchor it.
Replacement: "Overwrite `src/pages/index.astro` with a simple concept navigator using the shared
Layout: one card per direction (number, name, one-line description from directions.json) linking
to `/direction-N-slug`."

**Finding 5.2 — CUT**
> Phase 4 step 2: "### 2. Close with this shape" + the 8-line fenced closing-prose template ("Prototypes ready. / Comparison dashboard: <artifact URL> ... reply with a pick — or a mix (\"hero from 1, palette from 3\") ...")
Reason: worked example of output prose — the model should compose the hand-off; only the
required contents are load-bearing.
Replacement: "Close by reporting: the dashboard artifact URL, that `npm run dev` serves each
direction at `/direction-N-slug`, and the ask — pick one direction or a mix; next step is the
final homepage plus a locked design system."

**Finding 5.3 — CUT (dedupe)**
> "state it, don't ask" / "state them, don't ask" — appears at the top ("Generate names, slugs, and commit messages yourself and state them — don't ask.") and again in Phase 2 step 3 ("— state it, don't ask") and Phase 3 step 3 ("— state it."); the "Never in this skill" bullets also restate the Phase 2 wrangler-pages rule and the Phase 3 namespace rule.
Reason: repeated instructions inside one file; the top-level stance already covers all cases.
Replacement: keep the single top-level sentence; delete the per-step restatements. In "Never in
this skill", keep only the deploy/merge boundary (operational) and drop the two bullets that
restate body content.

---

## 6. new-site/references/interview-guide.md — verdict: significant overconstraint (3 findings)

The "When You Have Enough" checklist, quality bars ("Target customer must be specific enough
that a real person matches it"), redesign extraction procedure, and the brief.md output format
are interface/contract — KEEP. The rest is old-era interview scripting, half of it explicitly
overridden by SKILL.md ("Override its pacing").

**Finding 6.1 — CUT (contradiction, highest priority in this file)**
> "**Tool rule:** Every question you ask must use the `AskUserQuestion` tool. Never ask questions as plain text."
Reason: directly contradicts SKILL.md Phase 1 ("compress the whole interview into ONE batched,
grouped message ... never AskUserQuestion for anything with a sensible default"). A dead
conflicting mandate is worse than overconstraint — the model burns judgment reconciling them.
Replacement: delete the line (SKILL.md owns pacing and tooling).

**Finding 6.2 — CUT**
> The "**Conversation style:**" bullet block ("Conversational and adaptive — ask follow-ups based on what they say ... Batch related questions together (2-3 max) ... If they're stuck: 'Write something honest for now, we can sharpen it later'")
Reason: pacing/style scripting that SKILL.md explicitly overrides; the one durable bit
("push back on vague answers — 'everyone' is not a customer") already lives in SKILL.md.
Replacement: delete the block; open the file with "Coverage checklist and brief format for the
interview. Pacing and tooling are set by SKILL.md."

**Finding 6.3 — SOFTEN**
> The scripted example questions throughout, e.g. "*\"What does your business do — can you give me one sentence?\"*", "*\"What feeling should your site create in the first 3 seconds — before anyone reads a word?\"*", "*\"Who are you NOT? Sometimes easier to define by contrast. ...\"*"
Reason: worked examples of interview prose across six categories — a capable model phrases its
own questions; what's load-bearing is *what must be learned* and the quality bar per category.
Replacement: keep each category's "**What you need:**" line + quality bar + the "Critical rule:
Build around what they HAVE" line; drop the example-question scripts.

---

## 7. new-site/references/concept-brainstorm.md — verdict: significant overconstraint (5 findings)

KEEP: the locked-vs-free split, the 7-token palette and font-pairing interface (fontsource
package existence is a real failure mode), the contrast-axes table (it feeds the
`contrast_axes` JSON field and the "differ on 4-5 axes" distinctness bar), and the
directions.json schema.

**Finding 7.1 — CUT**
> The three full worked example directions (~55 lines): "## Direction 1: The Silent Gallery / Palette: Walnut #8B6F47 · Charcoal #3D3D3D ... ## Direction 2: The Atelier ... ## Direction 3: The Naturalist ..."
Reason: the largest worked-output example in the suite; anchors every future brainstorm toward
warm-editorial-eco-interior aesthetics (walnut/parchment/serif) regardless of the actual brand.
Replacement: keep the empty Direction Format skeleton only; if an anchor is truly wanted, one
2-line sketch, not three fully-produced directions.

**Finding 7.2 — CUT**
> "## Pitfalls to Avoid" — "\"Direction 1 is minimal, Direction 2 is also minimal but with a different font\" — no ... \"Bold and modern\" — this describes 90% of AI-generated websites ... A dark, brooding direction for a children's brand — no ..."
Reason: red-flag/rationalization list; every item restates the top rule ("genuinely different
choices across multiple contrast axes") or the palette quality rules already present.
Replacement: delete the section; the top rule + "Quality rules" bullets carry the intent.

**Finding 7.3 — CUT**
> "**Process (internal — don't show this to the user):** 1. Consider 2-3 palette options for each direction 2. Pick the one that most naturally fits ... 3. Do the same for font pairings 4. Respect any constraints ..."
Reason: scripted internal chain-of-thought — old-model reasoning scaffold; step 4 is the only
contract and it's already stated under "Locked vs Free".
Replacement: delete; keep "You are the designer — pick what best embodies each concept, within
the brief's constraints."

**Finding 7.4 — SOFTEN**
> "## Direction Naming Convention" + "Good: The Silent Gallery, Warm Authority, The Atelier, Field Notes, Studio Light, Bold Craft / Bad: Minimalist, Modern, Clean Professional, Option 1, Dark Theme"
Reason: good/bad example lists for a one-line intent.
Replacement: "Names: evocative, 2-4 words, reflecting the design metaphor — never generic labels
like 'Minimal' or 'Option 1'."

**Finding 7.5 — SOFTEN (minor)**
> directions.json format example contains two complete direction entries.
Reason: the schema is the contract; one entry defines it — the second only re-embeds Silent
Gallery/Atelier content (compounds 7.1's anchoring).
Replacement: trim to one entry.

---

## 8. new-site/references/astro-setup.md — verdict: clean (0 findings)

Every hard rule here is attached to a verified failure mode: the SESSION KV deploy-breaker, the
"Cannot use assets with a binding" error, the 0-byte-404 `not_found_handling` behavior, the
font-import-before-Tailwind CSS ordering, the `@reference` gotcha, the temp-dir scaffold reason.
This is the model file for what the new guidance calls an operational contract — the emphatic
tone ("Each omission is load-bearing", "don't 'helpfully' add one back") is earned by incident
evidence quoted inline. Do not touch.

---

## 9. new-site/references/subagent-brief.md — verdict: significant overconstraint (5 findings; the worst file in the suite)

KEEP (operational): the namespace containment rule ("Sub-agents must ONLY write to their own
namespaced paths" — parallel-agent collision contract), the output-file paths, the pre-populated
`<style is:global>` theme-override block (exact CSS-var interface), the Layout import path, the
`@reference` gotcha, the styling-token interface, the no-lorem-ipsum content rule, the redesign
image-manifest check, and the spawning instructions (parallel, single message, opus, palette
pre-filled). The Required Sections list is a completeness interface with ordering freedom — KEEP.

**Finding 9.1 — CUT**
> "Build a homepage prototype ... that a client would pay $30,000 for." / "You are a senior designer at a premium agency. ... match the craft level to the price tag ($100K custom build)" / "make them say \"holy shit, that's MY site.\"" / "This prototype must make the user lean forward in their chair."
Reason: role-play + stakes-inflation hype prose — the canonical old-model motivational pattern;
the sub-agent already loads frontend-design, which owns the craft bar. (Also: $30K and $100K
disagree with each other.)
Replacement: "Build a finished-feeling homepage with real personality and craft — the
frontend-design skill sets the bar. Match the tone to the brand in brief.md."

**Finding 9.2 — SOFTEN (highest quality impact)**
> "But ALL brands get: - Staggered hero entrance ... - A scroll-reveal system ... - Stat counters or reveals that reward scrolling (requestAnimationFrame with eased counting, typewriter, ...) - Hover interactions with considered curves ... - A navbar that responds to scroll (shrink, blur backdrop, opacity shift) - At least one element with autonomous motion (a subtle float, rotation, or pulse ...)"
Reason: mandating the identical six motion features on every direction manufactures exactly the
templated sameness the brief warns against — every prototype ships a scroll-shrink navbar, a
floating element, and a stat counter. Enumeration-as-mandate is the core Claude-5 anti-pattern.
Replacement: "Motion should feel authored and brand-calibrated, and reward scrolling — vary the
reveal vocabulary rather than fading everything up. Which moments get motion is your call per
direction."

**Finding 9.3 — SOFTEN**
> Principles 1-6 quotas: "it appears in the nav, section transitions, card details, backgrounds, CTAs, footer — at least 5 places", "At least one element that overlaps a boundary", plus the exhaustive sub-lists under "Micro-details that signal craft" and "Layout with deliberate tension".
Reason: the six principle *headings* (motif, authored motion, deliberate tension, typography as
a tool, craft micro-details, sectional rhythm) are excellent compressed intent; the counted
quotas and long option menus beneath them turn design judgment into box-ticking and overlap
frontend-design.
Replacement: keep each principle as heading + 1-2 sentence intent (e.g. "A recurring visual
motif derived from the brand identity, used often enough to read as a signature"); cut the
per-principle option enumerations and numeric floors.

**Finding 9.4 — SOFTEN**
> "## What \"Done\" Means" checklist: "- [ ] A visual motif appears in at least 5 places ... - [ ] At least 3 different animation types ... - [ ] Hover effects use spring/bounce curves, not just linear transitions ... - [ ] Someone seeing this would not say \"that looks AI-generated\""
Reason: checkbox-quantified creativity; duplicates the principles above and contradicts them
("spring/bounce curves" for all — Principle 2 says refined brands get slow eases).
Replacement: keep only the verifiable operational bar: "Done = renders under `npm run dev`,
responsive at 375px (adapt, don't just stack), all required sections present, content on-brand
and in the correct language, and the direction is visibly distinct from its siblings."

**Finding 9.5 — CUT**
> "### Calibrating Tone" table ("| Playful / warm | Spring curves, bounce, float | Illustrated elements ... |") + "This table is a starting point, not a cage."
Reason: a worked personality→style mapping the model doesn't need (and the file itself
disclaims); tone calibration is one sentence plus the brief.
Replacement: "Match motion energy, decoration, and layout risk to the brand personality in
brief.md; contrast within a design is what makes it feel designed."

---

## 10. new-site/references/comparison-dashboard.md — verdict: clean (0 findings)

Capture pipeline (playwright-core vs pinned-channel MCP, scroll-to-bottom-first for
scroll-reveal pages), CSP/data-URI constraint, compression budgets, and the publish flow are
operational, mostly incident-derived. The page-structure section is prescriptive but it is a
design spec for a shipped, user-approved deliverable (the dashboard *is* the decision medium),
with reasons attached — spec, not coercion. Leave as-is.

---

## 11. grilling/SKILL.md — verdict: clean (0 findings)

Ten lines of pure intent. "Ask the questions one at a time ... Asking multiple questions at once
is bewildering" is the deliberate design of this skill (it exists to be the one-at-a-time
stress-test tool; new-site's batching rule intentionally diverges). "If a question can be
answered by exploring the codebase, explore the codebase instead" is judgment-room, not
coercion. Model file for the new style.

---

## 12. push-to-device/SKILL.md — verdict: clean (0 findings)

Exists to encode environment facts: SSH login ≠ Tailscale account name, BSD-rsync/GNU-rsync
quoting behavior, byte-count verification, KNOWN_USERS onboarding. All operational, all
explained, no coercion ("may drift" honesty included). Leave as-is.

---

## 13. bebop-morning-briefing-pipeline.md — verdict: clean (0 findings)

Protected operational contract end-to-end: exact MCP call order, epoch-not-date-string rule
(real bug class), triage caps, MarkdownV2 escaping (incident: telegram-markdown-escaping), and
the SENT/FAILED signal rule (incident: bebop-placeholder-send-failure — the FAILED rule exists
because the agent once fabricated a briefing). "Follow this order exactly" is precisely the
rigidity the new guidance says to preserve. The 6-line/emoji format is the user's confirmed
output interface, not style coercion.

---

## 14. bebop-evening-wrapup-pipeline.md — verdict: clean (0 findings)

Same contract as the morning pipeline with the TOMORROW-calendar delta explicitly called out.
The mirroring across the two files is not duplication to cut — each is a standalone procedure
loaded independently by a scheduled run, and the "Key difference from morning" line exists
precisely to keep the pair from being misread as identical. Keep rigid.

---

# Ranked cut candidates (by expected quality impact)

1. **subagent-brief.md 9.2** — "But ALL brands get:" mandatory six-feature motion list (plus 9.3
   quotas): actively manufactures the templated sameness it warns against, on every opus
   prototype run.
2. **concept-brainstorm.md 7.1** — three fully-worked example directions: strongest anchoring
   artifact in the suite; every brainstorm inherits walnut-and-serif eco-editorial gravity.
3. **interview-guide.md 6.1** — the AskUserQuestion "Tool rule" that SKILL.md flatly
   contradicts: a live instruction conflict, not just overconstraint.
4. subagent-brief.md 9.1 — $30K/$100K/"holy shit" role-play hype (frontend-design already owns
   the bar).
5. concept-brainstorm.md 7.2 — "Pitfalls to Avoid" red-flag list (pure restatement).
6. new-site SKILL.md 5.1/5.2 — worked navigator code + scripted closing prose.
7. interview-guide.md 6.2/6.3 — overridden conversation-style block + scripted questions.
8. Dedupe-only items: ship-site merge-block triplication (3.1), preview-site adapter summary
   (2.1), new-site "state it, don't ask" repeats (5.3).

Files to leave untouched: rollback-site, astro-setup.md, comparison-dashboard.md, grilling,
push-to-device, both bebop pipeline memories.
