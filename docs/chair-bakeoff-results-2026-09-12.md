# Chair bake-off — the run

> The decision that followed this run (2026-09-20) is recorded in `docs/council-chair-decision-2026-09-20.md`.

Executed 2026-09-12 against `docs/chair-bakeoff-design-2026-09-11.md`. 36 paid
chair calls, no panel calls. **Nothing here changes `chair_model`.** `panels.toml`
is byte-identical to `main`; the seat is still `claude-opus-4-8` and the decision
is still the operator's.

Evidence: `docs/evidence/chair-bakeoff-2026-09-12.json` (every prompt size, every
raw chair response, every score input). Runner: `tools/regress/bakeoff.py`.
Grader: `tools/regress/bakeoff_grade.py`. Replayed panels:
`tools/regress/panels/*.json`. Spend cap: `tools/budget_transport.py`.

## Run receipt

| | |
|---|---|
| Balance before | **27.6123 DIEM** |
| Balance after | **24.0648 DIEM** |
| Spend attributable to this run | **2.2387 DIEM** (36 chair calls 2.2123 + 1 smoke call 0.0264) |
| Against the design's estimate | 2.68 — came in **16.5% under** |
| Ledger rows added | **37**, all `project=council`, `task_type=review`, `source=council/venice` |
| Spend ceiling | 4.00 DIEM, enforced per call, never approached, refused nothing |
| Council code under test | HEAD, v0.5.0, commit `0269003` |
| `chair_model` in `panels.toml` | **unchanged** — `git diff main -- council/panels.toml` is empty |

The balance fell 3.5475 while this run spent 2.2387: other work on this box
(a `romance` image/persona batch) was billing the same account concurrently, so
the balance delta is not this run's cost. The 2.2387 figure is the sum of the 37
ledger rows these calls wrote, priced through `venice_usage.pricing`, and it
matches the capped transport's own independently-computed running total to four
decimal places.

---

## 1. What the design got wrong, and what it cost to find out

The design was written on 2026-09-11. Three merges landed after it. Everything
below was checked before any money was spent, and five of the seven items would
have wasted calls or produced ungradeable evidence.

**1. Prices did not move.** All four candidates bill exactly what the design's
table says: `claude-opus-4-8` 6.000/30.000, `claude-sonnet-5` 3.000/15.000,
`openai-gpt-56-sol` 2.500/12.500, `z-ai-glm-5-3` 1.750/5.500 DIEM per million.
Confirmed against `venice-billing models` and `python -m venice_usage.refresh_prices
--check` (exit 0). The design's warning was right to exist and wrong this week.

**2. The pinned harness cannot grade the design's own rubric.** `tools/regress/run.py`
installs council at tag `council-v0.4.0` and asserts that version. `Synthesis` at
that tag has no `raw_response`, no `review_status` and no `required_changes` — all
three were added in the merges since. Axis 2 is a string match on the chair's
recorded text and Axis 3 reads the JSON-parse branch; neither is observable at
v0.4.0. **The run therefore used council at HEAD** (0.5.0, commit `0269003`), which
is also what the live CI gate runs. Recorded in the evidence file as
`council.commit`.

