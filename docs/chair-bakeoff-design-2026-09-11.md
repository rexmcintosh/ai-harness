# Chair bake-off — a runnable design (NOT run, NOT applied)

`panels.toml:9` sets `chair_model = "claude-opus-4-8"` as one global value used by
every panel, every rigor level, the CI merge gate and the backlog runner. On the
round observed on 2026-09-11 the chair was 97% of the cost. It is also the seat
that decides whether a PR merges.

**Nothing here changes it.** The chair is a quality decision and it belongs to the
operator, exactly like the judge model in the Ultimate Portugal engine. This
document is the experiment that would earn the decision.

Method follows the judge bake-off run on 2026-09-11: replay a **real saved call**
against candidates and grade them on a **specific known-right answer**, not on
whether the prose reads well.

---

## 1. What the chair actually does, and therefore what to test

`council/synthesize.py` sends the chair one call containing

- `ORIGINAL INPUT` — the diff, plus (on the CI path) the full contents of every
  changed file, capped at 40 KB per file and 160 KB total, and
- `PANELIST ANSWERS` — `_panel_digest()`: each seat's stance, headline, findings
  with severity and confidence, and suggestions.

and takes back JSON. `council/gate.py::decide_blocking` then does something
unusual: **the panel can only nominate, the chair alone confirms.** A finding
blocks only if the panel raised a candidate at the tier bar *and* the chair listed
it in `blocking_findings`. The chair can drop a confident panel finding to zero. It
cannot invent one.

So the chair is graded on exactly one skill: **when the seats disagree, does it
resolve the split against the evidence in front of it, or does it hedge?**

---

## 2. Test cases

The council's own history is the corpus. Every review the gate has ever run is
still on GitHub as a `github-actions[bot]` comment containing the chair's
synthesis **and** the raw panel, and the repository history says what the operator
did next. That is a ground truth nobody had to invent.

### Case A — `aris-pr1` (known false, committed fixture)

`tools/regress/fixtures/aris-pr1`. aris-management-website PR 1, base
`489b6070`, head `0595eaf9`. Diff 5,217 B; grounded context 8,423 B.

Panel raises, ground truth says all false: missing `width`/`height` on images
(`high` c9), no WebP. The v0.2.0 gate blocked on the first; v0.4.0's chair
declined to confirm it, verbatim: *"Valid maintainability suggestion, not a defect.
Non-blocking."*

**Right answer: `blocking == 0`, and neither named finding appears in
`blocking_findings`.**

### Case B — `stw-pr11` (known false, committed fixture)

`tools/regress/fixtures/stw-pr11`. swimtrack-website PR 11, base `95c87b72`,
head `eba8b540`. Diff 1,495 B; grounded context 7,805 B.

Panel raises, ground truth says all false: `ROOT` not declared, `engines` not
pinned, `.env` not gitignored, and `process.loadEnvFile` breaks on Node < 20.6
(`high` c8 and c9) — refuted by the `engines: >=22.12.0` pin sitting in the
`package.json` the chair was handed.

**Right answer: `blocking == 0`. The Node-version finding is the graded one: it is
refutable only by reading the context, so a chair that confirms it did not read.**

### Case C — `baw-pr11` (known TRUE — must be built)

**Not yet a fixture.** build-ai-automation-workflow PR 11, "Harden the venice_review
shim per the council's own CI-rollout review", base `6b4c81fe`, head `d74db908`,
merged 2026-08-16. 5 files, +233/-28. Diff 17,729 B; head files 22,461 B.

Its saved council comment records two disagreements, and the chair declined both:

> **Disagreement — Severity of missing 429/403 retry handling** (taste)
> chair's call: *"Treat as a robustness improvement, not a blocker... Worth a
> follow-up (retry 429/403/408 honoring Retry-After) but does not block."*

> **Disagreement — Marker-ownership 403 denial-of-service** (taste)
> chair's call: *"Real edge case but speculative... Recommend adding an author
> check (github-actions[bot]) before PATCH, but this is a hardening suggestion, not
> a verified defect. Not blocking."*

**Both were real.** Both shipped as fixes in PR 12 the same day, and both are named
in the shim's own docstring today:

