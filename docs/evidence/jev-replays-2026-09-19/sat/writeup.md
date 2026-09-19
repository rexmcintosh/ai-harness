# Can Jev do the blind-solve quality check on sat-prep questions?

Replay experiment. Repo `/home/dev/projects/sat-prep` was read-only; nothing in it was changed.
All data below is from `results.json` in this folder, produced by `run.py` against the
frozen wording in `questions_wording.json`.

## What was sent

- Loaded all 19 batch files in `content/batches/`: 844 questions total, 767 `mc` and 77 `spr`.
  `spr` (exact numeric answer, no choices) were excluded per the task — out of scope for a
  Choice/Noul judge.
- Every mc question got exactly one Jev call with two questions against one state
  (passage if present + stem + the four choice texts, never the key or explanations):
  - **Choice** over that question's own A–D choice texts: "pick the one answer choice that
    correctly answers the question."
  - **Noul**: "exactly one of the answer choices, A through D, is defensible as the correct
    answer to the question."
- 61 of the 767 mc questions (8.0%) carry a `figure_svg` and are answerable only from a chart
  Jev never sees (41 math, 20 rw). These are flagged `needs_figure` and reported separately,
  not dropped.
- 767/767 calls succeeded, no errors. Cost: 460,278 input tokens, **$0.0193** total, ~39
  seconds wall clock at 12 workers.

## Overall accuracy

| Slice | Correct / N | Accuracy |
|---|---|---|
| All 767 mc questions | 701 / 767 | 91.4% |
| Math | 204 / 231 | 88.3% |
| Reading & Writing | 497 / 536 | 92.7% |
| **Math, excluding needs_figure** | 182 / 190 | **95.8%** |
| **Math, needs_figure only** | 22 / 41 | **53.7%** |
| RW, excluding needs_figure | 477 / 516 | 92.4% |
| RW, needs_figure only | 20 / 20 | 100% (n=20, see caveat) |
| Easy | 198 / 212 | 93.4% |
| Medium | 333 / 368 | 90.5% |
| Hard | 170 / 187 | 90.9% |

Needs_figure caveat: RW figure questions hitting 20/20 is not evidence Jev can read charts —
it can't see them. Their distractor choices are often internally checkable against each other
by text alone (one option asserts two named values are equal, another gives numbers that
contradict it), so elimination can work without the chart. Math figure distractors are bare
numbers with no such cross-checkable structure, and accuracy collapses to 53.7% — barely
above the 25% floor for 4-way blind guessing. Read "100% on RW figures" as small-n plus
leaky distractor construction, not figure comprehension.

## By domain / skill (mc, all difficulties, figures included)

| Domain | Correct / N | Accuracy |
|---|---|---|
| rw / Craft and Structure | 92/92 | 100% |
| rw / Information and Ideas | 127/127 | 100% |
| rw / Expression of Ideas | 113/116 | 97.4% |
| math / Algebra | 62/65 | 95.4% |
| math / Geometry and Trigonometry | 20/21 | 95.2% |
| math / Advanced Math | 57/61 | 93.4% |
| rw / Standard English Conventions | 165/201 | 82.1% |
| math / Problem-Solving and Data Analysis | 65/84 | 77.4% |

By skill, worst first (selected):

| Skill | Correct / N | Accuracy |
|---|---|---|
| **Boundaries** | 67/98 | **68.4%** |
| Problem-Solving and Data Analysis | 65/84 | 77.4% |
| Form, Structure, and Sense | 98/103 | 95.2% |
| Geometry and Trigonometry | 20/21 | 95.2% |
| Algebra | 62/65 | 95.4% |
| Transitions | 65/68 | 95.6% |
| Advanced Math | 57/61 | 93.4% |
| (8 other RW skills) | all 100% | 100% |

Problem-Solving and Data Analysis's weak score is almost entirely the needs_figure subset;
its non-figure items are fine. **Boundaries is a real, non-figure weak spot**: 31 of 98
Boundaries questions wrong, none needs_figure. Manual read of the wrong ones (below) shows a
specific, repeatable failure mode, not scattered noise.

## Calibration

Bucketed by Jev's own `confidence` field (from the Choice answer):

| Confidence bucket | Correct / N | Accuracy |
|---|---|---|
| 0.00–0.50 | 41/87 | 47.1% |
| 0.50–0.70 | 40/57 | 70.2% |
| 0.70–0.85 | 42/44 | 95.5% |
| 0.85–0.95 | 48/49 | 98.0% |
| 0.95–1.00 | 530/530 | **100.0%** |

