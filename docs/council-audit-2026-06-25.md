# Council merge-gate audit — 2026-06-25

Auditor: Claude (Opus 4.8). Scope: the "Venice review council" merge gate as it runs in
CI (`council-v0.2.0`, pinned). Every claim below was checked against the actual engine
code and the real PR history on `rexmcintosh/swimtrack-website` (PRs #1–#11). Gate-critical
engine files (`review.py`, `engine.py`, `synthesize.py`, `config.py`, `venice.py`,
`routing.py`, `panels.toml`) are **byte-identical** between the working tree I read and the
pinned `council-v0.2.0` tag, so this analysis reflects what CI executed.

---

## 1. Executive summary

The council produces **genuinely valuable findings** — it caught the Astro View-Transitions
breakage in PR #9 and proposed `process.loadEnvFile` in PR #11, both adopted — but **as a
merge *gate* it is net-negative today**. On a correct 9-line dev-tooling change (PR #11) it
blocked **four times in thirteen minutes** on findings that are provably false or moot
(`ROOT` "undefined" when it's declared at line 12; "no `engines.node`" when `package.json`
pins `>=22.12.0`; a Node-version "TypeError" that cannot occur under that pin), then directly
**contradicted itself** (round 3: "narrow the catch to only ENOENT"; round 4, after the
author did exactly that: "catch more than just ENOENT"), and the human **merged over the red
gate** — the clearest possible signal the gate has lost authority. The root cause is
structural, not prompt-tuning: the gate is a **diff-only, ungrounded, single-lens OR** — any
one seat's `critical`/`high`-c≥8 finding fails the PR (`review.py:35`), the chair's synthesis
has **zero authority over the gate**, and the panel never sees the file or repo it is judging
even though CI has the full checkout. **The single highest-leverage fix: feed the panel the
full changed-file contents + a few always-on repo anchors (`package.json`, `.gitignore`) and
add a grounding pass that verifies a finding against that context before it may block.** Every
confirmed false positive is checkable by reading a file already present in the runner; this
one change removes them at the source while *preserving* the true positives (which survive
verification).

---

## 2. Findings table

Confidence = my certainty the issue is real, after verifying against code/PRs.

| # | Issue | Root cause (file · function) | Sev | Conf | Verified by |
|---|-------|------------------------------|-----|------|-------------|
| F1 | **Single lens can unilaterally gate.** `blocking` is an OR across *every* seat's raw findings; one `critical` or `high`-c≥8 fails the PR. No quorum. | `council/review.py:35` `run_pr_review`; `_is_blocking` `review.py:9-12` | High | Certain | All 5 blocking comments were driven solely by the Adversary; Security Officer approved in 6/6, Eng Manager never emitted `high`/`critical`. |
| F2 | **The chair has no power over the gate.** `synthesize()` output feeds the *comment body* only; `blocking` ignores it. The chair's "must-verify, may be outside the hunk" caveats can't stop a block. | `council/review.py:35-37`; chair value `syn` only used for text + `unavailable=syn.error` | High | Certain | review.py: `blocking = sum(... for f in r.findings ...)`; chair `syn` never consulted for pass/fail. |
| F3 | **Diff-only context blindness → false positives.** Shim passes only the raw diff; panel never sees the full file or repo, though CI checks out everything (`fetch-depth: 0`). | `scripts/venice_review.py:30,35` (only `DIFF_PATH`); `review.py:30-32` feeds `code_diff` only | High | Certain | PR #11 `ROOT`/`engines`/`.env` false positives; Adversary's own text: "before any declaration **visible in diff**". |
| F4 | **No grounding/verification.** Findings are taken at face value from model JSON; nothing checks them against the code. | `council/engine.py:24-37` `_ask_member` (parse, no verify); no verify step in `review.py` | High | Certain | `ROOT` (line 12), `engines>=22.12.0`, `.env` in `.gitignore` are all greppable in the checkout, never checked. |
| F5 | **Non-determinism + no cross-run memory.** `temperature=0.2`, no seed; each push re-litigates from scratch, unaware of prior rounds or human action. | `council/venice.py:20,46` (`temperature=0.2`); `venice_review.py:21-25` always POSTs a new comment | High | Certain | PR #11 self-contradiction across rounds; 4 separate comments posted; merged-over-gate never registered. |
| F6 | **Severity→gate miscalibration; no blast-radius.** Gate keys on a self-assigned severity *string* (un-normalized) + confidence; change size/risk/path tier ignored. A 9-line `tools/` script is gated like prod auth. | `review.py:9-12` `_is_blocking`; `engine.py:26` `severity=str(...)` (no normalization) | Med-High | Certain | PR #11 blocked repeatedly; `"High"`/`"CRITICAL"` casing would silently *bypass* the `==` checks. |
| F7 | **Severity word, not confidence, drives the gate (and it's noisy).** A seat that is *certain* of a real robustness nit (`med` c10) never blocks; a seat *moderately* sure of a moot issue (`high` c8) does. | `_is_blocking` weights label over confidence | Med | Certain | PR #11 r3 Eng Manager `med (c10)` did not block; Adversary `high (c8)` moot Node issue did. PR #9 r1 blocked (`high`), r2 passed (same theme rated `med`). |
| F8 | ▶ **Multi-comment spam, no dedup.** Every run POSTs a fresh comment; 4 near-identical reviews piled onto a 1-file PR. | `venice_review.py:21-25,37` `post_comment` | Low-Med | Certain | PR #11 has 4 council comments; no update-in-place, no fingerprinting. |
| F9 | ▶ **Fail-closed conflates "infra down" with "blocking findings."** Both exit 1; only the former should hard-fail closed. | `venice_review.py:38-45` | Low | Certain | A correct PR (#11) hard-failed CI identically to a real engine outage. |

▶ = discovered during the audit (beyond the six observed modes).

### Disposition of the six observed failure modes

- **A. Diff-only blindness → false positives — CONFIRMED (F3).** All three examples verified
  against the files: `ROOT` is `const ROOT = …` at `tools/i18n/translate.mjs:12`, used in
  `main()` at :88 (which runs last) → no `ReferenceError` possible; `package.json` has
  `"engines": {"node": ">=22.12.0"}`; `.gitignore:19` is `.env`. The runner had all three in
  its checkout.
- **B. Non-convergence / self-contradiction — CONFIRMED, verbatim.** Round 3 chair: *"Narrow
  the catch block to only swallow ENOENT."* Round 4 chair, reviewing the code *after* the
  author narrowed to ENOENT (`if (err?.code !== 'ENOENT') throw err;`): *"catching more than
  just ENOENT so EACCES/EPERM/malformed-.env don't abort."* Direct opposites. Driver: F5
  (temp 0.2, no memory) + the author also re-architected mid-review (package-flag → custom
  parser → `loadEnvFile`), and each new shape drew new objections.
- **C. Severity/gate miscalibration — CONFIRMED, with one refinement.** The change *was*
  blocked repeatedly with no blast-radius consideration (F6). **Refinement:** the confidence
  noise-gate actually works — every *blocking* finding was `high`/`critical` at c8–c9; the
  `low`/`med`/`tentative` nits did **not** block. The miscalibration is not "tentative
  findings block" but "**one seat's confident-but-wrong `high` blocks, and severity is an
  ungrounded self-label**" (F4/F6/F7). State this precisely in any write-up.
- **D. Adversary dominance — CONFIRMED and sharpened.** It is not that the chair "sides with"
  the Adversary — **the chair is irrelevant to the gate (F2)**. The gate is "any seat emits
  `critical`/`high`-c≥8," and across all 6 comments the Adversary (`grok-4-3`) is the *only*
  seat that ever did. Effectively a **panel of one** with three non-voting advisors.
- **E. No cross-run memory — CONFIRMED (F5/F8).** Stateless `run_pr_review`; a new comment per
  push; the human merge-over-gate on #11 left no trace the next run would have seen.
- **F. No grounding — CONFIRMED (F4).** No code path verifies any finding against the
  codebase.

---

## 3. Prioritized changelist

### (a) CI-shim changes — `swimtrack-website`

**S1 — Send full changed-file contents + always-on repo anchors, not the bare diff.** *(highest leverage)*
- **Problem:** F3/F4. The panel hallucinates context it can't see.
- **Evidence:** PR #11 blocked on `ROOT` "undefined" (declared at `translate.mjs:12`), "no
  `engines.node`" (`package.json` pins `>=22.12.0`), ".env may be committed" (`.gitignore:19`).
  The Adversary literally wrote "*before any declaration visible in diff*."
- **Change:** in `scripts/venice_review.py::main`, after computing the diff, read each changed
  code file from the checkout (`git diff --name-only "$BASE...HEAD"`) and append a
  `FULL CONTENTS OF CHANGED FILES (for context — review only the diff above)` block, capped
  per file (e.g. 400 lines / 40 KB) under the existing `settings.byte_cap`. **Always** append
  `package.json` and `.gitignore` as cheap repo anchors — they alone kill two of the three
  recurring false positives. Pass this as the review context instead of `diff` at
  `venice_review.py:30,35`.
- **Tradeoffs/risks:** larger prompts (bounded by `byte_cap=200_000` + per-file cap); the
  panel must be told to *flag* only diff lines but *reason* with full context (prompt nudge,
  engine-side E1). Minor cost increase.
- **Validate:** re-run on PR #11 diff with full `translate.mjs` + `package.json` + `.gitignore`
  in context → `ROOT`, `engines`, `.env`, and the Node-version block must no longer appear.

**S2 — One rolling comment + cross-run fingerprinting (dedup/state).**
- **Problem:** F8/F5. Four comments on a one-file PR; no awareness of prior rounds.
- **Evidence:** PR #11 carries 4 council comments (22:22→22:34), each re-deriving from scratch.
- **Change:** mark the council comment with a hidden HTML marker
  (`<!-- council-review -->`); on each run, GET issue comments, and if one exists, `PATCH` it
  instead of `POST` (`venice_review.py::post_comment`). Embed a JSON fingerprint of each
  blocking finding (path + normalized point) in the marker; suppress re-blocking on a
  fingerprint a maintainer has 👍-reacted or replied "ack/wontfix" to.
- **Tradeoffs:** loses per-round history (collapse prior rounds into a `<details>`); acknowledge
  flow needs a documented convention.
- **Validate:** push twice to a test branch → exactly one comment, updated in place; react to a
  finding → next push does not re-block on it.

**S3 — Separate "infra unavailable" (fail closed) from "blocking findings" (configurable).**
- **Problem:** F9. A correct PR hard-fails CI identically to an engine outage.
- **Evidence:** PR #11's 4 `failure` runs are indistinguishable in CI from a real Venice outage.
- **Change:** keep `unavailable → exit 1` (fail closed). Gate the *findings* path behind a
  `COUNCIL_ENFORCE` flag / required-check setting so a team can run the gate as **required** on
  `src/` but **advisory** on low-risk paths during calibration. Pairs with engine E4 (path tier).
- **Tradeoffs:** an advisory mode can be ignored — intended during calibration, then re-tighten.
- **Validate:** force a Venice 500 → still red (closed); a low-risk false positive in advisory
  mode → neutral check, comment still posted.

### (b) Engine changes — `council` package

**E1 — Grounding/verification pass before a finding may block.** *(highest leverage, pairs with S1)*
- **Problem:** F4. Findings block unverified.
- **Evidence:** `ROOT`, `engines`, Node-version blocks are all refutable from context the
  runner already has.
- **Change:** in `council/review.py::run_pr_review`, after `run_panel`, run a verification step
  over each *blocking-eligible* finding: a focused model call (or the chair, given full-file
  context from S1) answers `{"verified": bool, "why": "..."}` — "Is this finding true given the
  full file? A claim of 'X is undefined' is false if X is declared anywhere in the file." Drop
  findings that fail verification from the `blocking` count (still render them, demoted, as
  "unverified"). New helper `verify_findings(findings, context, client)`.
- **Tradeoffs:** +1 call per candidate finding (cheap — only blocking-eligible ones); a weak
  verifier could rubber-stamp (mitigate: verifier sees full file, must quote the line).
- **Validate:** ROOT/engines/Node findings → `verified:false` → dropped; #9 View-Transitions →
  `verified:true` (site uses Astro transitions) → still blocks.

**E2 — Chair-arbitrated, quorum gate; no single-lens unilateral block.** *(fixes D)*
- **Problem:** F1/F2. One seat (always the Adversary) gates; the chair is cosmetic.
- **Evidence:** every block in the history came from `grok-4-3` alone; Security Officer's clean
  approvals carried no weight; chair caveats ("confirm against the full file") couldn't stop it.
- **Change:** add a `blocking_findings: [{point, severity, confidence, why_blocking}]` field to
  the chair JSON (`prompts.py::SYNTH_OUTPUT`) — the chair must *promote* a seat finding to
  blocking, and is instructed that a single-lens finding needs corroboration OR a concrete,
  defensible exploit/break to block. Gate on **the chair's confirmed list** (`review.py:35`),
  not raw seat findings. Keep a safety valve: a `critical` from the **security** lens still
  blocks even single-handed (don't lose a real lone security catch).
- **Tradeoffs:** moves trust to the chair (`claude-opus-4-8`) — but that's its job; chair errors
  already fail closed via `unavailable`. A genuine single-lens non-security catch now needs
  chair sign-off (acceptable; the chair sees it).
- **Validate:** #9 View-Transitions (Adversary `high` + Eng Manager `med`, two lenses) → chair
  confirms → blocks. #11 ROOT (Adversary only, refuted by E1) → not promoted → passes.

**E3 — Determinism on the review path.**
- **Problem:** F5. `temperature=0.2`, no seed → cross-run flip-flop (B).
- **Evidence:** #11 r3↔r4 opposite catch-block demands; #9 r1 `high`/block vs r2 `med`/pass on
  the same lifecycle theme.
- **Change:** set `temperature=0` for review/gate calls (`venice.py` already plumbs
  `temperature`; pass 0 from the review path) and a fixed `seed` if Venice honors it. Determinism
  alone won't fix a wrong-but-stable verdict — E1/E2 do the heavy lifting — but it stops
  identical inputs producing opposite gates.
- **Tradeoffs:** slightly less diverse phrasing; fine for a gate.
- **Validate:** run the same diff twice → identical blocking set.

**E4 — Severity normalization + blast-radius/path tier.**
- **Problem:** F6/F7. Ungrounded self-label gates everything equally.
- **Evidence:** `engine.py:26` stores severity as a raw string; `"High"` would silently bypass
  `_is_blocking`. A `tools/i18n/*.mjs` 9-liner is gated like `src/`.
- **Change:** normalize severity (`.strip().lower()`, map synonyms) in `_ask_member`/
  `_is_blocking`. Add a path-risk tier: `src/`, auth/payment/security paths → full gate;
  `tools/`, `scripts/`, `*.config.*`, dev tooling → require chair-confirmed `critical` (or
  2-lens quorum) to block. Optionally raise the bar as diff size shrinks (a 9-line change should
  not block on robustness *taste*).
- **Tradeoffs:** path tiers are a heuristic; make them config in `panels.toml`/settings, not code.
- **Validate:** #11 (`tools/i18n/translate.mjs`) requires chair-confirmed critical → robustness
  nits no longer block; a `src/` auth diff keeps the full gate.

---

## 4. Calibration proposals with acceptance criteria

Build a tiny harness that runs the proposed config against the **real PR #9 and #11 diffs**
(both available via `gh pr diff`), *with S1 full-file context*, and asserts:

- **AC1 — kill the false-positive blocks (A/F).** On PR #11's final diff + full context, the
  gate must **pass** (0 chair-confirmed blocking). Specifically: `ROOT`-undefined,
  `no engines.node`, `.env-may-be-committed`, and `process.loadEnvFile-breaks-on-Node<20.6`
  must each be either absent or verified-false (E1) — they are refuted by `translate.mjs:12`,
  `package.json engines >=22.12.0`, `.gitignore:19`.
- **AC2 — preserve the true positives (no regression).** On PR #9 round-1 diff, the
  View-Transitions / dynamic-`.reveal` breakage must **still surface and still block** (two
  lenses raised it; chair confirms). On PR #11 round-2 diff, "replace the hand-rolled `.env`
  parser with `process.loadEnvFile`" must **still appear** as a recommendation. A config that
  suppresses either of these is a **regression** and must be rejected.
- **AC3 — convergence (B).** Feeding the *final merged* `translate.mjs` (loadEnvFile +
  ENOENT-narrow, engines `>=22.12.0`) must **pass**, and must **not** demand "catch more than
  ENOENT" as a *blocker* (grounding sees the engines pin → Node-compat moot; ENOENT-narrowing is
  taste, not `high`). Run it 3× (E3) → identical verdict each time.
- **AC4 — no single-lens unilateral block (D).** A finding raised by only the Adversary at
  `high`-c8, uncorroborated and not chair-promoted, must **not** block by itself; the same
  finding raised by ≥2 lenses, or chair-confirmed, **does**.
- **AC5 — security safety valve.** A synthetic single-lens **security** `critical` (e.g. a
  planted hardcoded secret in the diff) must **still block** even though only one seat raised it
  — verify E2's exception preserves lone real security catches.

---

## 5. "Do not change" list (working — keep)

- **Fail-closed on genuine engine/chair unavailability** (`review.py:37`,
  `venice_review.py:38-41`). Keep — but scope it to true infra failure (S3/F9), not to findings.
- **Code/doc split with docs as advisory** (`routing.py`, `review.py:39-46`). Working: doc-only
  PRs (#6, #8) correctly passed/stayed advisory. Keep.
- **The Security Officer lens and its "zero noise > zero misses" posture.** It approved correctly
  in 6/6 comments and never produced a false positive. Keep the seat and its prompt; the bug is
  the gate rule, not this lens.
- **Cross-model-family diversity; Adversary is non-Claude (`grok-4-3`).** The independence is
  valuable — it's *what* the Adversary surfaced (the #9 true positive) that proves it. Fix the
  gate (E2), not the seat.
- **Confidence noise-gate in rendering** (`render.py::gate_findings`, `_MIN_CONF`). It correctly
  demoted/dropped the low-confidence nits; the blocking problem lives in `review.py`, not here.
  Keep.
- **API-key scrubbing chokepoint** (`venice.py::_scrub`). Good hygiene; keep.
- **Consolidated single comment + raw-panel `<details>`** for transparency. Keep the
  consolidation; just make it update-in-place (S2).
- **Lenient JSON parsing** (`jsonparse.py`). Keep — it salvages fenced model output that would
  otherwise be discarded.

---

## Appendix — evidence index

- Gate logic: `council/review.py:9-12` (`_is_blocking`), `:35-37` (blocking count, unavailable).
- Context starvation: `scripts/venice_review.py:30,35`; `council/review.py:30-32`.
- Determinism: `council/venice.py:20,46` (`temperature=0.2`).
- No state: `scripts/venice_review.py:21-25` (always POST).
- PR #11: 4 council comments 22:22–22:34, all `failure`; merged 22:45 (human override). Round 3
  chair "narrow to only ENOENT" ↔ round 4 chair "catch more than just ENOENT." Every block from
  `grok-4-3`; Security Officer approved each round.
- PR #9: r1 `failure` (Adversary `high` c9/c8 View-Transitions — true positive, fixed) → r2
  `success` (same theme rated `med` → under gate). Chair r2 suggested `process.loadEnvFile`
  lineage for #11's eventual fix.
- Ground truth: `tools/i18n/translate.mjs:12` (`const ROOT`), `package.json` engines
  `>=22.12.0`, `.gitignore:19` (`.env`). `process.loadEnvFile` exists ≥ Node 20.12 — the pin
  guarantees it.
- Cross-PR gate behavior (Venice Review Council conclusions): #2 fail,fail · #4 fail · #5 fail ·
  #6 success · #7 fail · #8 success×3 · #9 success,fail · #10 success · #11 fail×4. (Workflow
  also runs `npm`/translate steps, so non-#9/#11 failures are suggestive, not council-confirmed.)
