# Jev (TypeSafe) assessment: where it could add value

Date: 2026-09-18. Model tested: `jev-1.13.0` (the current `jev-latest`).
Scope: every project under `~/projects`, plus `~/.local/bin` and `~/.claude`.
Evidence: `docs/evidence/jev-2026-09-18/` (test scripts and raw results).
Suggested order of work is kept out of this file on purpose. It is in
`docs/jev-first-actions-DRAFT-2026-09-18.md`.

## 1. Short version

Jev is not a chat model. It cannot write, summarize, or explain. It answers
fixed questions about a piece of text and returns numbers that code can use:
a pick from a list, a rating on a scale, or the chance that a statement is true.

It is very fast (about 0.6 seconds) and close to free ($0.042 per million input
tokens, output free). In small live tests it matched or beat the rules we use
today, including on cases those rules are known to get wrong. The tests were
tiny (39 calls) and used made-up inputs, so treat them as a first look, not proof.

Across the portfolio, 46 places fit. They fall into four patterns:

1. A large model is called only to produce a label or a yes/no. Jev is a direct swap.
2. A regex or keyword list stands in for a judgement about meaning. Jev replaces
   or backs up the rule.
3. An expensive model runs on everything. Jev decides first whether the call is needed.
4. A person sorts a pile by hand. Jev pre-sorts it with a confidence number.

The honest value picture: **the money saved is small, because the current spend
is small.** The largest single line item Jev could gate is $46. The real value
is in three other places:

- **Reliability.** Rules that fail silently today (a missed "stuck" session, a
  job that failed without the word "error", a wrong default in a meet parser).
- **Your time.** Piles you sort by hand (72 held backlog items, audit findings,
  a planned 2 to 3 hours each Monday on government listings).
- **Things that are too expensive to do today.** Judging every story, every
  chapter, every candidate, not just a top 5 chosen by a budget.

The main open risk is not quality. It is that TypeSafe is a new vendor with no
stated data retention period, so private data (email, children's data, unpublished
manuscripts) needs a decision before it goes there.

## 2. What Jev is

| Fact | Value | Source |
|---|---|---|
| Input | Text or JSON ("state") plus typed questions | docs |
| Output types | Choice (one of a list, with odds per option), Score (rating on described levels), Noul (chance of yes) | docs |
| Many questions per call | Yes. They run in parallel and cannot see each other | docs |
| Speed | Median 0.61 s, worst 0.98 s over 17 calls | measured |
| Price | $0.042 per million input tokens. Output is free | docs `/models` |
| Size limit | 64k tokens per request. 32k for the state plus the longest question | docs `/models` |
| Rate limit | 1,200 requests per minute. TypeSafe says limits may change without notice | docs `/models` |
| Languages | English is best. Others "handled but not equally well" | docs `/models` |
| Images, PDFs, audio | No. Text only | docs `/models` |
| Training on our data | TypeSafe says no | docs `/models`, `/legal` |
| Data retention | "As long as necessary". No period stated. Zero retention is enterprise only | DPA |
| Endpoint | `POST https://api.typesafe.ai/v1/systemone`, bearer key `TYPESAFE_API_KEY` | docs `/api` |

### Known weak spots (from TypeSafe's own page, last reviewed 2026-09-17)

- **It reads literally.** It answers the words you wrote, not what you meant.
  We saw this in testing (section 3).
- **No math.** It cannot count, compare numbers, or compare dates. Code must do that.
- **No multi-step reasoning.** One direct judgement per question.
- **Long, noisy input hurts accuracy.** Send only what the question needs.
- **Text that argues for its own label can steer it.** It does not treat input
  as hostile. This matters for spam and for anything an outsider writes.
- **Separate questions are not consistent with each other.** A yes/no asked two
  ways can give numbers that do not add up. Thresholds must be tuned per question.

## 3. Live test results

All inputs were made up. No private data was sent. Total cost of all tests was
under one tenth of a cent.

