# Can a Jev pre-filter gate the clarity read?

Measured replay against the real ledger of `clarity_scan` calls in
`/home/dev/projects/romance-empire`. Nothing in that repo was changed.

**Verdict: no. There is no threshold that skips a worthwhile share of chapters with
near-zero real-defect misses.** The best in-sample safe setting skips 5.3% of chapters and
saves 3.9% of the $46.28, and that setting does not survive a split-half test. At the
thresholds the brief asked about (0.05, 0.1, 0.2, 0.3) the gate skips **nothing at all**,
because Jev never returns a probability that low on this material.

---

## 1. The evaluation set

| | |
|---|--:|
| `clarity_scan` calls in `manuscripts/*/usage-log.csv` | 404 |
| Total cost of those calls | $46.28 |
| Calls rebuilt into evaluation rows | 357 |
| Evaluation rows (after de-duplicating identical prompts) | **356** |
| Cost those rows represent | $40.78 (88% of $46.28) |
| Books | 8 |
| Median chapter length | 2,408 words |

**Matching is exact, not approximate.** The ledger records `prompt_sha256` =
`sha256(system + "\n\n" + user)`. The `CLARITY_READER` persona has exactly one version in
git history (added 2026-07-14, never edited), so the system prompt is a constant; the only
change was the user-prompt format (the 2026-07-14 round read `edited-v2/chapter-NN-proofread.md`
and wrote `CHAPTER: {n}`, later rounds split `edited.md` and wrote `CHAPTER: Epilogue|{n}`).
Rebuilding candidate prompts from every git blob of `edited.md`, every `edited.md.bak-*`,
and every per-chapter file, then hashing, reproduces the logged hash for 371 of the 404
calls. **No pair is uncertain: a row is either an exact hash match or excluded.**

Flags come from the `clarity-report.json` version that round wrote, recovered from git
(the file is overwritten on every run; two extra versions survived only because the
`session-gc` WIP cron snapshotted them).

### The 48 calls not in the set

| Reason | Calls |
|---|--:|
| open-water 2026-07-13: the dev run the day before the persona was committed. No prompt reproduces and no report version exists. | 26 |
| high-tide 2026-09-10: 15 tiny re-scans whose result was overwritten before any commit caught it. | 15 |
| high-tide 2026-08-01: report exists, the exact pre-fix text version does not. | 6 |
| Duplicate calls on a byte-identical prompt (folded into one row). | 1 |

**One exclusion is not random and matters.** The 6 high-tide 2026-08-01 chapters whose text
is unrecoverable are chapters 6, 14, 15, 20, 22, 24 — and five of those six are exactly the
chapters where that round's fixes were applied. Their text was overwritten by the fix before
anything committed it. So high-tide round 2 contributes clean chapters to the set and loses
most of its defective ones. That biases the measurement in the gate's **favour**, not against it.

### Flag distribution

| Flags raised by the expensive reader | Chapters | Share |
|---|--:|--:|
| 0 | 150 | 42.1% |
| 1 | 139 | 39.0% |
| 2 | 62 | 17.4% |
| 3 | 5 | 1.4% |
| 4+ | **0** | 0% |

Total 278 flags. **No chapter in the whole catalogue ever drew 4 or more flags**, so the
"4+ vs 0" separation the brief asked for has to be read as "2-3 vs 0".

---

## 2. Level 2 — how much of Level 1 is a real defect

The repo records its own dispositions, chapter by chapter, for every round:

| Round | Source | Flags | Judged real |
|---|---|--:|--:|
| 2026-07-14 (all 8 books) | `clarity-tier1.md` + `clarity-tier2-taste-read.md` | 131 | 17 |
| 2026-07-31 still-waters | `rebuild-work-order.md` WO-4 | 18 | 9 |
| 2026-07-31 saltbox | `close-look-2026-08-29.md` | 18 | 0 (2 taste calls) |
| 2026-08-01 high-tide / undertow | each book's `texture-adjudication.md` | 10 / 22 | 5 / 6 |
| 2026-08-01 OW / FS / OOR / FSnow | `docs/clarity-reaudit-2026-08-01.md` | 83 | 13 |

Anchored against the rebuilt flags, that gives **45 real flags of 278 (16%)**, sitting in
**43 of the 206 flagged chapters**. Five of the 2026-07-14 Tier-1 items turned out to come
from the excluded 2026-07-13 deep-dive run, not from the catalogue scan, and were dropped.

