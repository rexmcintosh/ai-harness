# Jev replays, 2026-09-19: five places, five answers

Each replay asks one question: on history where the answer is already known, would Jev as
a plug-in have helped? Wording was fixed before any result was seen. Model `jev-1.13.0`.
Scripts, frozen wording, raw results and the full reports are in
`docs/evidence/jev-replays-2026-09-19/` (and `ultimate-portugal/docs/evidence/` for the
first one). Manuscript text and backlog prompts are not stored there.

This file describes results. What to do about them is in
`jev-first-actions-DRAFT-2026-09-18.md`.

| Place | Plug-in shape | Sample | Result | Verdict |
|---|---|---|---|---|
| Ultimate Portugal story judge | gate before Opus | 3,312 judged stories | 48% filtered, 0 of 23 used and 10 of 456 held lost | **Works. Live since 2026-09-19** |
| Backlog night runner | hold-only gate before a Sonnet session | 157 items with outcomes | "needs an outward action": AUC 0.95; at 0.7 recall 0.89, precision 0.85 | **Works for one question of three** |
| SAT question bank | blind-solve check | 767 questions | 91% right; 100% right when confidence is 0.95 or more (69% of questions); 0 real content bugs found | **Not as designed. A narrower use holds** |
| Clarity read (books) | gate before the expensive reader | 356 chapter scans | AUC 0.60; inside one book 0.52, which is chance | **Does not work** |
| SwimTrack editorial | scope gate, pillar label, ranker | 2 real decisions, 45 fresh items | too little history | **Not measurable yet** |

## 1. Ultimate Portugal story pre-filter

See `ultimate-portugal/docs/evidence/jev-prefilter-2026-09-19/`. One yes/no question per
story. At line 0.1 with community threads passed through, 48% of stories never reach the
judge and none of the 23 used stories is among them. The line held on a month not used to
choose it. Portuguese and English behaved alike.

Why it works: the junk is obvious from a headline (football, celebrity, crime, foreign
news), and that is a judgement Jev makes well.

## 2. Backlog night runner

157 items with a recorded outcome. Three questions were tested.

- **A, "does finishing this need an outward or irreversible action" works.** AUC 0.947
  against blind labels (53 yes, 104 no). At 0.7: 47 of 53 caught, 8 false holds. The
  score behaves like a step: under 0.7 only 5 to 10% of items are truly outward, over 0.9
  all of them are. 0.5 adds 2 true holds and 19 false ones.
- **B, "is the prompt self-contained" fails.** Jev scored 84 of 157 at 0.3 or less while
  124 are runnable cold. It is not usable.
- **C, "will it finish in one pass"** separates the labels well (AUC 0.95) but the real
  sessions only weakly.
- The money is small. Only 32 items ever cost a session ($100 in all, $3.13 each), and 56
  of 67 held items were held before any session ran. Unambiguously wasted spend in all of
  history is about $0.68 and one timeout.
- **The safety finding is the point.** Of 5 accepted sessions the gate would have held, 3
  carried an instruction that README safety rule 2 forbids: write rows to a live database,
  run a paid model extraction, `git push origin master`. Nothing screens for that today.
- All 6 missed holds share one shape: the prompt tells the agent not to do the outward
  step, and Jev scores what the agent is allowed to do, not what the item needs to be
  done. The house merge boilerplate causes 6 of the 8 false holds. Both are wording
  problems, not yet re-tested.
- Jev's date blindness showed up: an item marked "do not run before 2026-09-22" scored 0.07.
- The whole screen of 157 items cost one cent and took 12 seconds.

## 3. SAT question bank

767 multiple-choice questions from 19 batches (77 numeric-answer questions left out).

- 91.4% agreement with the answer key. Reading and writing 92.7%. Math 88.3%, or 95.8%
  without the 41 math questions that need a figure Jev cannot see (those: 53.7%).
- Calibration is clean: at confidence 0.95 or more, 530 of 767 questions, every answer
  matched the key.
- One repeatable weak spot: punctuation at clause boundaries, 68% right. Jev prefers "no
  punctuation" when a comma or semicolon is correct.
- 32 disagreements and low-confidence cases were read by hand. **None was a mis-keyed or
  ambiguous question.** In this bank a disagreement means Jev is wrong.
- So Jev must not replace the blind-solve gate as it stands: that gate deletes a question
  on disagreement, and Jev would delete good questions.
- What does hold: when Jev agrees with the key at 0.95 or more (69% of the bank), the
  strong solver has nothing to add in this sample. That is a gate that would cut the
  strong solver's work by about two thirds. It has not been tested on a batch with
  planted errors, which is the test that would show it can catch a bad key.

## 4. Clarity read

356 of the 404 paid scans were rebuilt exactly (prompt hashes match the cost ledger).
150 chapters drew no flag, 139 one, 67 two or three, none four or more. The repo's own
records mark 45 of 278 flags as real defects; a blind read of 30 flags put it nearer 30%.

- Jev never calls a chapter clean. Its answers run 0.33 to 0.71 (whole chapter) and 0.37
  to 0.83 (best window). Lines at 0.05 to 0.3 skip nothing.
- AUC for "this chapter has a real defect": 0.57 and 0.53. At 0.5 the gate would skip 67%
  of chapters and lose 51% of the real defects.
- The best safe point skips 5% of chapters ($1.79 of $46.28) and does not survive a
  split-half check: on the held-out half it misses 1 to 2 real-defect chapters.
- The reason is clear in the data. Across books, Jev's score tracks the book's flag rate
  (rank correlation 0.83). Inside a book it is at chance (0.52). Jev is reading the
  book's prose style, which the editors kept on purpose as house voice, not the defect.
- Pointing the reader at part of a chapter barely beats chance either.
- Side finding: two chapters were scanned twice on identical prompts on 2026-09-10 and
  only the second result was kept, so the expensive reader's own repeatability is unknown.

Why it fails: "does any sentence in 2,500 words fail to parse on first read" needs a
sentence-by-sentence close read. That is reasoning work, not a single judgement.

## 5. SwimTrack editorial

The pipeline has run once (2026-06-07). Two decisions with text survive. Nothing can be
measured on that. A fresh pull of the six feeds gave 45 items, labelled blind before the
run: 4 in scope.

- Jev found all 4 at every line tested. False positives fell from 8 to 4 between 0.3 and 0.7.
- Pillar label: 4 of 4 right on the in-scope items.
- The keyword ranker's real top 5 for that day held 0 of the 4 in-scope items. Ordering by
  Jev's "serves a swim parent" score put all 4 in the top 5. One day, one sample.
- Jev disagreed with both real Opus decisions. One of them, a race recap, is a kind the
  editorial rules say to mark out of scope.

## What the five have in common

- Jev was right where the judgement is visible on the surface of a short text: is this
  junk, does this need an outward action, which answer fits.
- Jev was wrong where the judgement needs a close read of long text, a step of reasoning,
  a number, or a date.
- Its confidence was informative every time it was checked: very high and very low
  answers were reliable, the middle was not. The clarity replay never left the middle.
- A disagreement between Jev and a stronger process meant "Jev is wrong" far more often
  than "the stronger process is wrong". That rules out designs that act on a Jev
  disagreement, and allows designs that skip work when Jev agrees with high confidence.
- Cost and speed were never the limit. All five replays together cost under 40 cents.