### Test 1: email triage (the Bebop shape)

Four emails. Three questions per email in one call.

| Email | Category | Needs reply today | Importance (0 to 3) |
|---|---|---|---|
| Swim coach: warm-up moved, confirm tonight | family (0.99) | 0.97 | 2.99 |
| Shop: 30% off, 48 hours | promo (1.00) | 0.08 | 0.39 |
| Host: payment failed, 5 days left | money (1.00) | 0.07 | 2.82 |
| Old colleague: coffee sometime, no rush | work (1.00) | 0.04 | 1.04 |

All correct. The payment email is the useful one: important, but not due today,
because the email says 5 days. Jev separated those two ideas.

### Test 2: three harness decisions, today's rule versus Jev

Cases were built around the known weak points of the current rules.

**Watchdog log check** (`watchdog/triage.py:176`, a 5-word error regex)

| Log tail | Correct answer | Regex today | Jev |
|---|---|---|---|
| Clean run, `failed=0 error=0` | no alert | no alert | 0.02 |
| First fetch failed, retry worked, job done | no alert | **alert (wrong)** | 0.03 |
| Real traceback, exit code 1 | alert | alert | 0.97 |
| "Could not reach server, giving up", exit code 2, no error word | alert | **no alert (wrong)** | 0.97 |
| Article titles that contain "error" and "failed" | no alert | **alert (wrong)** | 0.03 |

Regex: 2 of 5 right. Jev: 5 of 5. The silent failure is the important row.

**tmux session state** (`~/.local/bin/agents:34-53`)

| Screen | Correct | Regex today | Jev, first wording | Jev, sharper wording |
|---|---|---|---|---|
| Permission menu | BLOCKED | BLOCKED | BLOCKED (0.94) | BLOCKED (1.00) |
| Task running | WORKING | WORKING | WORKING (0.99) | WORKING (0.99) |
| Turn finished, status line | WAITING | WAITING | WAITING (0.97) | WAITING (0.98) |
| Agent asks "A or B?", no status line | WAITING | **IDLE (wrong)** | **BLOCKED (0.71, wrong)** | WAITING (0.85) |
| Fresh empty prompt | IDLE | IDLE | IDLE (0.88) | IDLE (0.93) |
| Permission menu with new wording | BLOCKED | **IDLE (wrong)** | BLOCKED (0.99) | BLOCKED (1.00) |

This test shows the literal-reading weak spot. The first BLOCKED wording said
"cannot continue until the user picks an option". The agent's own "A or B?"
question fits those words, so Jev chose BLOCKED. Rewording the option to say
"a numbered menu drawn by the terminal program, not a question in the agent's
message" fixed it. **Wording is the tuning knob, and every use needs a small
set of test cases before it is trusted.** Note that the wrong answer came with
lower confidence (0.71) than every right answer (0.88 to 0.99), so a confidence
threshold would also have caught it.

**Council panel router** (`council/router.py`, today a `gemini-3-5-flash` call
with a 2,000-token budget). Five clear inputs: 5 of 5 right at 0.99 to 1.00
confidence. One vague input ("Thoughts on the new teaser pipeline?") returned
0.47 confidence, split between two panels. That is the correct behavior: the
low number is the signal to fall back to the default panel.

### Test 3: Portuguese

Six made-up headlines, each sent in Portuguese and in English. Question:
would this story change a practical decision for a foreigner in Portugal?

| Story | Correct | Portuguese | English |
|---|---|---|---|
| Residence permit renewals move online | yes | 0.94 | 0.91 |
| Tax regime change for new residents | yes | 0.94 | 0.94 |
| Football derby result | no | 0.02 | 0.02 |
| TV presenter's new show | no | 0.03 | 0.03 |
| Lisbon rents up, new cap published | yes | 0.72 | 0.87 |
| Opposition criticises the prime minister | no | 0.04 | 0.04 |