**Independent check (done before any Jev call).** I drew 30 flags at random (seed 20260919),
read each quoted sentence in +/-400 words of its own chapter, and judged it myself:
**9 of 30 (30%) are real reader-stumbles**, against the repo's 16%. Four of my nine match
chapters the repo also calls real; five are ones the repo kept as house voice — including
one (`false-start` ch19, "the same two syllables ... *good morning*") that the round-1 triage
kept and the round-2 re-audit then fixed. So the repo's Level-2 label is a **lower bound**.
Misses counted against it understate the real risk.

Recorded in `flag_adjudication.json`.

---

## 3. What Jev actually returned

Question wording was written to `questions_wording.json` before the first API call and not
touched afterwards. Design A: whole chapter as state, one Noul plus a 4-level Score.
Design B: the chapter cut into consecutive ~450-word windows on paragraph boundaries
(5.4 windows per chapter, 1,919 windows in all), the same Noul per window, chapter score =
max window probability.

| | min | median | max |
|---|--:|--:|--:|
| Design A Noul P(at least one break) | 0.33 | 0.46 | 0.71 |
| Design B max window Noul | 0.37 | 0.55 | 0.83 |
| Design A Score (0 = several breaks ... 3 = perfectly clean) | 1.45 | 1.98 | 2.33 |

**Jev never says a chapter is clean.** It never returns a probability below 0.33, and its
score never reaches level 3 on any of the 356 chapters — including the 150 the expensive
reader passed with zero flags. Run cost: $0.060 for design A, $0.098 for design B, 90
seconds in total. Cost is not the obstacle here.

### Separation

| Measure | Design A Noul | Design A Score | Design B max | Design B mean |
|---|--:|--:|--:|--:|
| Mean, 0-flag chapters | 0.456 | 2.011 | 0.534 | 0.433 |
| Mean, 1-flag chapters | 0.469 | 1.977 | 0.554 | 0.449 |
| Mean, 2-3-flag chapters | 0.493 | 1.936 | 0.593 | 0.469 |
| Spearman rho vs flag count | 0.204 | -0.195 | 0.207 | 0.206 |
| AUC, Level 1 (>=1 flag) | 0.598 | 0.600 | 0.596 | 0.605 |
| AUC, **Level 2 (>=1 real defect)** | **0.570** | 0.512 | **0.533** | 0.504 |

The direction is right and the gap is tiny: 0.534 vs 0.593 on design B, with standard
deviations of 0.088 and 0.098. **39 of the 150 zero-flag chapters score above the mean of
the 2-3-flag chapters.** On the thing that matters — does this chapter hold a real defect —
both designs are at or near chance (0.50-0.57).

Length is not driving it (Spearman vs word count: -0.08 for both Nouls).

---

## 4. The gate at each threshold (skip when the probability is below it)

**Design A — whole chapter, Noul** (206 chapters have >=1 flag; 43 have >=1 real defect)

| Threshold | Skipped | >=1-flag missed | Real-defect missed | Flags lost | $ saved |
|--:|--:|--:|--:|--:|--:|
| 0.05 | 0 (0%) | 0 | 0 | 0 | $0.00 |
| 0.10 | 0 (0%) | 0 | 0 | 0 | $0.00 |
| 0.20 | 0 (0%) | 0 | 0 | 0 | $0.00 |
| 0.30 | 0 (0%) | 0 | 0 | 0 | $0.00 |
| 0.50 | 238 (66.9%) | 125 (60.7%) | 22 (51.2%) | 158 (56.8%) | $25.46 |

**Design B — window max** (same denominators)

| Threshold | Skipped | >=1-flag missed | Real-defect missed | Flags lost | $ saved |
|--:|--:|--:|--:|--:|--:|
| 0.05 | 0 (0%) | 0 | 0 | 0 | $0.00 |
| 0.10 | 0 (0%) | 0 | 0 | 0 | $0.00 |
| 0.20 | 0 (0%) | 0 | 0 | 0 | $0.00 |
| 0.30 | 0 (0%) | 0 | 0 | 0 | $0.00 |
| 0.50 | 107 (30.1%) | 50 (24.3%) | 11 (25.6%) | 60 (21.6%) | $11.30 |