Same, bucketed by top-option probability instead (a related but distinct number — see note):
0.00–0.50 → 36.4%, 0.50–0.70 → 62.2%, 0.70–0.85 → 83.0%, 0.85–0.95 → 96.4%, 0.95–1.00 → 100.0%.
Same shape, slightly more conservative at the low end.

**High confidence is trustworthy here.** 530 of 767 questions (69%) landed at confidence
≥0.95, and every one of them was correct. Calibration is monotonic and clean across the
whole range — the strongest result of the experiment.

Note: `confidence` and "top option probability" are two separate numbers the API returns and
are not always equal (mean absolute difference 0.033, but 65/767 questions differ by >0.15,
max 0.24). Both calibrate about equally well; `confidence` is the primary field used above.

The `one_defensible` Noul also tracks Choice-correctness: 0.0–0.5 → 44%, 0.5–0.7 → 63%,
0.7–0.85 → 82%, 0.85–0.95 → 98%, 0.95–1.0 → 99.7%.

## Manual adjudication of the interesting cases

Sample-size and bias note: this is one model's (mine) single read of each question, not a
panel, and I recomputed the math by hand rather than trust the batch's own explanation field.

### (i) Jev disagrees with the key at high confidence

66/767 (8.6%) of all mc questions had Jev disagree with the key. 19 of those are
needs_figure (expected blindness, not a content bug); 47 are plain-text questions Jev got
wrong. Below are the 15 highest-confidence disagreements (confidence 0.53–0.86):

1. `math-advanced-02#049` (.86) — key B(19.6) right; Jev picked C(39.2), the intermediate
   value 2h before dividing by 2. Arithmetic slip.
2. `2026-08-figures-math-2#005` (.79, needs_figure) — blind to the chart, expected miss.
3. `math-algebra-02#030` (.76) — key C(3) right (elimination system); Jev picked B(4),
   exactly the scripted "divided by wrong coefficient" distractor. Arithmetic slip.
4. `rw-form-structure-01#000` (.69) — key A("lays") right, singular subject "research"; Jev
   picked B("lay"), pulled in by nearer plural nouns — the exact trap the item tests. Jev
   fails the trap it's supposed to catch.
5. `math-advanced-02#057` (.67) — key D(-5) right (discriminant=0); Jev picked A(5), a sign
   error matching the scripted distractor.
6. `rw-boundaries-01#034` (.67) — key C (comma after intro dependent clause) right; Jev
   picked A, the **no-punctuation** run-on option. Real Jev error, not a bad question.
7. `math-advanced-02#017` (.65) — key A(8) right (exponent equation); Jev picked B(7),
   matching a scripted distribution-error distractor.
8. `rw-boundaries-02#013` (.64) — same shape as #6: comma needed after intro clause, Jev
   picks no punctuation.
9. `rw-boundaries-02#011` (.62) — same shape again.
10. `rw-transitions-wic-01#009` (.59) — key B("Moreover") right (adds a bigger, non-
    contradicting benefit); Jev picked C("Nonetheless"). The most defensible of the 15 —
    "chiefly valued for honey... Nonetheless, worth more for X" can be misread as a mild
    correction — but standard convention still wants a continuer here. Called: Jev wrong.
11. `rw-boundaries-01#047` (.58) — long intro participial phrase needs a comma (key D); Jev
    again picks the no-punctuation run-on option.
12. `diagnostic-v1#028` (.57) — non-essential clause needs a **matching pair** of marks
    (key A, comma...comma); Jev picked D, no punctuation on either side.
13. `rw-boundaries-01#016` (.56) — FANBOYS conjunction joining two independent clauses needs
    a comma before it (key B); Jev picked A, missing the comma.
14. `2026-08-figures-math-2#017` (.53, needs_figure) — blind to the chart, expected miss.
15. `rw-boundaries-02#000` (.53) — two independent clauses need a semicolon or period (key
    B); Jev picked C, no punctuation (fused sentence).

**Verdict: 0 of 15 are mis-keyed or genuinely ambiguous questions.** 2 are expected
figure-blindness. The other 13 are real Jev mistakes on correctly-keyed questions: 4 are
math arithmetic/sign slips, 8 are the *same* Boundaries failure — Jev defaults to "no
punctuation" when the fix is "add a comma/semicolon at a clause boundary," especially after
a long introductory clause — and 1 is a defensible-but-wrong transition read.

### (ii) Jev agrees with the key but is unsure