The yes/no question held up in Portuguese. The gap between yes and no stories
stayed wide. One story lost some confidence (0.72 versus 0.87). A second
question in the same test, a three-way pick between "lead story", "one line",
and "reject", was weaker in both languages: it chose "one line" for the football
and politics stories at low confidence. The yes/no form was the cleaner signal.
Six headlines is a small sample. It says Portuguese is worth a real trial. It
does not prove it.

### Test 4: the watchdog log check on real logs

This is the first test on real data. 116 samples of 50 lines each, cut from 12
real cron logs on this machine (loom, meettrack, diem drain, watchdog, backlog
runner, both story engines, ops tick, engage scan, bento sync). Emails, customer
rows, wiki article names, email subjects, and token-like strings were removed
before anything was sent. About two thirds of the samples were picked because
today's rule fires on them. So the mix is harder than a normal day.

The correct answers came from two blind labelers (Sonnet). They saw only the
log text. They did not see the rule's answer or Jev's answer. The Jev question
wording was fixed before any label existed. 54 samples were real failures and
62 were not.

| | Right | Missed failures | False alarms |
|---|---|---|---|
| Rule today (`watchdog/triage.py:176`) | 79 of 116 (68%) | 5 | 32 |
| Jev, alert at 0.5 or more | 104 of 116 (90%) | 8 | 4 |
| Jev, alert at 0.7 or more | 106 of 116 (91%) | 10 | 0 |
| Jev, alert at 0.3 or more | 107 of 116 (92%) | 0 | 9 |

How Jev's numbers lined up with the truth:

| Jev's number | Samples | Real failures among them |
|---|---|---|
| Under 0.3 | 53 | 0 |
| 0.3 to 0.7 | 19 | 10 |
| 0.7 and over | 44 | 44 |

What this shows:

- Below 0.3 and above 0.7, Jev made no mistakes on this set. All 12 of its
  disagreements with the labels sit in the middle band.
- The middle band holds the real gray cases. Examples: a read error that fixed
  itself on the next run, a traceback that the job caught and carried on from,
  a backlog item held with "needs you". Two careful people could label these
  either way. The labelers marked 11 of the 12 as medium confidence.
- That maps onto the watchdog's own ladder: under 0.3 is ok, over 0.7 is an
  alert, the middle is a warning to read when convenient.
- The rule's 32 false alarms are the main cost today. 12 came from
  `"error": null` lines in the diem drain log, and 16 from the two story
  engines, whose reports use the word "failed" about news sources.
- Its 5 misses were failures with no error word, as in test 2. 3 of them
  were in the backlog runner log.
- A second question, "did the most recent run fail", was right on 95 of 97
  samples where the labelers could tell.
- Speed: all 116 samples took 12.5 seconds at 6 at a time. Median 0.62 s, worst
  1.03 s, no errors. Cost: $0.0074 for 177k tokens. Samples are about 1,500
  tokens each. At the watchdog's rate (48 polls a day) that is about $0.003
  per log per day.

Limits of this test:

- The labels came from a model, not from you. They are careful, but they are
  one opinion on the gray cases.
- The 0.3 and 0.7 lines were read off this same set. They need a fresh set
  before they are trusted.
- 116 samples from one machine. 16 of them are the same meettrack outage.

Two things this test found that are not about Jev:

- **The watchdog's log check reads no logs today.** All three paths in
  `watchdog/run.py:39-43` are missing. `loom/logs/absorb.log` does not exist
  (the loom log is `loom/logs/runs.log`). Both meettrack logs were renamed to
  `.paused-20260815`. A missing log is skipped without a word
  (`run.py:209-211`). About 20 other cron logs are never checked at all.
- **The loom coverage check fails often.** `loom/logs/runs.log.err` shows the
  same error 52 times, last on 2026-09-15: the model's reply is not valid JSON
  (`loom/coverage.py:50`). Each failure falls back to "not covered". This is
  the exact kind of failure that item H1 removes.

## 4. The four patterns, with the best example of each