The five requested thresholds are useless here — four skip nothing and the fifth throws away
a quarter to a half of the real defects. The interesting range is between them:

**Design B, finer sweep**

| Threshold | Skipped | >=1-flag missed | Real-defect missed | Flags lost | $ saved |
|--:|--:|--:|--:|--:|--:|
| 0.38 | 1 (0.3%) | 0 | 0 | 0 | $0.07 |
| 0.40 | 8 (2.2%) | 5 | 0 | 6 | $0.75 |
| **0.42** | **19 (5.3%)** | **8 (3.9%)** | **0** | **9 (3.2%)** | **$1.79** |
| 0.45 | 45 (12.6%) | 20 (9.7%) | 5 (11.6%) | 23 (8.3%) | $4.33 |
| 0.48 | 88 (24.7%) | 42 (20.4%) | 8 (18.6%) | 50 (18.0%) | $9.11 |
| 0.50 | 107 (30.1%) | 50 (24.3%) | 11 (25.6%) | 60 (21.6%) | $11.30 |

Design A's equivalent zero-real-defect point is 0.34: **2 chapters skipped (0.6%), $0.15**.

### The 0.42 point does not survive a split-half test

Choosing the threshold on a random half and reporting on the other half:

| Design | Threshold chosen on dev half (largest with 0 real-defect misses) | Dev | Holdout |
|---|--:|---|---|
| A Noul | 0.39 | 10.1% skipped, 0 real-defect missed | 11.8% skipped, **2 real-defect missed**, 14 flags lost |
| B max | 0.44 | 7.3% skipped, 0 real-defect missed | 10.1% skipped, **1 real-defect missed**, 5 flags lost |

So the apparently safe point is an artefact of fitting on all the data. On unseen chapters,
a setting that skips ~10% leaks real defects.

### Per book

| Book | Rows | >=1 flag | Real defect | Mean B max | Within-book AUC (L1) | At B max < 0.42: skipped / L1 missed / real missed |
|---|--:|--:|--:|--:|--:|---|
| false-start | 52 | 30 | 7 | 0.486 | 0.61 | 9 / 2 / 0 |
| first-snow | 26 | 17 | 4 | 0.589 | 0.53 | 0 / 0 / 0 |
| high-tide | 48 | 14 | 3 | 0.527 | 0.62 | 3 / 0 / 0 |
| open-water | 50 | 31 | 6 | 0.629 | 0.58 | 0 / 0 / 0 |
| out-of-reach | 50 | 37 | 3 | 0.619 | 0.43 | 0 / 0 / 0 |
| saltbox-season | 26 | 23 | 1 | 0.606 | 0.34 | 0 / 0 / 0 |
| still-waters | 52 | 28 | 11 | 0.497 | 0.46 | 5 / 4 / 0 |
| undertow | 52 | 26 | 8 | 0.517 | 0.56 | 2 / 2 / 0 |

### The mechanism: Jev is reading the book, not the chapter

This is the clearest result in the study.

* Across the 8 books, the book's mean Jev probability tracks the book's flag rate well:
  Spearman **0.83** (design A) and **0.60** (design B) against the share of flagged chapters,
  **0.67** (design B) against flags per 10k words. Saltbox (5.4 flags/10k) and Out of Reach
  (4.5) sit at the top; High Tide (1.3) and Still Waters (2.7) at the bottom. Jev gets that
  ordering broadly right.
* Within a book, it is chance: mean within-book AUC for Level 1 is **0.518** (design B) and
  **0.476** (design A); for Level 2, **0.560** (design B).

Almost all of the pooled AUC of 0.60 is between-book variation. Jev is picking up how
compressed and elliptical a book's register is — which is a real property of the prose, and
the same property the human adjudicators repeatedly called "house voice, leave it". It is not
picking up "this chapter contains a sentence that breaks".

That also explains the per-book skip pattern at 0.42: every skipped chapter comes from the
four lowest-register books, and none from the four densest ones — even though the densest
books hold the most flags. A gate like that does not target defects; it targets style.

---

## 5. Design B localisation — can Jev point at the right part of a chapter?

For each flagged sentence, is the window containing that quote ranked highly among that
chapter's windows?

