# DRAFT: first thoughts on actions for Jev

Status: DRAFT. Nothing here is decided. This file holds opinions about what to
do. The facts are in `jev-assessment-2026-09-18.md`. Throw this file away
freely; the assessment does not depend on it.

## One decision comes before everything else

**What data may go to TypeSafe?** No retention period is stated, and zero
retention is enterprise only.

- Option 1: public and operations data only, for now. Covers all of Tier 1.
- Option 2: also unpublished manuscripts and the private wiki.
- Option 3: also personal email (Bebop).
- Children's data stays out until a DPA is signed, under every option.

First thought: Option 1. It unlocks almost all of Tier 1 with no privacy
question at all (the one edge case is H5, which sends backlog item prompts:
private work notes, but no personal data), and it gives weeks of real use before trusting a 25-day-old
vendor with anything private. An email to `privacy@typesafe.ai` asking for the
retention period costs nothing and informs Options 2 and 3.

## How to run any of these safely: shadow mode first

For each use, Jev runs next to today's rule and only writes its answer to a
log. Nothing acts on it. Compare the two against what really happened. Switch
over only where Jev was clearly better. Keep today's rule as the fallback if
the API is down. Pin `jev-1.13.0`.

How long to shadow depends on how often the event happens, not on the
calendar. A fixed "two weeks" is wrong for rare events. Aim for at least 100
judged cases, and at least 20 of the rare kind (real failures, real stuck
sessions). Where history exists, replay it first: old cron logs, the 3,116
judged stories, the 962 post labels, the 404 scanned chapters.

Two hard rules:

- **Jev never replaces a safety floor.** The secret scan, the loom sentinel
  regexes, the readiness checks, and the approve key stay as they are. Jev may
  add a second opinion beside them. It may add a hold. It may never clear one.
- **A gate that skips an expensive check needs a stated pass mark before it
  skips anything.** For the clarity gate (P2): replay all 404 scanned chapters.
  Count the chapters where the full scan found a real problem and Jev said
  "clean". The gate turns on only if that count is under 1 in 100 and none of
  the misses was a problem that was later fixed in the book. The same form of
  rule applies to the story pre-filter: near zero "used" stories rejected.

Each use also gets a small file of 10 to 20 labeled cases, run before any
wording change. Test 2 showed why.

## A shared building block

One small module, used by everything else: load the key, call the endpoint,
retry on 429 and 529, time out fast, fall back cleanly, write usage to the
existing Venice-style ledger. Python first (the harness, romance-empire, swim
editorial are Python). A thin JS copy later for the story engines. The official
SDKs exist (`typesafe_sdk`), which may be enough.

## Suggested order

1. **Shared module plus the watchdog log check (H7) and session monitor (H4),
   in shadow mode.** Reason: operations data only, both rules were shown
   failing, and both run all day, so a week of shadow logs gives hundreds of
   comparisons. For H4, Jev is only asked when the regex says IDLE. It labels
   for nudges only. It never presses approve.
2. **Story pre-filter on `swimtrack-website` (A2), in shadow mode, then
   `ultimate-portugal` (A1).** Reason: highest volume, and thousands of stories
   already carry a human-grade verdict, so accuracy can be measured on day one
   by replaying old stories. The number that matters is: how many "used" and
   "held" stories would Jev have rejected? That must be near zero before it
   filters anything. Replay A1's Portuguese stories the same way to settle the
   language question with real data.
3. **Backlog safety gate (H5), then the morning pre-sort (H6).** The gate may
   only add holds. Replay it over the 136 current items first and read every
   item it would hold.
4. **Council router (H3).** Small and already tested. Low confidence falls back
   to the default panel.
5. **Engage post labels (P1).** Replay the 962 cached labels, compare, then
   switch. Expose the odds to the ranking code.
6. **Swim editorial gate (A3, A4) and SAT question check (A5).** A5 is a new
   check more than a swap: run it over the 19 existing batches and look at the
   questions where Jev's odds are split.
7. **Small copy checks (P5, P8, P3, P15).**

After the data decision, if it allows manuscripts: P4 (AI-tell score per
chapter, tied to the Still Waters feedback), then P2 (clarity gate, shadow mode,
measured on the 404 chapters already scanned), then P6 and P7.

Bebop (H2) and loom (H1) wait for Option 2 or 3.

## Things to decide later, not now

- Whether A6 (government listing triage) gets built. It depends on the hunt
  itself going ahead, not on Jev.
- Whether the SAT tutor gate (A7) is worth a DPA and a privacy policy change.
- Whether to keep Jev at all. A fair test: after a month of shadow logs, did it
  catch events the rules missed, and did it cause any bad call? If the answer is
  not clearly yes and no, remove it.
