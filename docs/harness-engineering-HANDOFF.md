# Handoff — harness-engineering assessment → implementation

Paste the block below into a fresh session to continue. Full reference:
`~/projects/build-ai-automation-workflow/docs/harness-engineering-assessment-2026-07-20.md`

---

## CONTEXT

I ran an assessment comparing Ryan Lopopolo's `harness-engineering` repo (cloned
to `~/projects/harness-engineering`; it's a ~50k-word *prose* reference library
for agents, not runnable code — 12 theses + 2 playbooks + an eval method) against
my own harness. The written assessment is at:

`~/projects/build-ai-automation-workflow/docs/harness-engineering-assessment-2026-07-20.md`

Read that file first — it has the evidence for everything below.

**Headline:** we independently built running versions of most of what he
theorizes (loom = feedback-into-infrastructure; 8 cron loops = continuous
maintenance; council = convergent review in all 17 CI repos). His theory gives us
*names* for gaps we couldn't see. loom is more operationally mature than anything
in his corpus.

**The gaps found (ordered by consequence), all verified against the live system:**
1. **Two authority docs, drifted.** The merge protocol lives in both
   `~/.claude/CLAUDE.md` (85 lines, canonical) and `~/projects/AGENTS.md`
   (42 lines, older/weaker). AGENTS.md — the file **Codex** reads via the
   `delegate` skill — is missing the push step, the `git status -sb` verification,
   the scope-limiting clause, branch deletion, and the whole session-gc + backlog
   sections.
2. **Harness not versioned.** `~/.claude/.git` tracks only 9 files (`.gitignore` +
   `CLAUDE.md` + 7 commands). 13 skills, settings, agents untracked. `.gitignore`
   denies settings.json because it holds a live plugin token — solvable with a
   redacted template.
3. **Everything accretes, nothing retires.** loom has no GC pass. `MEMORY.md` =
   99 lines loaded every session. `archive.yaml` empty. Across 3,805 July
   transcripts, **5 of 13 skills invoked 0 times** (topical-map-dfseo2, tidy,
   rollback-site, preview-site, dist). `preview-site` (0) is the broken middle of
   adjust-site(6)→ship-site(2). 2 memory files unindexed; 1 is a dup.
4. **No invariant has a verifier.** Memory schema declares 4 types; 36 of 100
   files use a type not in the schema.
5. Proof is free-text `Verified:`, not claim-matched evidence.
6. `build-ai-automation-workflow` and `splash_poller` (highest-leverage repos)
   have no CLAUDE.md; romance-empire has 424 lines.
7. `set -a; . ~/.env` exposes all 14 keys to any agent that needs one.

**Two mechanisms worth stealing from his `docs/domain-modeling/homelab.md`** (his
own homelab — closest structural analogue to our VPS):
- **Versioned automation contracts:** each cron loop points at a checked-in
  contract declaring scope, default mode (report-only/change-producing/
  deploy-capable), authoritative sources, required validation, approval/abort,
  summary schema. Our 8 loops have none — authority lives implicitly in shell.
- **Report-only-by-default doc-freshness role** = the loom GC pass, already
  designed. Also: remote shell scripts should put their body in a `main()` invoked
  once at the end so a truncated transfer leaves only unexecuted definitions.

**The eval layer (from a separate "vibes vs evals" thread) converges here:**
- **Start with council.** Highest stakes (gates all 17 CI repos), and its output
  is a *deterministic* `blocking: true|false` → programmatic grader, no LLM judge,
  no grader-validation problem.
- **The golden set already exists, misfiled as memory prose.** 5 labeled
  false-positive cases with recorded ground truth in
  `~/.claude/projects/-home-dev/memory/council-review-failed-not-always-blocking.md`,
  `aris-management-website.md`, `swimtrack-council-gate.md`:
  - aris PR #1: "missing width/height" (false — `dist/` shows `width="1536"`),
    "no WebP" (false — 2.9MB PNG→52-276kB WebP)
  - swimtrack-website PR #11: "ROOT not declared" (false, line 12), "engines not
    pinned" (false), ".env not gitignored" (false). Blocked 4×.
  - Both PRs merged; diffs retrievable via `gh pr diff`.
- **council v0.4.0 was built to kill exactly this class and was never re-run
  against these 5 cases.** That is the tweet's critique inside our own stack.
  (`monthly-bidding` is still pinned to the bad `council-v0.2.0`.)
- Two corrections to the 6-component eval design: (a) n=1 buys *regression
  detection*, not *effect estimation* — Ryan's evals/ calls "one stochastic
  rollout treated as representative" an invalid result; name which one each eval
  buys; (b) the design needs a 7th component, **retirement** (remove tooling that
  doesn't survive ablation) — else the eval layer is Gap 3 with a dashboard. The
  skill-invocation grep is already a free retirement signal.
- Blind spot: no retrieval instrumentation for *context* — "invoked" is
  measurable for skills/tools but not for 100 memory files / 203 wiki articles,
  which is why the MEMORY.md tax can't be justified or trimmed.

## HARD CONSTRAINTS (from ~/.claude/CLAUDE.md — do not violate)

- **Merge protocol:** when a branch is merge-ready, post a compact **Merge
  recommendation** and STOP. Merge only on explicit "do it"/"merge it"/"ship it".
  Push until `git status -sb` shows `## main...origin/main` no `[ahead N]`. Delete
  claude/* session branch at merge.
- `build-ai-automation-workflow` HAS a git remote — real PRs/pushes possible.
  `~/.claude` and `~/wiki` are local-only git repos (no remote — "local only").
- **Don't write to `~/projects/backlog/` without an explicit go-ahead** (propose,
  then confirm). Don't dangle vague loose ends — package as cold-runnable or ask.
- **Don't create memory/wiki notes for things the repo already records** (the
  assessment doc records this work). Adding accretion is literally Gap 3.
- Working-tree diff review → `council review --diff` (needs
  `set -a; . ~/.env; set +a` for VENICE_API_KEY).
- Plain-English by default.

## WHAT I WANT DONE (proposed order — confirm before starting)

**(0) Collapse the two authority docs — 15 min, highest consequence/lowest effort.**
Make `~/.claude/CLAUDE.md` canonical; reduce `~/projects/AGENTS.md` to a pointer.
Done = one source of truth for the merge protocol; Codex reads the real one.

**(1) Version the harness — ~30 min, unblocks everything.**
Extend `~/.claude/.gitignore` to track `skills/` + a secrets-stripped
`settings.template.json` (keep live `settings.json` ignored — plugin token).
Commit. Done = `git -C ~/.claude log` shows skills tracked; a skill edit is now
diffable/revertable.

**(2) council golden set — the eval MVP AND the answer to "did our fix work?"**
Build 5 fixtures from the memory files above (PR diff + expected `blocking:false`
+ recorded reason). Write a programmatic grader. Run council-v0.2.0 vs v0.4.0
against all 5. Report the two numbers. Done = a real regression number exists +
we know whether v0.4.0 actually suppresses the 5 known false positives. Decide
where this lives (likely `~/loom-runtime/council/` since council is a package
there; note `~/loom-runtime` origin is `~/projects/build-ai-automation-workflow`).

**(3) One versioned contract per cron loop** (homelab pattern). This doubles as
the eval layer's success definitions. Start with watchdog (its "never touches
prod" is currently just a README sentence).

Start by reading the assessment doc, then confirm the plan (or adjust it) before
touching anything. (0)+(1)+(2) is ~one session and (2) produces a real number.