```
Hardening round 3 (council follow-ups on build-ai-automation-workflow PR #11, 2026-08-16):
  - GitHub API retries also cover 429/408 throttling, honoring Retry-After (capped).
  - The rolling comment is only ever PATCHed when it is marker-matched AND authored by
    github-actions[bot] — a marker planted in a third-party comment can no longer 403
    the gate closed.
```

The dissenting seats were right; the incumbent chair under-called both.

**Right answer: `review_status == "changes_requested"`, with `required_changes` /
`blocking_findings` naming (i) retry on 429/408 honouring `Retry-After` and (ii) an
author check before PATCH.** Partial credit for one of two.

**Case C is the load-bearing case.** Without it the bake-off has a degenerate
winner: a chair that confirms nothing at all scores perfectly on A and B. Do not
run the bake-off with A and B alone.

### Rejected candidate — swimtrack-website PR 9 round 1

`docs/council-regression-2026-07-23.md` names this as the unbuilt AC2 fixture
("PR #9 round-1 View-Transitions breakage"). **It does not survive checking, and
should not be used.** Round 1 (`b573de00`) split the panel: Adversary
`high` c9 + `high` c8 that a one-shot observer breaks across view transitions; Eng
Manager `low` c9 + `med` c6 tentative; Security Officer silent. The chair deferred:
*"severity depends on whether the site actually uses view transitions."*

Checked today: at `b573de00` the repo had **no** `<ViewTransitions />`, no
`ClientRouter`, and `astro.config.mjs` set `output: 'static'`. The chair's hedge was
evidentially defensible and the Adversary's `high` was about a hypothetical. The
author's next commit added `astro:page-load` anyway — belt and braces, not a bug
report. There is no clean right answer here, so it is not a graded case.

It is still worth keeping as an **ungraded probe**: the decisive evidence (no
`<ViewTransitions />` in the full `Layout.astro` the chair was given) *was* in
context, and a chair that resolves the conditional out loud is doing something the
incumbent did not. Score it as a comment, not a point.

### Building Case C

The existing harness already writes fixed-head fixtures with hash evidence.

```bash
cd ~/projects/build-ai-automation-workflow
BASE=6b4c81feebf120a51d8d01ac35131f81b78c3887
HEAD=d74db9085caffa3ed9e16035dd3cc2ca159de103
FX=tools/regress/fixtures/baw-pr11
mkdir -p "$FX/head"
git diff --no-color "$BASE" "$HEAD" > "$FX/change.diff"
git -C . archive "$HEAD" \
  .github/workflows/venice-review.yml scripts/venice_review.py \
  setup/templates/venice-review.yml setup/templates/venice_review.py \
  tests/test_venice_review_refactor.py package.json .gitignore 2>/dev/null \
  | tar -x -C "$FX/head"
python3 -c "
import sys; sys.path.insert(0,'tools/regress')
import harness
from pathlib import Path
harness.write_manifest(Path('$FX'), fixture_id='baw-pr11',
  repo='https://github.com/rexmcintosh/build-ai-automation-workflow',
  pr=11, base='$BASE', head='$HEAD', expected_blocking=2)
"
```

`.gitattributes` already marks `tools/regress/fixtures/**` as `-text`, so the
hashed bytes survive a checkout with `core.autocrlf` on. The `write_manifest`
kwargs above match `tools/regress/harness.py:66` as of this commit — re-read it
rather than trusting this snippet if the harness has moved on.

`harness.grade_records` does support a non-zero expectation — it passes a cell when
`blocking == expected_blocking` — but no fixture has ever used one, so exercise it
with `--dry-run` before spending.

**Do not grade Case C on that count alone.** `blocking == 2` is too brittle a
target: a chair that confirms both real findings as one merged item scores 0, and a
chair that confirms two unrelated things scores 2. For Case C the count is a
tripwire; the actual grade is Axis 1 below, which reads which findings were named.

---

## 3. Candidates

Live from `GET https://api.venice.ai/api/v1/models`, `model_spec.pricing`, fetched
2026-09-11. DIEM per million tokens. **Re-fetch before running — prices move;
`openai-gpt-56-sol` was billed 6.25/37.50 on 2026-09-03 and 2.50/12.50 a week later.**