**Pattern A: a big model called only for a label.** The model writes JSON, code
parses it, and extra code guards against bad JSON. Jev returns the typed value
directly, so the parsing and retry code goes away.
Best example: `loom/coverage.py:24-60`. The prompt literally asks for
`{"covered": true}` or `{"covered": false}`.

**Pattern B: a rule that stands in for meaning.** Keyword lists and regexes
that grow a new patch every time they miss.
Best example: `watchdog/triage.py:176`, shown failing 3 of 5 cases above.

**Pattern C: a cheap gate before an expensive call.**
Best example: `ultimate-portugal`. A headless Opus session reads every new
story each weekday. 1,948 stories in August, 1,710 rejected (88%). In September
so far 1,168 stories, 976 rejected, 8 used.

**Pattern D: a pre-sort for a pile you work through by hand.**
Best example: the backlog. 72 held and 17 in-review items, sorted by date only
(`backlogrun/cli.py:1297`).

## 5. Candidates by area

Fit: how well the decision matches what Jev does. Data: what would be sent.
"Private" means a data decision is needed first (section 7).

### 5.1 AI harness (`ai-harness`, `vps-tools`, `backlog`, `~/.local/bin`)

| # | Where | Decision | Today | Pattern | Fit | Data |
|---|---|---|---|---|---|---|
| H1 | `loom/coverage.py:24-60` | Does the wiki article already cover this learning? | `deepseek-v4-flash` yes/no in JSON, up to 40 calls a night | A | Very high | Private wiki |
| H2 | `bebop/prompts/briefing-*.md:12`, `bebop/run-briefing.sh:70-91` | Which emails reach the briefing | Keyword list inside the Haiku writing prompt. Not measurable | A + C | Very high | Private email. Highest sensitivity here. Some Portuguese mail likely |
| H3 | `council/router.py:7-19` | Which panel reviews this | `gemini-3-5-flash`, 2,000-token budget | A | Very high (tested) | Code and questions |
| H4 | `~/.local/bin/agents:34-53`, `session-bridge/src/tmux.ts:3-21` | Is this session stuck, waiting, working | Three regexes, every minute per session. Unknown screens fall to IDLE, which hides the nudge | B | High (tested) | Screen text, may show code or a secret |
| H5 | `backlogrun/cli.py:443`, `:583-594` | Which 2 items run tonight. Is the item safe to run unattended | Oldest first. Safety is left to the Sonnet session to self-report | C | High | Backlog prompts |
| H6 | `backlogrun/cli.py:1291-1341` | What you review first in the morning | Date sort | D | High | Backlog prompts |
| H7 | `watchdog/triage.py:173-186` | Is this log tail a real failure | 5-word regex, every 30 minutes, 3 logs | B | High (tested) | Ops logs |
| H8 | `watchdog/run.py:306-344` | Is this worth waking the investigator | Fixed new/worse/cooldown rule | C | Medium | Small report |
| H9 | `council/gate.py:20-34`, `:83-97` | Does this finding block the merge | Synonym table, folder-name lists, fixed `confidence >= 8` | B | Medium. Input is up to 200 KB, so it needs one call per finding with a slice of the file | Private code |
| H10 | `loom/pending.py:29-88` | Are these two quarantined learnings the same fact | Word overlap, threshold tuned on one example | B | Medium | Private wiki |
| H11 | `loom/route.py:72-93` | Which wiki file gets this learning | `deepseek-v4-flash` returns a path. Guards exist because it sometimes answers with a sentence | A | Medium. Blocked: the index is about 48k tokens, over the limit. Needs a shortlist step first | Private wiki |
| H12 | `council/sweep.py:58-90` | Are two findings duplicates. Is a finding real | First 80 characters. Self-reported confidence | B | Medium. Weekly | Code |
| H13 | `tools/regress/bakeoff_grade.py:56-135` | Grade a chair model on a rubric | Regex patterns, already patched once for a miss | B | Medium. Rare and manual | Model output |
| H14 | `backlogrun/cli.py:77-81`, `:621-634` | Did the night session finish, hold, or fail | Regex on the last message | A | Low. 2 a night, other checks exist | Session text |
| H15 | `loom/sentinel.py:14-22` | Does wiki text hold a dangerous instruction | 7 regexes. One known false alarm that never clears | B | Low. Second layer only. The regexes stay | Private wiki |
| H16 | `loom/weave_lint.py:9-37` | Was the new fact woven in or just stuck on the end | Diff ratio | B | Low. Input near the size limit | Private wiki |
| H17 | `fixit/queue.py`, `diem/queue.py`, `loom/phantom.py` | Queue order and one-time cleanup | Oldest first, fixed type order | D | Low | Mixed |