34 correct answers had the top two option probabilities within 0.25 of each other; 4 correct
answers had `one_defensible` < 0.5 (2 overlap with the split set). I read the 15 most
extreme splits plus the 4 low-Noul cases — 17 unique questions.

**Verdict: 0 of 17 show real ambiguity or a mis-keyed question.**
- 6 are needs_figure math (blind guesses that happened to land right, mostly by eliminating
  an option whose magnitude doesn't fit the others — not figure comprehension).
- 1 (`rw-info-ideas-01#000`, Command of Evidence) has a genuinely well-built near-miss
  distractor (an option that weakly supports the same conclusion without the controlled
  comparison the key option gives) — a legitimately hard, correctly-keyed item that
  appropriately makes Jev less certain. Good item-writing, not a bug.
- The remaining 10 are standard, correctly-keyed grammar/algebra items (verb-object
  no-punctuation rule, singular "each," matching-coefficient systems) where Jev is
  chronically less confident than the item deserves — again mostly Boundaries.

## The practical gate

Defining "send to review" as: Jev disagrees with the key, OR confidence < T, OR
`one_defensible` < T:

| Threshold T | Sent to review | Accuracy of the auto-accepted rest |
|---|---|---|
| 0.70 | 172/767 (22.4%) | 100% (in-sample) |
| 0.85 | 261/767 (34.0%) | 100% (in-sample) |
| 0.95 | 450/767 (58.7%) | 100% (in-sample) |

Plain disagreement alone already flags all 66 wrong answers — 8.6% of the bank. Adding
"confidence < 0.85" balloons review to 34% but only by sweeping in 195 more questions Jev
actually got right, mostly chronically-under-confident Boundaries items. The confidence
add-on buys little for real cost in review volume.

**On "what fraction of real catches would the gate contain": zero, because this manual read
found zero real catches (mis-keyed or genuinely ambiguous questions) anywhere in the 767** —
not in the high-confidence disagreements, not in the low-confidence agreements. That is a
meaningful negative result: in this bank, on this sample, a Jev-disagrees-with-key signal
was a proxy for "Jev is wrong," not "the question is wrong" — the opposite of what
`GENERATION.md`'s blind-solve gate wants (delete on disagreement, no debate, no repair).
Swapping Jev in for that gate as currently scoped would likely delete a real slice of good
Boundaries and math questions for the wrong reason.

## Where Jev is unusable, in numbers

- **Boundaries (punctuation-only distractors)**: 68.4% (67/98), with error concentrated in
  one repeatable failure — Jev under-corrects and prefers "no punctuation" when a comma or
  semicolon is required at a clause boundary, especially after a long introductory dependent
  or participial phrase. 8 of the 15 highest-confidence wrong answers show exactly this.
- **Any question needing a figure**: math figure questions are 53.7% (22/41), barely above
  guessing — Jev never receives the chart. 61/767 (8.0%) of the bank needs a figure.
- **Math arithmetic**: even outside figures, Jev's math errors are individually explainable
  arithmetic/sign slips (matching a written-in distractor almost every time), at a low but
  nonzero rate (8/190 non-figure math wrong, 4.2%) — consistent with the vendor's own
  warning that Jev cannot reliably do math.

## Limits

- Sample is the whole 767-question mc bank as it exists today — one point in time, one
  content set, not a general claim about all sat-prep content or all judge models.
- The manual adjudication (32 unique questions after overlap) is one model's (mine) single
  read, not a panel. I independently recomputed the algebra/arithmetic rather than trust the
  batch's own explanation field, but did not have a second reviewer.
- "Zero real catches" should not be read as "sat-prep's content is bug-free" — it's "Jev, on
  this run, didn't find any." A different or larger sample, or a genuinely buggy batch,
  could look different.
- `confidence` and top-option probability are close but not identical fields; both are
  reported since it wasn't obvious which one a product would key on.
- Cost and latency here ($0.02, 39s for 767 calls) say nothing about running this at 10x or
  100x the volume beyond linear scaling; no rate-limit or throughput ceiling was hit here.

## Files in this folder

- `questions_wording.json` — frozen wording, written before any Jev output was seen.
- `load_data.py` — read-only loader for the 767 mc questions from sat-prep.
- `run.py` — the harness that calls Jev via `jevlib.ask_many` and writes `results.json`.
- `results.json` — one row per question: id, batch, section/domain/skill/difficulty,
  needs_figure, key, Jev's choice, per-option probabilities, confidence, `one_defensible`
  Noul value, and a `correct` flag.
- `fetch_full.py` — pulls a question's full source record (including explanation, not sent
  to Jev) by id; used only for manual adjudication, not sent anywhere.