| model | in | out | cache-in | context | maxComp | privacy |
|---|---:|---:|---:|---:|---:|---|
| `claude-opus-4-8` *(incumbent)* | 6.000 | 30.000 | 0.600 | 1,000,000 | 128,000 | anonymized |
| `claude-sonnet-5` | 3.000 | 15.000 | 0.300 | 1,000,000 | 64,000 | anonymized |
| `openai-gpt-56-sol` | 2.500 | 12.500 | 0.250 | 1,000,000 | 128,000 | anonymized |
| `z-ai-glm-5-3` | 1.750 | 5.500 | 0.325 | 1,000,000 | 131,072 | **private** |

Context is not the binding constraint: `byte_cap = 200000` plus the digest is
around 70k tokens, the largest chair prompt ever recorded is 125,886 tokens, and
every candidate carries 1M. Any model under ~400k context is excluded on headroom
alone, which is why `grok-4-6` (500k, and the wrong family — see below) and
`openai-gpt-53-codex` (400k) are not here.

Why these four:

- **`claude-opus-4-8`** — the incumbent. It must run in the same harness, same
  fixtures, same prompts, or the comparison is worthless.
- **`claude-sonnet-5`** — the conservative swap. Same family, so the markdown-fenced
  JSON that `council/jsonparse.py` exists to recover behaves the same way. Exactly
  half the price.
- **`openai-gpt-56-sol`** — won the Ultimate Portugal judge bake-off on 2026-09-11,
  matching `claude-fable-5-1` on the graded question at 14% of the price. Testing
  whether the same winner wins a different job is worth one arm. **Caveat for the
  rubric: it shares a family with the code-review panel's Eng Manager seat
  (`openai-gpt-53-codex`), and the chair's whole value is arbitrating between
  families. Watch specifically for it siding with the Eng Manager on A, B and C.**
- **`z-ai-glm-5-3`** — the aggressive swap and the only fully independent family in
  the list. 5.5x cheaper output than the incumbent, and `privacy: private` (zero
  data retention) where the other three are `anonymized` — a real bonus when the
  payload is private source.

Also considered and left out: `zai-org-glm-5-2` (1.400/4.400 — cheaper still, but
too close to `z-ai-glm-5-3` to buy a distinct answer; the obvious substitute if a
fifth arm is affordable), `kimi-k3` and `openai-gpt-54` (~18.8 out, barely cheaper
than the incumbent), `deepseek-v4-pro` and `grok-4-3` (already panel seats — a chair
that is also a seat is not independent), `gemini-3-1-pro-preview` (a seat on three
panels), `claude-fable-5-1` (12.000/60.000, twice the incumbent).

---

## 4. Rubric

Grade the **returned JSON**, not the prose. Three scored axes and two recorded but
unscored.

**Axis 1 — Verdict (0–2 per fixture).** Mechanical, from `run_pr_review`'s return.

| | A `aris-pr1` | B `stw-pr11` | C `baw-pr11` |
|---|---|---|---|
| 2 | `blocking == 0` | `blocking == 0` | `changes_requested` naming **both** the Retry-After fix and the author check |
| 1 | — | — | names exactly one |
| 0 | `blocking > 0` | `blocking > 0` | `clean`, or blocking on something else |

**Axis 2 — Grounding (0–2 per fixture).** Did the synthesis cite the context that
settles it? Scored by string match on the chair's own text, so it is reproducible:

- A: the chair says the width/height finding is a maintainability point, not a defect.
- B: the chair names the `engines` pin (`>=22.12.0` / `package.json`) when dismissing
  the Node-version finding. A chair that reaches `blocking == 0` without naming it
  got the right answer by luck; score 1, not 2.
- C: the chair names `Retry-After` / `429` and `github-actions[bot]` / PATCH.

**Axis 3 — Discipline (0–2, once per run).**
- Parses as JSON on the first attempt without `jsonparse.loads_lenient` falling
  through to the fence or brace path (instrument `loads_lenient` to report which
  branch fired).
- `blocking_findings` entries all carry a non-empty `point`, a `severity` and a
  `why` — `synthesize()` downgrades `review_status` to `"unknown"` when they do
  not, and an `"unknown"` verdict is a silent gate failure.
- No finding the panel never raised (the chair must not originate blocks).

**Recorded, not scored:** cost in DIEM from the ledger row each call writes, and
wall-clock latency. These break ties; they never win on their own.