### 5.2 Publishing (`romance-empire`, author sites, social)

| # | Where | Decision | Today | Pattern | Fit | Data |
|---|---|---|---|---|---|---|
| P1 | `src/social/engage_scan.py:235-347` | Label each TikTok post: format, intent, author account, genre fit 0 to 3, comment invite 0 to 2 | `deepseek-v4-flash`, 25 posts per call, daily. 962 labels so far, $0.05 total | A | Very high. The prompt is already Choice plus Score. Downstream thresholds want the odds they do not have | Public posts |
| P2 | `src/venice/edit_pipeline.py:697-741`, `scripts/clarity-scan.py` | Does any sentence in this chapter fail on first read | GPT-5.4 with reasoning on. 404 calls, **$46.28, the most expensive stage in the catalogue** | C | High as a gate. Jev cannot write the quotes and fixes | Unpublished manuscript |
| P3 | `scripts/proofread-resolve.py:46-57` | Proofreader query: dismiss, escalate, or style | 25 regexes tied to character names. They will not carry to a new series | B | Very high | Editorial notes |
| P4 | `src/venice/manuscript_qa.py:56-205` | Does this read as machine-written | About 20 regexes and thresholds. Rule-of-three was left out as "not regexable" | B | High, per chapter. Whole book is over the limit. This is the "reads like AI" problem from the Still Waters feedback | Manuscript |
| P5 | `src/social/calendar_kit.py:373-381` | Is the book-one mention the call to action or an aside | The code comment says code cannot tell, and sends it to you | D | Very high. Tiny input | Public copy |
| P6 | `.claude/commands/lock.md:32-60` | Is this audit finding real or made up by the model | You check each claim against the prose by hand | D | High. One finding plus its chapter is small | Manuscript |
| P7 | `src/venice/manuscript_qa.py:880-910` | Does chapter 1 tell the reader where the book is set | Regex on a hand-written alias list per book | B | High | Manuscript |
| P8 | `src/copy/checks.py:24-80` | Blurb checks. Also trope-label speak, which nothing checks today | 8-phrase list, dash count | B | High. Matches your social copy rules of 09-16 | Public copy |
| P9 | `src/venice/texture_scan.py:47-88` | Does this chapter have the three texture problems | GPT-5.4, 90 calls, $1.55 | C | Medium | Manuscript |
| P10 | `src/social/zernio_push.py:433-492` | Is this caption already live | Character similarity, tuned on one pair | B | Medium | Public copy |
| P11 | `src/social/engage_scan.py:645-696`, `:902-939` | Does this drafted comment respond to the video. Did the model invent a name | Word overlap, capital-letter rule | B | Medium. The safety half (length, links, emoji) stays as code | Public posts |
| P12 | `src/social/engage_scan.py:1045-1087` | Is there enough in this post to comment on | 25 of 48 targets needed a second paid Opus draft | C | Medium | Public posts |
| P13 | `emdash_trim` ($37.47, 505 calls), `said_trim` ($10.07) | Does this chapter need a trim pass | Raw counts | C | Low. A count is already exact. Jev adds little | Manuscript |
| P14 | content-humanizer `scan.py` | Is this flagged phrase a real AI tell | Phrase lists. Its own notes say a hit is a candidate, not a verdict | B | Medium. It is a shared plugin, so a change affects every project | Drafts |
| P15 | `illustrated-copy.yaml` pull lines | Does this book line read cold with no context | Nothing. Judged by eye | D | Medium. New check | Public copy |