| | |
|---|--:|
| Flags whose quote was located in a window | 271 of 278 |
| Mean windows per chapter | 5.39 |
| Flag's window ranked **1st** | 25.8% (chance 19.0%) |
| Flag's window ranked 1st or 2nd | 44.3% (chance ~37%) |
| Mean rank of the flag's window | 2.86 of 5.39 |
| Mean percentile of the flag's window | 0.579 (chance 0.50) |

A small, real, but weak effect: about 7 percentage points above chance on top-1. Directing
the expensive reader at the top two windows of every chapter would look at ~37% of the text
and find ~44% of the flags — barely better than reading 37% of the chapter at random. This
is a different plug-in from whole-chapter skipping, and it is also not good enough.

---

## 6. The money

* The catalogue's 404 `clarity_scan` calls cost **$46.28**, about $0.115 per chapter.
* The best threshold with zero real-defect misses **on the full set** (design B, 0.42) skips
  19 of 356 chapters and would have saved **$1.79 — 3.9% of $46.28**. It also drops 8 chapters
  the expensive reader had flagged, 9 flags in total.
* Chosen honestly on a held-out half, that same kind of setting **leaks real defects** while
  saving about a tenth of the spend.
* Running the gate itself costs about **$0.0002 per chapter** ($0.16 for the whole 356-chapter
  replay across both designs, 90 seconds). Jev is cheap and fast. It just does not know the answer.

**So the share of $46.28 the best safe threshold would have saved is 3.9%, and that number
does not survive out-of-sample validation. Treat the honest answer as ~0%.**

---

## 7. Limits

1. **Level-2 truth is the operation's own record, not an independent panel.** It was produced
   by the same AI-assisted pipeline (with Rex gating some of it). My own 30-flag sample put
   the real rate at 30% against the record's 16%, so the record under-labels. Every
   "real-defect missed" number here is therefore an undercount.
2. **One exclusion is biased toward the gate.** Five of the six high-tide chapters dropped for
   unrecoverable text are the five where that round's fixes landed. The set keeps high-tide's
   clean chapters and loses its defective ones.
3. **The two scan rounds are not independent chapters.** 179 rows come from 2026-07-14 and 177
   from the later round; for an unchanged chapter, the two rows are near-duplicate prose (the
   prompts differ because the text source changed, so every hash is distinct, but the sentences
   often are not). The effective sample is closer to ~180 distinct chapters than 356. Scored on
   the later round alone the picture is the same: AUC(L1) 0.61 (A) / 0.64 (B), AUC(L2) 0.56 / 0.54.
4. **Ground truth is one non-deterministic reader's output, not a gold standard.** `openai-gpt-54`
   with reasoning on was run once per chapter. The repo's own adjudications disagree with each
   other across rounds (open-water ch8 was "the model got it wrong" in July and a real fix in
   August; false-start ch19 the same). A gate evaluated against a noisy oracle can only look worse
   than it is — but the miss rates here are far too large for that to rescue it.
5. **No chapter ever drew 4+ flags**, so the requested 0-vs-4+ contrast could not be run. The
   real contrast is 0 vs 2-3, where the means differ by 0.06 on a 0-1 scale.
6. **One question wording, one window size, one model version** (`jev-1.13.0`). The wording was
   frozen before the first call and derived from the persona contract, so it is a fair test of
   *that* wording — not proof that no wording could work. A negative at this effect size
   (within-book AUC ~ 0.52) is unlikely to be rescued by rephrasing, but it has not been ruled out.
7. **Epilogues are keyed chapter 0** throughout, matching the pipeline's own convention.
8. **Chapter text is deliberately absent from `results.json`.** It lives only in the local
   `dataset.json`, which stays in the scratchpad.

## Files

| File | What it is |
|---|---|
| `questions_wording.json` | The frozen Jev questions, written before the first call |
| `build_dataset.py` | Rebuilds the 356-row set by exact prompt-hash matching (READ-ONLY on the repo) |
| `build_adjudication.py` | Anchors the repo's disposition records to individual flags |
| `flag_adjudication.json` | Level-2 labels + my independent 30-flag sample |
| `run.py` | Both Jev designs |
| `score.py` | Thresholds, separation, per-book, split-half, localisation |
| `results.json` | Per row: book, chapter, flags, both designs' scores. No chapter text. |
| `dataset.json`, `jev_a.json`, `jev_b.json`, `excluded.json` | Working files |
