# Jev as a plugin for the council: offline test

**Date:** 2026-09-19. **Model:** `jev-1.13.0` (pinned). **Status:** measurement only. Nothing
is wired into `council review`, the merge gate or the backlog runner.
**Harness:** `tools/jev_council/` (tests: `tests/test_jev_council.py`). **Raw answers:**
`~/projects/backlog/reports/evidence/2026-09-19-jev-council-offline/` (outside this repo,
because they quote private review text).

## Summary for the owner

1. Jev is not a reviewer. It is good at the small label-and-score jobs around the review.
2. Three jobs tested well: reading the chair's verdict, tying a chair block to the panel
   finding it confirms, and spotting when two seats raised the same problem.
3. One job tested half well: re-scoring a finding. Jev tells a nit from a real failure. It
   cannot tell a real failure from a false alarm, because that needs the code.
4. One job failed: checking a finding against a slice of code. Drop it.
5. The whole test was 642 calls, about 0.6 seconds each, and cost under two cents.

## The question

The council reasons with large models. Around that reasoning sit several small judgements
that are labels or scores: which panel, how serious, is this the same finding, what did the
chair decide, which finding does this block confirm. Today a large model, a crude rule, or a
person does each one. The test asks where Jev does them well enough to inform the reasoning
that follows. It does not ask Jev to find or prove defects.

House rule for any later wiring: a Jev signal may make the gate stricter or give the chair a
labelled hint. It must never waive a block. Every use needs a kill switch and a fallback to
today's behaviour, and it starts in shadow mode.

## Data

- 67 saved council reviews were found: the backlog runner's `reviews/` folder and two
  evidence folders from 2026-09-18 and 2026-09-19.
- **Scope rule.** The owner allowed council text, code snippets and diffs for council work
  (2026-09-19). Repos with student, customer, mail, tax or finance data stay out, and so do
  the romance repos (unpublished manuscripts are a category that needs its own decision).
  A saved review's id does not name its repo, so scope comes from the backlog's id to repo
  map, and an id the map does not know is refused.
- After the scope rule and removing the runner's duplicate copies: **28 reviews, 207
  findings, 386 cross-seat finding pairs**, from `ai-harness`, `swimtrack`,
  `swimtrack-website`, `ultimate-portugal` and `aris-management-website`.
- Known-answer sets: the three regression fixtures (19 findings with a known outcome) and
  the 2026-09-12 chair bake-off (4 distinct chair blocks, all confirming one known finding).
- Emails, URL query strings and long token-shaped strings are removed before sending.

## Results

### 1. Read the chair's verdict: works

One Choice over the recommendation text: `approve`, `approve_with_conditions`,
`request_changes`. The 28 labels were written by hand **before** any Jev answer was seen;
7 were marked borderline at that time (the wording supports two labels).

| | 3 labels | ready to merge vs not | clear cases only (21) |
|---|---|---|---|
| Jev | **25 of 28** | **26 of 28** | **21 of 21** |
| Opening-words rule (regex on the first phrase) | 22 of 28 | 23 of 28 | 18 of 21 |

All three Jev misses are cases marked borderline in advance, and each miss is one step away
(for example "Hold for a small fix, then merge" read as conditions, not as request changes).
Mean confidence was 0.87 when right and 0.71 when wrong, so a threshold adds safety.
Why it matters: 17 of the runner's 22 structured review records carry `review_status:
unknown` today, so the morning report cannot say which items are ready.

A second question in the same call, "can it be merged exactly as it is?", behaved badly: it
read optional suggestions as requirements and scored many clean approvals near 0.1. It is
dropped. This is the literal-reading weak spot; wording needs test cases before it is trusted.

### 2. Tie a chair block to a panel finding: works

One Choice: the options are the panel's findings plus `none`. 4 real chair blocks, each asked
with the right panel (answer `F1.1`), with the right panel minus `F1.1` (answer `none`, and
the remaining findings concern the same file and function), and with two other fixtures'
panels (answer `none`).

**16 of 16 correct.** Confidence 1.00 on every true link and every cross-fixture `none`, and
0.75 to 0.93 on the hard `none` cases. This speaks directly to the open provenance gap
(`docs/council-gate-policy-2026-09-18.md`): it is an independent check on whatever source id
the chair reports. The sample is small and comes from one defect; it needs more real blocks.

### 3. Spot the same problem raised by two seats: works

One yes/no per cross-seat pair. 386 pairs: 269 scored under 0.2, 27 scored 0.8 or more. The
top 14 pairs (0.89 and up) were read by hand: 12 are clearly the same problem in different
words, 1 is the same topic stated as a confirmation by both seats, and 1 is a false match
(one seat confirms a behaviour, the other raises a risk about it). Pairs between 0.45 and 0.8
were related but different problems, which is the right ordering. A cut near 0.85 gives
about one "raised by 2 seats" cluster per review. Today the chair has to notice this itself,
and the weekly sweep compares only the first 80 characters.

### 4. Re-score a finding: partly

Three questions per finding: kind, "would this break normal use?" and severity on four
described levels.

- Jev's scores rise in step with the seats' own severity (would-break 0.10 for `info`, 0.31
  `low`, 0.42 `med`, 0.55 `high`, 0.64 `critical`), so it measures the same thing on a steady
  scale.
- It splits off what is not a defect: of 207 findings, 19 are `no_issue`, 30 are
  `missing_test_or_doc`, 38 are `hypothetical_risk`, and 117 are `concrete_defect`.
- It flags disagreements worth a look: 9 of 46 findings a seat called high or critical
  scored under 0.3 (docs defects rated "high", for example), and 10 findings a seat called
  low or info scored 0.6 or more (one of them later became a required change).
- **Limit, shown on the fixtures:** the two Node-compat false alarms that once blocked a pull
  request four times score 0.69 to 0.75, the same as the real defects (0.58 to 0.76). As
  worded they are concrete. Only the code shows they are moot. Jev reads the wording, so it
  can sort and flag findings but must not decide which are true.

### 5. Check a finding against a code slice: does not work

The same false alarms, checked against 40 lines of the cited file, then again with the one
line of `package.json` that settles them. Answers were low-confidence and inconsistent
(0.23 to 0.44 on four of six), and adding the decisive line did not move them to
"contradicts". This is multi-step reasoning over code, which TypeSafe lists as a weak spot.
Dropped.

### Not run

Panel routing (already 5 of 5 in the 2026-09-18 assessment) and a blast-radius score for the
gate's tier. Both need diffs rather than saved reviews and are cheap to add to this harness.

## Cost and speed

642 calls, 0 errors, 367,000 input tokens, about 1.5 US cents at the listed price. Median
call 0.6 seconds.

## Recommended order, if any of this is wired in

1. **Verdict label in the morning report, shadow first.** Show Jev's label and confidence
   next to `review_status` when the chair left it unknown. Display only; `approve` keeps its
   own checks.
2. **"Raised by N seats" as a fact in the chair's input**, and the same grouping in the
   weekly sweep.
3. **Source-link check inside the provenance work**, as the second opinion on the chair's
   source ids. It may fail a review closed; it may never pass one.
4. Finding kind and would-break as labelled hints for the chair, after more labelled data.

## Limits of this test

One labeller, who also wrote several of the reviewed changes. Small known-answer sets (4
chair blocks, 19 fixture findings). Most reviews are from one repo and one week. English
only. Thresholds here are read off this data and must be re-checked on data they have not
seen. TypeSafe states no retention period; the owner's scope rule above is the control.