### 5.3 Apps and other projects

| # | Where | Decision | Today | Pattern | Fit | Data |
|---|---|---|---|---|---|---|
| A1 | `ultimate-portugal/engine/RANKING.md`, `scripts/collect-stories.mjs`, `scripts/apply-verdicts.mjs` | Per story: five 1 to 10 scores, select / quick hit / hold / reject, promotes a competitor | A headless Opus session (3-hour timeout) reads every new story each weekday. 60 to 100 a day, 84 to 88% rejected | C | Very high shape match. Mostly Portuguese input. Test 3 is encouraging but small | Public news |
| A2 | `swimtrack-website/engine/` | Same engine, swim-parent news | Same. 200 to 400 stories a month | C | High. English mostly, so it is the safer place to prove A1 | Public news |
| A3 | `swimtrack/editorial/src/editorial/agents.py:24-26`, `pipeline.py:19-33` | In scope or not. Which of 7 pillars | Opus. JSON repaired by hand-written code | A + C | Very high. Also lets every candidate be judged, not only a top 5 | Public RSS |
| A4 | `swimtrack/editorial/src/editorial/rank.py:7-56` | Which 5 stories deserve an Opus call | 35 keyword substrings | B | High | Public RSS |
| A5 | `sat-prep/content/GENERATION.md` step 2 | Blind-solve each new question. Does the answer match the key | A manual agent step. Pass or fail only | A | Very high. The odds per option expose questions with two defensible answers, which pass/fail cannot see. Not for number-answer questions | Made-up questions, no student data |
| A6 | `govcon-hunt-list` report sections 8 and 10 | Is this SAM.gov listing a fit: remote, set-aside, which lane | Nothing built. Planned as 2 to 3 hours of reading each Monday | D | High. Public English text. Greenfield | Public listings |
| A7 | `sat-prep/worker/tutor.ts:70`, `:416-430` | Is the student asking for the full answer. Is it off topic | A full Sonnet stream starts before anything checks | C | High fit, **blocked on data**: a child's messages | Children's data |
| A8 | `splash_poller/parser.py:67-113`, `startlist_pdf.py:55-213` | Gender, stroke, distance from a meet PDF header | Regex with silent defaults (`FEMALE`, `100`, `Livres`). A past bug corrupted splits this way | B | Medium. Portuguese and mixed languages. Must batch per PDF | Public meet PDFs with children's names |
| A9 | `sat-prep/src/lib/feedbackBacklog.ts:61-74` | Route a beta report: fix now, product decision, billing/legal/auth | The reporter's own dropdown pick | D | Medium | Parent and student text |
| A10 | `aris-management-website/src/pages/api/contact.ts` | Real enquiry or bot | Length floor and a link regex that also flags real people | B | Medium. Outsider text can steer Jev (section 2), so it adds a signal but must not be the only gate | Enquirer details |
| A11 | `swimtrack-website/scripts/check-draft.mjs:32-81`, twin in `ultimate-portugal` | Does the draft read as AI | 8 regexes and counters | B | Medium. Same idea as P4 | Public drafts |
| A12 | `swimtrack-coach/lib/parser/venice.ts`, `stroke-source.ts` | Stroke and intensity per set | Qwen, then a regex override because the model "flickers" | A | Low for now. All terse Portuguese, zero traffic. A golden test set exists to measure it | Children's training data |
| A13 | Swimmer identity match (3 places) | Are these two records the same swimmer | Exact tiers, then a human queue | D | Low. Children's personal data for a small gain | Children's data |
| A14 | `swimtrack/pipeline/discover.ts:48-75` | Is this a Portuguese meet | 60 keywords | B | Low. A newer path already uses a nation field | Public |

