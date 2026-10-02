# council v0.4.0 golden-set regression check — 2026-07-23

**Claim class: REGRESSION DETECTION, not effect estimation.** Single stochastic
rollouts against known-answer fixtures. This answers *"do the five cases we know
the answer to still fail?"* — it does **not** answer *"is chair arbitration better
than the simpler design?"* (that would need the matched-worlds apparatus in
`harness-engineering-assessment-2026-07-20.md` §7 Correction 1).

**Why.** council v0.4.0 (chair arbitration + S1 full-file-context grounding) was
built specifically to kill a false-positive blocking class — and was never re-run
against the five labeled cases that motivated it (assessment §7: "we believe the
fix works because the design sounds right"). This closes that loop. Backlog item:
`2026-07-23-council-v040-regression-check`.

## Fixtures

Both PRs merged; both ground-truth **blocking = 0** (every finding council raised
against them was refuted against the real tree — assessment §7 table, audit AC1).

| Fixture | Diff | Repo checkout for grounding |
|---|---|---|
| `aris-pr1` | `gh pr diff 1` in aris-management-website (5.2 KB) | worktree @ head `0595eaf9` |
| `stw-pr11` | `gh pr diff 11` in swimtrack-website (1.5 KB) | worktree @ head `eba8b540` |

Golden findings (all ground-truth false): aris — missing `width`/`height`, no
WebP; swimtrack-website — `ROOT` not declared, `engines` not pinned, `.env` not
gitignored, plus the audit's fourth named case, `process.loadEnvFile` breaks on
Node < 20.6 (refuted by `package.json` `engines >= 22.12.0`).

## Arms

Same fixtures through `council.review.run_pr_review`, chair `claude-opus-4-8`
(identical in both versions), graded programmatically on the returned `blocking`
count — no LLM judge.

| Arm | Version | file_context | Represents |
|---|---|---|---|
| v020 | council-v0.2.0 (tag, fresh venv) | n/a (API predates it) | historical baseline; **monthly-bidding still pins this** |
| noctx | council 0.4.0 (live pipx) | none | **as deployed on aris** (stale shim — see Drift) |
| ctx | council 0.4.0 (live pipx) | swimtrack-shim gather (40 KB/file, 160 KB cap, anchors `package.json`/`.gitignore`/`.nvmrc`) | as designed (audit S1); as deployed on swimtrack-website |

## Results

| Fixture | v0.2.0 | v0.4.0 no-ctx | v0.4.0 + ctx | Expected |
|---|---:|---:|---:|---:|
| aris-pr1 | **1 blocking** | 0 | 0 | 0 |
| stw-pr11 | **2 blocking** | 0 | 0 | 0 |
| unavailable (fail-closed) | none | none | none | none |

**v0.4.0 PASSES the golden set — 0 blocking on both fixtures, in both deployed
configurations. v0.2.0 still fails it — 3 blocking findings, every one from the
audit's named false-positive set:**

- v0.2.0 / aris: blocked on *missing `width`/`height` → CLS* (`high` c9) — golden
  case #1 verbatim.
- v0.2.0 / stw: blocked twice on *`loadEnvFile` fails on Node < 20.6* (`high`
  c8 + c9) — the audit's named case, refuted by the engines pin the reviewer
  never saw.

Finding-level behavior of the fix, visible in the bodies:

- **no-ctx:** panelists still *raise* the same false findings (width/height at
  c9, loadEnvFile at c8/c9) — the **chair declines to confirm them as blocking**
  ("Valid maintainability suggestion, not a defect. Non-blocking."). Arbitration
  alone already clears the golden set.
- **ctx:** on stw-pr11 the false findings **disappear entirely** — grounding
  kills them at the panel, not just at the chair.

## Drift finding (independent of the API runs)

**The S1 grounding fix never propagated to aris.**
`aris-management-website/scripts/venice_review.py` calls `run_pr_review` without
`file_context`; `swimtrack-website/scripts/venice_review.py` gathers and passes
it. Same failure class as the monthly-bidding pin: a fix shipped without
verifying propagation. The no-ctx arm exists precisely to measure this deployed
configuration — it passes today, but only on the strength of chair arbitration.

## Recommendations

1. **monthly-bidding: bump the CI pin `council-v0.2.0` → `council-v0.4.0`.** The
   pinned version demonstrably still produces the false-positive blocking class;
   the current version demonstrably does not (on the known cases).
2. **aris: port `gather_file_context` from swimtrack-website's shim.** Defense in
   depth — arbitration passes today, grounding removes the false findings rather
   than out-voting them. Audit all 17 `venice-review.yml` repos' shims while
   there: version-pin *and* shim shape.

## Limits

- n=1 per cell. Detects regression on known cases; estimates nothing. The three
  golden findings that did not recur this run (WebP, ROOT, `.env`) are consistent
  with pass but individually unexercised — the assertion that holds is
  "expected blocking contribution of the golden set = 0", which v0.4.0 met on
  both fixtures.
- **AC2 (true positives still block) was NOT run** — this suite contains only
  known-false cases. A config that passes here by blocking nothing at all would
  sail through; AC2 fixtures (PR #9 round-1 View-Transitions breakage) are the
  guard and remain unbuilt.
- New non-blocking `high` on aris/ctx ("sharp doesn't run in Workers →
  optimization no-op") is factually dubious (the deployed site's images are
  transformed — assessment §7) but did not block, so it is outside this check's
  claim. Noted for any future effect-estimation work.

## Reproduction

The durable harness, fixture provenance, commands, and future JSON evidence
format live in [`tools/regress/`](../tools/regress/README.md). Its `--dry-run`
validates the committed fixed-head fixtures with zero API calls; a live run
requires `--paid`, installs the pinned `council-v0.4.0` tag in a fresh venv, and
runs both fixtures with and without full-file context. The original session
scratchpad and its response bodies were reaped; the result table above is the
surviving record of that 2026-07-23 run. The historical estimate for the four
v0.4.0 cells remains 16 API calls and about 0.72 DIEM.