**Ungraded probe:** on the PR 9 round-1 replay, does the chair resolve the
view-transitions conditional against `Layout.astro` rather than restating it?

**Passing bar.** Max 15 (3 fixtures x 4) + 3. A challenger replaces the incumbent
only if, across all repeats, it (i) never scores 0 on Axis 1 for A or B — a false
block on the gate is worse than a missed finding, because it stops 18 repos' merges
— and (ii) scores at least as well as the incumbent on C. Cost decides between
challengers that clear both, never between a challenger and the incumbent on
correctness.

**Run each cell 3 times.** `temperature=0` on the gate path is not determinism
across models, and the 2026-07-23 regression run's own limits section says n=1 per
cell detects but does not estimate. Three is the minimum that shows variance;
disagreement between a candidate's own repeats is itself a finding.

---

## 5. Cost

Only the **chair** is called. The panel is replayed from a recorded digest — the
saved GitHub comment for C, a recorded `run_panel` result for A and B — so every
candidate arbitrates the identical panel and no member tokens are bought. That is
what makes this cheap *and* what makes it a clean comparison.

Measured prompt sizes (real, from the committed fixtures and the live PR), at a
deliberately pessimistic 3.0 characters per token and 3,000 completion tokens per
call:

| fixture | chair prompt | ~tokens in | opus-4-8 | sonnet-5 | glm-5-3 | gpt-56-sol |
|---|---:|---:|---:|---:|---:|---:|
| `aris-pr1` | 17,188 B | 5,729 | 0.3731 | 0.1866 | 0.0796 | 0.1555 |
| `stw-pr11` | 12,848 B | 4,283 | 0.3471 | 0.1735 | 0.0720 | 0.1446 |
| `baw-pr11` | 43,870 B | 14,623 | 0.5332 | 0.2666 | 0.1263 | 0.2222 |
| **total (3 repeats)** | | | **1.2534** | **0.6267** | **0.2778** | **0.5223** |

| plan | chair calls | DIEM |
|---|---:|---:|
| 2 fixtures (A+B only) x 4 x 2 — **do not use, degenerate** | 16 | 1.021 |
| 3 fixtures x 4 x 2 | 24 | 1.787 |
| **3 fixtures x 4 x 3 — recommended** | **36** | **2.680** |
| 3 fixtures x 4 x 5 | 60 | 4.467 |

**Budget warning.** The account balance is about 6 DIEM and the shared wrapper cap
is 6 DIEM. The recommended plan is 2.68 — 45% of the remaining balance. Either top
up first, or run the 24-call plan at 1.79 and accept n=2. Run it under the capped
transport hook (`--transport-hook`), not bare.

Estimates run **high** on purpose: 3.0 chars/token is near the pessimistic end of
the 2.3–4.0 range the Ultimate Portugal work measured across tokenisers, and 3,000
completion tokens is above the chair's ledger mean of 1,670. A projection that
guesses low is a guard that lets a run through.

### What the decision is worth

423 chair calls in 54 days of local use — about 2,859 a year, with the CI gate on
top and currently unmeasured (that is what `docs/ci-usage-durability-2026-09-11.md`
fixes). At the observed chair means of 28,394 prompt tokens and 1,670 completion
tokens:

| model | DIEM/call | DIEM/yr | vs incumbent |
|---|---:|---:|---:|
| `claude-opus-4-8` | 0.2205 | 630.3 | — |
| `claude-sonnet-5` | 0.1102 | 315.2 | −315.2 |
| `openai-gpt-56-sol` | 0.0919 | 262.6 | −367.7 |
| `z-ai-glm-5-3` | 0.0589 | 168.3 | −462.0 |

A 2.68 DIEM experiment against a 168–462 DIEM/yr swing. That is the case for
running it; it is not a case for changing the chair without running it.

---

## 6. Running it

The harness this extends is `tools/regress/` — it already pins a council version,
validates fixture hashes, refuses to spend without `--paid` and an explicit
`--estimate-diem`, and writes atomic JSON evidence. Three additions are needed:

1. **`--chair-model MODEL` (repeatable)** — `tools/regress/run.py:177` already
   passes `chair_model=getattr(settings, "chair_model", "")`; make it iterate the
   flag values instead. One extra dimension on the run matrix.