## 6. Value ranking across everything

Ranked by expected value to you, not by how neat the fit is. Data blockers are
noted. **Nothing here is proven yet.** A place in Tier 1 means "most worth
measuring first", not "safe to switch on". Each item still needs a replay
against real, already-judged data before it acts on anything.

### Tier 1: clear value, low risk, public or low-sensitivity data

| Rank | Item | Why it matters | Kind of value |
|---|---|---|---|
| 1 | A2 story pre-filter (English), then A1 (Portuguese) | The largest volume in the portfolio. Thousands of stories a month read by Opus to reject 85%. A2 is English and proves the pattern. A1 has 10 times the volume but moves up only if a replay of its 3,116 judged stories shows Portuguese holds up. Six headlines do not show that | Cost, run time |
| 2 | H7 + H4 watchdog and session monitor | Both rules miss real events silently today, and both were shown failing in test 2. A missed stuck session or failed job costs you hours | Reliability |
| 3 | H5 backlog safety gate | It checks a rule the backlog README calls non-negotiable and nothing enforces today: is this item outward-facing or irreversible. The gate may only add holds, never remove one, so a wrong answer costs a delayed item, not an unsafe run | Safety |
| 4 | P1 engage post labels | Already the right shape. Gives the ranking code the confidence numbers it lacks. Re-labeling becomes free | Quality, cost |
| 5 | A3 + A4 swim editorial gate | Deletes fragile JSON repair code and a keyword table. Every candidate gets judged | Reliability, reach |
| 6 | A5 SAT question check | Finds ambiguous questions, a defect the current check cannot see. Quality of a paid product | Product quality |
| 7 | H3 council router | Trivial swap, tested 5 of 5, removes a model call | Simplicity |
| 8 | P5 + P8 + P3 small copy and proofread checks | Tiny inputs. P5 is a question the code already hands to you. P3 removes per-book regex upkeep | Your time |

### Tier 2: high value, needs a decision or some building first

| Rank | Item | What it needs first |
|---|---|---|
| 9 | H6 backlog morning pre-sort | Nothing blocking. It only changes the order you read in |
| 10 | P2 clarity-read gate | **This is the riskiest kind of use: a cheap gate that skips an expensive check.** A wrong "clean" lets a defect through to a published book. It must not skip anything until a replay on the 404 already-scanned chapters shows it misses almost none. Also needs the manuscript data decision |
| 11 | P4 + P6 + P7 AI-tell score, audit finding triage, chapter-1 grounding | Manuscript data decision. P4 speaks directly to the "reads like AI" reader feedback |
| 12 | A6 government listing triage | It is a new build, not a swap. High payoff in hours if the hunt goes ahead |
| 13 | H2 Bebop email filter | Best product fit of all, and the most private data of all. Needs the data decision. Email is outsider text, so a sender could word a message to look important. The cost of that is one junk line in a briefing, which is low |
| 14 | H1 loom coverage | Cleanest swap in the portfolio. Private wiki data decision |

### Tier 3: real but small, or blocked

H8 to H17, P9 to P15, A8 to A14. Low volume, marginal gain, or blocked by
language, size, or data. A7 (tutor gate) would be Tier 1 on fit alone. It sits
here only because it sends a child's messages to a new vendor.

### What the money looks like

- Jev's own cost rounds to zero. All 39 test calls cost under $0.001. The
  whole story flow of about 2,000 stories a month would cost about one cent.
- What it can save is modest: part of $46 (clarity), part of the Opus story
  sessions, a Gemini call per council run, a DeepSeek call per loom check.
- So do not judge this on the bill. Judge it on missed alerts, hand sorting,
  and work that a budget stops you doing today.

## 7. Where not to use it