**3. `review_status` is not on the gate path at all.** Case C's stated Axis 1
target is `review_status == "changes_requested"`. `REVIEW_SYNTH_OUTPUT` — the
system prompt `run_pr_review` sends the chair — never asks for that field. Only
the backlog runner asks, by appending an instruction to the same prompt
(`backlogrun/cli.py:702`). On the gate path `synthesize()` therefore falls through
to `"unknown"` for every model. It did, in all 36 cells. Case C is graded on the
chair's `blocking_findings` instead, which is what the design itself says two
paragraphs later ("the count is a tripwire; the actual grade ... reads which
findings were named").

**4. The gate cannot block Case B or Case C at all — no matter which chair sits.**
This is the finding worth the whole run. `decide_blocking` returns 0 unless the
panel first raised a *candidate* at the tier bar. `risk_tier` returns `"reduced"`
when every changed path is developer tooling, and at `"reduced"` the bar is
`critical` **and** confidence ≥ 8.

| fixture | changed paths | tier | panel candidates |
|---|---|---|--:|
| A `aris-pr1` | `src/pages/work.astro` | full | 1 |
| B `stw-pr11` | `tools/i18n/translate.mjs` | reduced | **0** |
| C `baw-pr11` | `.github/`, `scripts/`, `setup/`, `tests/` | reduced | **0** |

Neither B's nor C's panel contains a single `critical` finding, so
`candidate_findings` is empty and the gate returns 0 for every candidate. On
Case C that means **even a chair that correctly confirmed both real defects would
not have blocked build-ai-automation-workflow PR 11.** `run_pr_review` calls the
chair before computing the tier. The gate then discards its confirmed blocks
when no panel finding clears the tier threshold. The chair still under-called
the defects in this replay, but replacing it alone would not have changed the
gate outcome. This replay does not establish the complete historical cause of
the original merge.

Consequence for the rubric: Axis 1 is scored on the chair's own
`blocking_findings` list, with the gate's `decide_blocking` count recorded beside
it. Scoring the gate count would have given every candidate an identical 2 on B
and an identical 0 on C.

**5. The committed fixture `expected_blocking = 2` is unreachable.** Written as the
design specifies, because it records what the right answer would be, but
`harness.grade_records` can never pass that cell while the tier is `"reduced"`.
Flagged here so the next person does not chase it.

**6. Adding the fixture at the design's path broke the pinned regression.**
`run.py` listed `fixtures/` and asserted the result was exactly
`("aris-pr1", "stw-pr11")`; dropping `baw-pr11` in beside them failed its dry run
before a call. Fixed by loading the golden set **by name** (`_golden_fixtures()`),
so the two runners can share a fixtures directory without either changing shape.
`python3 tools/regress/run.py --dry-run` passes again: *"Dry run passed: 2
fixtures, 4 matrix cells, 0 API calls."*

**7. The transport hook did not exist, and needed one thing the design could not
know.** Written as `tools/budget_transport.py`. It refuses to send once the
process has spent past `BUDGET_TRANSPORT_CEILING_DIEM` (set to 4.00 for this run;
never hit). The part the design predates: `venice_usage.guard` now **refuses the
ledger row** for any client with an injected transport unless the caller passes
`transport_is_real=True`. Running the bake-off through the hook without that flag
would have produced a real bill and no record of it. The runner passes it.

**Free where the design budgeted 0.30 DIEM.** The design planned 8 member calls to
record panels for A and B. All three panels were instead recovered verbatim from
the saved `github-actions[bot]` comments — the same source the design already uses
for C — so **zero member calls were bought** and all three cases get identical
treatment. The recovery tool is committed as
`tools/regress/recover_panel.py` and it round-trips: re-running it on the saved
comments reproduces the committed panel JSON byte for byte. It is lossy in exactly
one way, stated in its docstring — `render_markdown` drops findings below
confidence 5 before rendering, so a sub-c5 nit cannot be recovered. Nothing below
c5 can clear any tier bar, so the gate arithmetic is unaffected.

Sources: `aris-pr1` comment `4794337893`, `stw-pr11` comment `4804745083`
(round 4 — the round whose bytes match the fixture), `baw-pr11` comment
`5308471957`.

**A note on the design's Case B prose.** swimtrack-website PR 11 ran four council
rounds. The design's Case B paragraph lists findings from rounds 2 and 4 together
("ROOT not declared, engines not pinned, ... and `process.loadEnvFile` breaks on
Node < 20.6"). Only round 4 (comment `4804745083`) matches the committed fixture's
bytes, and that is the panel replayed. The graded finding — Adversary `high` c9
plus `high` c8 on `process.loadEnvFile` — is in round 4, exactly as the design
says.

**Arithmetic.** The design's "Max 15 (3 fixtures x 4) + 3" does not add up from its
own axes: 3 fixtures x (Axis 1 0–2 + Axis 2 0–2) = 12, plus Axis 3's 0–2 = **14**.
Totals below are out of 14.

---

## 2. The result

36 chair calls, 3 fixtures x 4 candidates x 3 repeats, one replayed panel per
fixture, `temperature=0`, `chair_max_completion_tokens=8000` — the live gate's
own settings.

### Scores

Axis 1 = did it reach the right verdict (0–2 per case). Axis 2 = did it cite the
context that settles it (0–2 per case). Axis 3 = parse and shape discipline, once
per candidate (0–2). A cell showing a decimal varied between its three repeats;
the per-repeat table below shows how.

| chair | A1 A | A2 A | A1 B | A2 B | A1 C | A2 C | Axis 3 | total /14 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| `claude-opus-4-8` *(incumbent)* | 2 | 2 | 2 | 2 | 0 | 2 | 2 | **12** |
| `claude-sonnet-5` | 2 | 2 | 2 | 2 | 0.33 | 2 | 2 | **12.33** |
| `openai-gpt-56-sol` | 2 | 2 | 2 | 2 | **1** | 2 | 2 | **13** |
| `z-ai-glm-5-3` | 2 | 2 | 2 | 2 | 0 | 0 | 1 | **9** |

### Per repeat (Axis 1 / Axis 2)

| chair | case | r1 | r2 | r3 |
|---|---|---|---|---|
| `claude-opus-4-8` | A `aris-pr1` | 2/2 | 2/2 | 2/2 |
| `claude-opus-4-8` | B `stw-pr11` | 2/2 | 2/2 | 2/2 |
| `claude-opus-4-8` | **C `baw-pr11`** | 0/2 | 0/2 | 0/2 |
| `claude-sonnet-5` | A `aris-pr1` | 2/2 | 2/2 | 2/2 |
| `claude-sonnet-5` | B `stw-pr11` | 2/2 | 2/2 | 2/2 |
| `claude-sonnet-5` | **C `baw-pr11`** | 0/2 | **1/2** | 0/2 |
| `openai-gpt-56-sol` | A `aris-pr1` | 2/2 | 2/2 | 2/2 |
| `openai-gpt-56-sol` | B `stw-pr11` | 2/2 | 2/2 | 2/2 |
| `openai-gpt-56-sol` | **C `baw-pr11`** | **1/2** | **1/2** | **1/2** |
| `z-ai-glm-5-3` | A `aris-pr1` | 2/2 | 2/2 | 2/2 |
| `z-ai-glm-5-3` | B `stw-pr11` | 2/2 | 2/2 | 2/2 |
| `z-ai-glm-5-3` | **C `baw-pr11`** | 0/0 **err** | 0/0 **err** | 0/0 **err** |

### Cases A and B are degenerate, exactly as the design warned

All four candidates scored a clean 2/2 on both known-false cases in all three
repeats. Zero false blocks in 24 cells. Every one of them refuted the Adversary's
`high` c9 CLS claim by naming Astro's intrinsic-dimension inference, and every one
refuted the `process.loadEnvFile` claim by naming the `engines` pin. **A+B cannot
separate these models.** The design's instruction not to run the bake-off without
Case C was correct, and is now measured rather than argued.

### Axis 3

| chair | strict JSON, all cells | `blocking_findings` shape | no invented blocks | score |
|---|---|---|---|--:|
| `claude-opus-4-8` | yes | ok | none | 2 |
| `claude-sonnet-5` | yes | ok | none | 2 |
| `openai-gpt-56-sol` | yes | ok | none | 2 |
| `z-ai-glm-5-3` | **no** (3 empty responses) | ok | none | 1 |

No candidate ever needed `jsonparse.loads_lenient`'s fence or brace fallback: 33 of
36 responses parsed strictly on the first attempt, and the 3 that did not were
empty, not fenced. No candidate ever manufactured a block the panel had not raised.

---

## 3. Case C, quoted — the load-bearing case

Ground truth: both dissents were real. Both shipped as fixes in PR 12 on the same
day and both are named in `scripts/venice_review.py`'s docstring today — retry
covering 429/408 honouring `Retry-After`, and PATCH only when the comment is
marker-matched **and** authored by `github-actions[bot]`.

### `claude-opus-4-8` — incumbent. Declined both, 3 times out of 3.

Run 1, `recommendation`:

> Approve and merge. This is well-tested, defensively-written hardening of a CI
> shim; the panel's concerns are robustness/edge-case improvements (429 handling,
> comment authorship check, pagination) that are worth filing as follow-ups but
> none constitute a verified defect that guarantees failure under normal operation.

Run 1, on the author check:

> **Not blocking.** The MARKER is an HTML comment the workflow itself appends; a
> third party would have to deliberately craft a comment containing our internal
> marker to trigger this. It is a hardening suggestion (verify author before
> PATCH), not a real defect in normal operation. File as follow-up.

Run 1, on the retry:

> **Real robustness gap but not a guaranteed failure** — GITHUB_TOKEN in Actions
> has generous limits and calls are few. Worth a follow-up to add 429/Retry-After
> handling; does not block.

Run 2 raised the marker-ownership issue as a `user-challenge` disagreement and then
left its `resolution` field **empty** — it named the problem and said nothing about
it. Run 3 took 5.9 seconds, listed **no disagreements at all**, and returned
"Approve — merge. Solid hardening round with good test coverage; the remaining
concerns are robustness improvements, not verified defects."

`blocking_findings`: empty in all three runs.

This is the 2026-08-16 decision reproduced almost verbatim, on a replayed panel,
fourteen months of nothing having changed. It is also the best evidence that the
replay is faithful.

### `openai-gpt-56-sol` — blocked the author check, 3 times out of 3.

Every run returned `"request changes"` and one blocking finding.

Run 1:

> **[high] Public marker is trusted without comment ownership validation;
> `scripts/venice_review.py:109-129` and `setup/templates/venice_review.py:109-129`**
>
> *why:* `find_council_comment` accepts the first issue comment whose body contains
> the publicly known `<!-- council-review -->` marker and returns its ID without
> checking its author or app identity. `upsert_comment` then PATCHes that selected
> comment. A PR participant can seed such a comment before the workflow lookup,
> causing the action either to attempt modification of a comment it does not own
> and fail with 403, blocking the required gate, or to overwrite unrelated user
> content if the token is permitted to edit it. This is a concrete
> untrusted-input-to-privileged-API-target vulnerability introduced by the
> rolling-comment implementation.

And, resolving the disagreement the incumbent waved off:

> positions: The Eng Manager called for an author/ownership check; the Adversary
> suggested retrying 403 responses; the Security Officer offered no assessment.
> **chair's call: Blocking.** Retrying 403 does not address the trust-boundary
> defect: an arbitrary issue comment can select the PATCH target solely by
> embedding the public marker.

Run 2: *"Untrusted marker can hijack comment upsert; scripts/venice_review.py:111-130"*.
Run 3: *"Untrusted marker permits persistent comment-ownership CI denial of service"*.
Three runs, three blocks, the same defect, the same file and line range.

On the retry finding sol agreed with the incumbent — run 2: *"Confirmed reliability
defect, but non-blocking and medium severity: a 429 causes fail-closed behavior
rather than an incorrect merge decision."*

### `claude-sonnet-5` — blocked it once in three. Unstable.

Runs 1 and 3 returned a bare `"approve"` with no blocking findings. Run 1 raised the
marker issue as a `user-challenge` and, like the incumbent's run 2, left the
`resolution` **empty**.

Run 2 got it right, and got it right well:

> **[high] find_council_comment matches any comment containing the MARKER string
> with no author check; `scripts/venice_review.py:113-128` (and setup/templates copy)**
>
> *why:* Verified against the code ... On a public repo (or any repo where
> non-collaborators can comment on PRs), an external actor can post a comment
> containing the literal `<!-- council-review -->` string; the next gate run
> resolves that comment id, attempts to PATCH a comment it doesn't own, GitHub
> returns 403, and `raise_for_status()` throws an uncaught HTTPError — crashing the
> job and breaking the merge gate for that PR ... until someone manually deletes the
> spoofed comment. This is a concrete, low-effort, externally triggerable denial of
> service against the review gate itself.

One run in three is not a chair. The variance is the finding.

### `z-ai-glm-5-3` — returned nothing. 3 times out of 3.

```
run 1: chair_error='ValueError: empty or non-string response'  seconds=92.09   tokens_out=8000
run 2: chair_error='ValueError: empty or non-string response'  seconds=72.44   tokens_out=8000
run 3: chair_error='ValueError: empty or non-string response'  seconds=112.77  tokens_out=8000
```

Exactly 8,000 completion tokens each time — `chair_max_completion_tokens` — and an
empty content string. glm-5-3 spent the entire ceiling on reasoning and emitted no
JSON. It was already close on the small cases: 6,890 / 7,324 / 7,264 output tokens
on `aris-pr1`, 86–92% of the ceiling on an 18 KB prompt.

**On the CI gate this is not a low score, it is an outage.** `run_pr_review` sets
`unavailable = True` when `syn.error is not None`, and `ci_review.py` fails closed
on `unavailable`. Every PR the size of `baw-pr11` — 45 KB of chair prompt, which is
a *small* PR against the recorded maximum of 125,886 tokens — would have blocked
merges across all 18 repos. This is precisely the failure
`docs/output-caps-2026-09-11.md` names: the ceiling was sized from
`claude-opus-4-8`'s ledger history (largest chair completion ever, 5,174 tokens) and
does not fit a heavier reasoner.

### Nobody found the retry defect

All four candidates called the missing 429/`Retry-After` retry a non-blocking
robustness item. It was real, and it shipped as a fix the same day. **The best
available Axis 1 score on Case C was 1 out of 2, and no model earned the other
point.** Changing the chair does not fix this class of miss.

---

## 4. Cost

### What this run cost

Both columns below are the same number reached two ways: the capped transport
priced each response's `usage` block as it went, and the 36 ledger rows the calls
themselves wrote were repriced afterwards. They agree to four decimals.

| chair | calls | tokens in | tokens out | DIEM | DIEM/call | median s |
|---|--:|--:|--:|--:|--:|--:|
| `claude-opus-4-8` | 9 | 107,877 | 7,019 | 0.8578 | 0.0953 | 12 |
| `claude-sonnet-5` | 9 | 107,877 | 12,802 | 0.5157 | 0.0573 | 14 |
| `openai-gpt-56-sol` | 9 | 68,511 | 18,886 | 0.4074 | 0.0453 | 19 |
| `z-ai-glm-5-3` | 9 | 68,166 | 56,759 | 0.4315 | 0.0479 | 63 |
| **total** | **36** | **352,431** | **95,466** | **2.2123** | | |

Plus one 0.0264 DIEM smoke call (`z-ai-glm-5-3` on `stw-pr11`) made before the run
to prove the plumbing — real call, real ledger row, `strict` JSON — so nothing was
discovered 20 calls deep. **37 rows added to the production ledger, all
`project=council`, `task_type=review`, `source=council/venice`. Total attributable
spend 2.2387 DIEM against a 2.68 estimate, 16% under.** The budget transport's
4.00 DIEM ceiling was never approached and it refused nothing.

The estimate ran high for the reason the design predicted (3.0 chars/token is
pessimistic) and for one it did not: **the two families tokenise the same bytes very
differently.**

| chair | prompt bytes sent | billed prompt tokens | bytes/token |
|---|--:|--:|--:|
| `claude-opus-4-8` | 231,426 | 107,877 | 2.15 |
| `claude-sonnet-5` | 231,426 | 107,877 | 2.15 |
| `openai-gpt-56-sol` | 231,426 | 68,511 | 3.38 |
| `z-ai-glm-5-3` | 231,426 | 68,166 | 3.40 |

Identical prompts, and the Claude family bills **57% more input tokens** than the
other two. That is a real cost difference on top of the published per-token rate,
and it is invisible to any projection made from byte counts — including the design's.

### What the weekly chair line becomes

Repriced from the trailing 7 days of real chair calls in
`~/.local/state/venice-usage/ledger.db` (4,952,505 prompt tokens, 201,383 completion
tokens), at today's catalogue rates, excluding this run's own rows.

| chair | in/M | out/M | chair DIEM/week | council DIEM/week | saving/week | saving/year |
|---|--:|--:|--:|--:|--:|--:|
| `claude-opus-4-8` *(now)* | 6.000 | 30.000 | **35.76** | 67.02 | — | — |
| `claude-sonnet-5` | 3.000 | 15.000 | 17.88 | 49.14 | 17.88 | ~930 |
| `openai-gpt-56-sol` | 2.500 | 12.500 | **14.90** | 46.16 | **20.86** | **~1,085** |
| `z-ai-glm-5-3` | 1.750 | 5.500 | 9.77 | 41.04 | 25.98 | ~1,351 |

The chair is **53.4%** of council's weekly spend, confirming the figure the run was
commissioned on. That week is 123 chair calls. Note the ledger's own
stored `usd` column values the same 123 rows at 86.47 DIEM — 2.4x the repriced
figure, because that column is historical and those rows were written under the old
seed prices (`claude-opus-4-8` at 15/75). Every figure in this document is repriced
through `venice_usage.pricing`, which is generated from the live catalogue.

---

## 5. Recommendation

**Quality: `openai-gpt-56-sol` is the only candidate that beat the incumbent on the
only case with a known-right answer, and it beat it every single time.**

- On the two known-false cases, sol matched the incumbent perfectly: zero false
  blocks in six runs, each grounded in the specific context that refutes the claim.
  A false block on the gate stops merges across 18 repos, and sol never produced one.
- On Case C, sol confirmed the marker-ownership defect as blocking in 3 of 3 runs and
  returned `"request changes"` in 3 of 3. The incumbent approved in 3 of 3.
  `claude-sonnet-5` managed it in 1 of 3, which is worse than useless on a gate: a
  chair that finds a real defect a third of the time teaches the operator to ignore it.
- `z-ai-glm-5-3` is disqualified on a hard failure, not a soft score. It returned an
  empty response on all three runs of the 45 KB case, hitting the 8,000-token chair
  ceiling. On the live gate that is `unavailable=True` and a fail-closed merge block.
  It cleared A and B cleanly, so this is a ceiling problem, not a reasoning problem:
  it would need `chair_max_completion_tokens` raised — with new ledger evidence, per
  that setting's own comment — before it could be considered, and the 45 KB case
  that broke it is small.

**Cost, second.** sol is 14.90 DIEM/week against the incumbent's 35.76 — a 20.86/week
saving, about 1,085 a year, and it takes the chair from 53.4% of council's spend to
32.3%. It is also cheaper than `claude-sonnet-5`, the only other candidate that
clears the design's bar, so cost does not have to arbitrate a close quality call.
Latency is the one place sol is worse: 19s median against the incumbent's 12s, and
72–90s on the 45 KB case against the incumbent's 6–16s. On a CI gate that is
immaterial.

**The confound, stated plainly.** The design flagged that sol shares a family with
the Eng Manager seat (`openai-gpt-53-codex`) and warned to watch for it siding with
its cousin. On Case C the winning answer *was* the Eng Manager's finding, and sol
adopted it three times out of three. This run cannot separate "sol arbitrates
better" from "sol agrees with its own family". Two things argue against the cynical
reading — sol dropped the Eng Manager's other findings (pagination, the c7 429 point)
as non-blocking, and on A and B it refuted `high`-confidence claims from the
Adversary rather than deferring to any seat — but neither is proof. **The cheapest
way to settle it is a fourth graded case whose known-right answer comes from a seat
other than the Eng Manager.** That is a real gap and it is the reason this document
recommends rather than decides.

**What is not recommended.** Do not read this as a mandate for
`[settings] chair_model`. It tests the chair on code review only; `decision`,
`brainstorm`, `spec-review` and `red-team` share the same global value and none was
exercised. If the answer differs by panel, the config already has the shape for a
per-panel `chair_model`, exactly as `chair_max_completion_tokens` resolves today.

---

## 6. The finding that outranks the chair choice

**On developer-tooling PRs the merge gate cannot block without a confident
`critical` panel finding, even when the chair confirms a real `high`.**

`baw-pr11` shipped two real bugs. The incumbent chair under-called both — that is
measured above, three times over. After calling the chair, `run_pr_review`
computes `risk_tier`. For that PR it returns `"reduced"`, whose bar is `critical`
with confidence ≥ 8. The panel's best was `high` c9, so `candidate_findings`
was empty and `decide_blocking` returned 0 regardless of the chair's answer.
The chair call still incurs cost and its answer appears in the review comment.
**When `openai-gpt-56-sol` correctly
blocked in this run, the gate still returned `blocking = 0`.** Every Case C cell in
the evidence file shows `gate_blocking: 0`, including sol's three correct blocks and
sonnet's one.

So: replacing the chair improves the *review comment* on dev-tooling PRs. It does not
change the *gate outcome* on these two reduced-tier fixtures. A tooling change
with an eligible critical finding can still be blocked. `scripts/`, `tools/`,
`tests/`, `setup/`, and `.github/` include the CI shim and workflow configuration.
Council and the backlog runner also have top-level `council/` and `backlogrun/`
packages; those paths do not automatically receive the reduced tier. Whether that tier
rule is right is a separate decision from the chair, it is worth more than the chair,
and nothing here changes it either.

---

## 7. What this run did not settle

- **The retry half of Case C.** No candidate blocked on it. Best score available was
  1 of 2 and nobody got the second point.
- **Whether sol's Case C win is independence or family agreement.** Needs a fourth
  fixture whose right answer comes from a non-OpenAI seat.
- **Long context.** The largest chair prompt here is 45 KB / ~12.6k tokens against a
  recorded maximum of 125,886. `z-ai-glm-5-3` already broke at 12.6k. Nobody tested
  the survivors at 10x that.
- **Panels other than code-review.** Not exercised. See §5.
- **The ungraded PR 9 round-1 probe.** Not run — the design costed 3 fixtures, not 4,
  and it was never in the 36-call budget.
- **Effect size.** 3 fixtures, 3 rollouts. This detects; it does not estimate. Same
  claim class as `docs/council-regression-2026-07-23.md`.

---

## 8. Reproducing this

```bash
cd ~/projects/build-ai-automation-workflow
set -a; . ~/.env; set +a

# free: fixture hashes, context construction, matrix shape, pessimistic pricing
python3 tools/regress/bakeoff.py --dry-run --repeats 3 \
  --chair-model claude-opus-4-8 --chair-model claude-sonnet-5 \
  --chair-model openai-gpt-56-sol --chair-model z-ai-glm-5-3 \
  --output /tmp/bakeoff-dry.json

# paid: 36 chair calls, no panel calls, hard-capped
export BUDGET_TRANSPORT_CEILING_DIEM=4.00
export BUDGET_TRANSPORT_LOG=/tmp/bakeoff-spend.jsonl
python3 tools/regress/bakeoff.py --paid --estimate-diem 2.72 --repeats 3 \
  --chair-model claude-opus-4-8 --chair-model claude-sonnet-5 \
  --chair-model openai-gpt-56-sol --chair-model z-ai-glm-5-3 \
  --transport-hook "$PWD/tools/budget_transport.py:post" \
  --output tools/regress/results/chair-bakeoff-$(date -u +%Y%m%dT%H%M%SZ).json

python3 tools/regress/bakeoff_grade.py tools/regress/results/chair-bakeoff-*.json
```

The pinned v0.4.0 regression is unaffected and still passes:
`python3 tools/regress/run.py --dry-run` → *"Dry run passed: 2 fixtures, 4 matrix
cells, 0 API calls."*