2. **`--replay-panel PATH`** — load `list[MemberResult]` from JSON and skip
   `run_panel` entirely, so the candidates share one panel and only the chair is
   billed. Record the panel once with `--record-panel PATH`.
3. **`--repeats N`** — three rollouts per cell, each its own row in the evidence.

Then:

**Before any of this: there is no capped transport hook.** The regress README's
`--transport-hook /absolute/path/to/budget_transport.py:post` is a placeholder; no
such file exists in this repo. The nearest real thing,
`~/projects/.session-gc/up-refresh-batch-20260907/venice_budget.py`, wraps the
Ultimate Portugal Node clients and asserts a LIFETIME 6-DIEM key that belongs to a
finished batch — it is not a Python `post` callable and cannot be pointed at this.
Either write the hook (a `post(url, **kw)` that refuses once a running total
crosses a ceiling) or issue a fresh Venice key whose own LIFETIME limit is the cap,
and run bare against that. Do not run 36 paid calls against the shared key with no
ceiling at all.

```bash
cd ~/projects/build-ai-automation-workflow
set -a; . ~/.env; set +a

# 0. free, no API calls — fixture hashes, context construction, matrix shape
python3 tools/regress/run.py --dry-run --output /tmp/bakeoff-dry.json

# 1. record ONE panel per fixture (4 member calls each; NOT chair calls).
#    Case C's panel comes from the saved PR comment, so it needs no API call —
#    transcribe it instead, exactly as the digest in the comment reads.
python3 tools/regress/run.py --paid --estimate-diem 0.30 \
  --record-panel tools/regress/results/panels/ \
  --transport-hook /home/dev/projects/build-ai-automation-workflow/tools/budget_transport.py:post

# 2. the bake-off: 36 chair calls, no member calls
python3 tools/regress/run.py --paid --estimate-diem 2.70 \
  --replay-panel tools/regress/results/panels/ \
  --repeats 3 \
  --chair-model claude-opus-4-8 \
  --chair-model claude-sonnet-5 \
  --chair-model openai-gpt-56-sol \
  --chair-model z-ai-glm-5-3 \
  --transport-hook /home/dev/projects/build-ai-automation-workflow/tools/budget_transport.py:post \
  --output tools/regress/results/chair-bakeoff-$(date -u +%Y%m%dT%H%M%SZ).json

# 3. what it cost, from the ledger the calls wrote themselves
venice-usage report --since "$(date -u -d '-1 hour' +%Y-%m-%dT%H:%M:%S)" --group-by model
```

`--transport-hook` above names a file that must be written first (see the warning
at the top of this section); the regress README documents the contract it has to
satisfy — an absolute path, `file.py:callable`, loaded and handed to `VeniceClient`
as its HTTP `post`.

Grade from the evidence JSON, not from reading the comments: Axis 1 is `blocking`
and `review_status` straight out of the payload, Axis 2 is a string match on the
recorded body, Axis 3 is the `loads_lenient` branch and the `blocking_findings`
shape.

---

## 7. What this experiment does not settle

- **It tests the chair on code review only.** `decision`, `brainstorm`,
  `spec-review` and `red-team` use the same global `chair_model` and none of them
  is represented here. A winner on code review is evidence for the gate, not a
  mandate for `[settings] chair_model`. If the answer differs by panel, that is an
  argument for making `chair_model` per-panel — which the config already has the
  shape for, since `chair_max_completion_tokens` resolves per panel and per rigor.
- **Three fixtures, three rollouts, one chair each: this detects, it does not
  estimate.** Same claim class as `docs/council-regression-2026-07-23.md`. It can
  say "this candidate failed a case we know the answer to". It cannot say
  "candidate X is 12% better".
- **The fixtures are small.** 12–44 KB of chair prompt against a `byte_cap` of
  200,000 B and a recorded maximum chair prompt of 125,886 tokens. Long-context
  degradation is exactly where cheap models break, and none of these cases would
  catch it. Add a fourth fixture built from a genuinely large PR before trusting a
  challenger on the CI gate.
- **It measures the chair in isolation.** The model that best arbitrates *this*
  panel might not be the best chair for a different panel composition. Change one
  thing at a time.