| Area | Why not |
|---|---|
| Anything that must write text: council seats, the chair's summary, drafting, line edits, trims, blurbs, translation, Bebop's wording | Jev cannot write |
| Whole-book audits (`edit_pipeline.audit`, final QC, coherence read, 73k to 96k tokens) | Over the size limit, and they need long reasoning |
| `proofread_bible_keeper` (31k tokens average) | Already at the limit |
| Every image decision (sharpness gate, culls, cover picks) | Text only |
| Money and exact matching: `finance-tracker` import planner, card parser, `monthly-bidding`, swim time standards, SAT answer checking, `diem/drain.py`, `venice_usage` | Exact math. Code is right and repeatable. Jev is neither |
| Safety floors: `loom/gate.py` secret scan, `backlogrun/readiness.py`, `sessiongc` classify | These fail closed on purpose. A probability must never replace them |
| Pressing approve on a permission prompt (`session-bridge/src/router.ts:142`) | A wrong "approve" is far worse than "not understood". Test 2 showed a confident wrong BLOCKED before the wording fix. Jev may label a session for a nudge. It must never press the key |
| `nato-support` | Offline, PowerShell only, may be classified. No cloud service is allowed. The SOP 810 checks are Jev-shaped in principle, which is worth knowing, but not usable |
| `tax-advisor` | PDFs need vision, and it is the most sensitive data you hold |
| The human blind bake-off (`scripts/blind-bakeoff.py`) | Its whole job is to capture your taste. Automating the judge destroys the tool |
| Signup forms on the author sites | An email address is not text with meaning. Three layers already work. It would add delay to signup |

## 8. Risks and open questions

1. **Data handling is the main question.** No retention period is stated. Zero
   retention is for enterprise customers only. A subprocessor list exists at
   `trust.typesafe.ai/subprocessors` and has not been reviewed. Until decided,
   the safe set is public data: news stories, social posts, marketing copy,
   public listings, made-up questions, and operations logs. Terms can also
   change later. The DPA and subprocessor list need a re-read from time to
   time, and every use needs an off switch that returns to today's rule.
2. **Children's data** (SAT tutor, swim apps) would need the DPA signed and a
   privacy policy line before any use.
3. **New, small vendor.** The skills repo is 25 days old. Limits "can change
   without notice". Every use needs a fallback to today's rule or model, so an
   outage changes nothing.
4. **Version drift.** `jev-latest` moves. Thresholds tuned on 1.13 may shift.
   Pin `jev-1.13.0` in anything that gates real work.
5. **Literal reading.** Shown in test 2. Each use needs 10 to 20 labeled cases
   kept next to the code, run before any wording change.
6. **Outsider text can steer it.** Relevant to email, contact forms, social
   posts, and log lines that contain user content. Any use that reads outsider
   text keeps its rule-based checks. Jev adds a signal there. It is never the
   only gate.
7. **Portuguese.** Test 3 was encouraging on six headlines. The story engine
   has thousands of already-judged stories, so a proper measurement is cheap.
8. **Confidence is not correctness, and the odds may not be well calibrated
   for our data.** TypeSafe says the model is trained for calibrated answers.
   We have not checked that. A reported 0.97 could be right less often than 97
   times in 100 on our inputs. Every gating use needs its threshold set from
   labeled data it has not seen, per question. Several candidates already hold those answers: 3,116
   judged stories, 962 post labels, 48 engage targets with your post or skip
   choice, and the swim coach golden set.

## 9. How this was done

- Docs read: introduction, quick start, API, models, confidence, known weak
  spots, legal, and the data processing agreement.
- Three live tests on made-up data, 39 calls in total.
- Three read-only surveys of the projects. Key numbers were re-checked by hand:
  story counts and reject rates, stage costs from `manuscripts/*/usage-log.csv`,
  and the code at `loom/coverage.py`, `agents`, `watchdog/triage.py`,
  `council/panels.toml`.
- The `typesafe` plugin (version 0.5.7) is installed at user scope. It holds
  one guidance file and no code that runs.
