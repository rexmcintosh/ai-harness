# Jev shadow signals for the council

**Date:** 2026-09-19. **Model:** `jev-1.13.0` (pinned). **Status:** shadow mode. The signals
are shown and logged. They decide nothing.
**Code:** `council/jev.py`, `council/signals.py`. **Tests:** `tests/test_council_jev_shadow.py`
and the Jev section at the end of `tests/test_backlogrun.py`.
**Background:** `docs/jev-council-offline-test-2026-09-19.md` (the offline test that chose
these three jobs), `docs/council-gate-policy-2026-09-18.md` (the provenance gap) and
`docs/contracts/jev.md` (the rules of the shared Jev client, which every call here uses).

## Summary

1. Jev is a small model from TypeSafe. It answers a yes/no, a choice or a score about a
   short text. It is not a reviewer.
2. Three small jobs around a council review tested well offline. They now run after each
   review, in shadow mode: the answers are displayed in their own section and written to a
   log, and nothing else reads them.
3. Nothing Jev returns can change what the panel or the chair sees, the gate count,
   `review_status`, review readiness, `backlog-run approve` or any exit code.
4. One switch turns all of it off: `COUNCIL_JEV=0`. The shared switch `JEV_DISABLED=1`, which
   turns every Jev caller on the machine off, works too. With a switch off, without a key,
   or for a repository that is not on the allow list, the output is byte-identical to the
   output before this change.
5. Every call goes through the shared client in the `jev` package. The council has no HTTP
   client of its own.

## The three signals

| Signal | Question put to Jev | Offline result | What is shown |
|---|---|---|---|
| Verdict label | What did the chair decide: `approve`, `approve_with_conditions` or `request_changes`? One choice over the chair's recommendation text. | 25 of 28 against labels written in advance, 21 of 21 on the clear cases | `Jev reads the chair's verdict as: approve_with_conditions (0.91)` |
| Seat agreement | Do these two findings, from two different seats, report the same problem? One yes/no per pair. | Clean split; a cut near 0.85 gave about one group per review | `F1.1 (Eng Manager) and F3.1 (Adversary) look like the same problem (0.94); raised by 2 of 3 seats` |
| Block source | Which panel finding does this chair block confirm? One choice: every panel finding, plus `none`. | 16 of 16 | `Block 1 -> F1.1 (Eng Manager, high c9, eligible at this tier) (1.00)` or `Block 1 -> none of the panel findings (0.93)` |

The question wording is the wording the offline test used, character for character. This
model reads literally, so a reworded question is a new, untested question. The wording
lives once, in `council/signals.py`, and the offline harness in `tools/jev_council/`
imports it from there.

Finding ids have the form `F<seat>.<finding>`. They follow the order in which the chair
was shown the findings. A seat that errored has no findings and still uses up its number.
In the rendered raw panel, lower-confidence findings are moved below the others, so the
second bullet under a seat is not always finding 2. The section therefore prints the seat
name, severity and confidence next to each id.

Pairs at or above 0.85 are merged into groups, so three seats that raise one problem show
as one line with "raised by 3 of 3 seats". Seats that errored are not counted.

The eligibility note on a block source uses the merge gate's own rule
(`council/gate.py::is_candidate`) with the blast-radius tier of the change. The tier is
computed from the changed paths of the code part of a diff, in the same way as the pull
request gate computes it. When the input is not a diff there is no tier, and the note is
left out.

## Where the signals run

| Place | What changed | What did not change |
|---|---|---|
| `council review` | After the chair has answered and the review is printed, one last section is added: `### Jev shadow signals (display only; not used by the chair or the gate)`. It appears in the terminal and the markdown format. | The panel's input, the chair's input, the review text above the section, the exit code. `council review` has no JSON output format, so there is no `jev_shadow` JSON key. |
| `backlog-run` (nightly runner) | Its council step adds the same section to the saved review. It saves Jev's label and confidence in the review's `inputs.json` record under `jev_shadow`. `report` and `show` print one extra line for a worked item: `Jev reads the chair's verdict as: approve_with_conditions (0.91) [shadow, display only]`. | `review_status`, the readiness state, the reasons, `report.json`, the rule for suggesting `approve`, `approve` itself. A review whose readiness is "unknown" stays "unknown". |
| `council sweep` (weekly) | After the sweep has merged findings with matching text and the chair has written its summary, Jev is asked which of the remaining findings describe the same problem. Groups are printed as one note after the coverage line: `Jev would also group: finding 2 + finding 3 (0.93)`. | Which findings are reported, their order, the counts, the summary, the exit code. With nothing to group, the report is byte-identical. |

`backlog-run` has a second, separate use of Jev: the pre-session hold gate in
`backlogrun/gate.py`. It asks one question BEFORE a worker session and may add a hold. The
shadow signals run AFTER the council review and decide nothing. The two share the shared
client and nothing else: no code, no state, no log. An item the gate holds is never worked,
so it gets no review, no shadow call, no display line and no shadow log line. An item the
gate lets through is asked once by each; every call is one row in the shared usage ledger
(project `backlog-run` for the gate, `ai-harness` for the signals), and the review is one
line in the shadow log. Both are off under `JEV_DISABLED=1`. Tests pin each of these points.
The gate does not use the allow list described below. It sends the title and the prompt of
any backlog item it screens, for every repository. That is a different kind of text (the
owner's task wording, not review text about a repository's code), and its scope is that
gate's own matter.

The signals do not run in `council/review.py::run_pr_review`, which is the pull request
merge gate used by CI. That file was not edited, and a test checks that it makes no Jev
call even when a key is present. `council ask` and `council compare` do not run them
either.

## What is sent, and the data rule

Sent to TypeSafe: the chair's recommendation text, the text of panel findings (clipped to
about 320 characters when used as answer options), and each chair block's point and
reason. The diff and the reviewed files are never sent. Before anything leaves, email
addresses, URL query strings and long token-shaped strings are removed from the whole
request, the answer options included.

The owner's data rule of 2026-09-19 allows council text, code snippets and diffs to go to
TypeSafe for council work. The council applies it as an **allow list** of repositories. A
deny list is not fail-closed: a new private repository would be in scope until someone
remembered to add its name. With an allow list, a repository nobody has decided about gets
no Jev call.

The allow list is `IN_SCOPE_REPOS` in `council/jev.py`. It holds the five repositories of the
offline test dataset, which are the repositories the signals were measured on:

- `ai-harness`
- `swimtrack`
- `swimtrack-website`
- `ultimate-portugal`
- `aris-management-website`

A repository is in scope only when all three checks pass:

1. Its name is on the allow list. The match is exact: `ai-harness-fork` and `Ai-Harness` are
   refused.
2. Its name is not on the explicit refusal list, `OUT_OF_SCOPE_REPOS` in `council/jev.py`.
   That list names the repositories that hold student, customer, mail, tax or finance data,
   and the romance repositories (unpublished manuscripts). It is a second check on purpose:
   a name that lands on both lists by mistake is still refused. A test checks that the two
   lists never overlap.
3. None of the shared out-of-scope words (`OUT_OF_SCOPE` in `jev/scope.py`) appears in the
   repository name or in the backlog item id. An allowed repository is still refused for an
   item whose id names private work, for example an id that contains `sat-prep` or `bebop`.

Each list exists once. The offline harness in `tools/jev_council/` imports the rule from
`council/jev.py`, and it still loads the same 28 reviews and 207 findings as before.

**How to add a repository.** Adding one is an owner decision. After the decision, add the
name to `IN_SCOPE_REPOS` in `council/jev.py` and to the pinned list in
`tests/test_council_jev_shadow.py` (`ALLOWED_REPOS`), then refresh the installed command
(see Rollout). The test pins the list so that it cannot grow by accident.

The shared scope record, `ALLOWED_NOTE` in `jev/scope.py`, now states the council decision in
one sentence: for council work only, Jev may receive the council's own words (panel
findings, chair text) and code snippets and diffs. The not-allowed list there is unchanged:
personal email, the private wiki, children's or student data, customer data, financial and
tax records. Repositories that hold such data stay out of the council signals.

The romance (manuscript) repositories are kept OUT of the council signals by this change,
even though `jev/scope.py` records unpublished manuscript prose as allowed since 2026-09-19.
The council decision was given separately from the manuscript decision and did not name
those repositories, so the stricter reading was kept. If the owner wants council reviews of
those repositories to get the signals too, the change is small: move their names from
`OUT_OF_SCOPE_REPOS` to `IN_SCOPE_REPOS` in `council/jev.py`, as described above.

How the repository is decided:

A repository's identity is git's own answer, not a folder name. The code asks git for the
repository that holds a path and takes the name of its main checkout. That name is right
inside a linked git worktree, whose own folder is named after a session, and through a
symbolic link. The question to git has three possible answers, and they are never mixed up:

| Answer | Meaning | Effect |
|---|---|---|
| a name | The path is in that repository. | The name is checked against the allow list. |
| outside | Git itself said: not a git repository. | See `council review` below. Everywhere else: refuse. |
| unknown | It could not be resolved: git is missing, the question timed out (3 seconds), a permission or ownership error, an unreadable path. | Refuse. There is never a fallback. |

- `council review`: the working directory's repository, and the reviewed path's repository
  when a path is given. Every repository found this way must be in scope. A path that is
  *outside* any repository (a diff saved under `/tmp`) is judged by the working directory
  alone. A path whose repository is *unknown* is refused; the working directory is not used
  in its place. An *unknown* working directory is refused too.
- `backlog-run`: the identity of the repository the item was worked in, and that identity
  must ALSO equal the `repo` field of the backlog item. Any mismatch means no Jev call. So
  a folder that is merely named like an allowed repository (a plain folder, a symbolic link
  to another repository, a worktree of another repository) gets no call, and neither does an
  item whose `repo` field is a path and not a name. The item id is checked against the word
  list. The work queue runner uses the same code path, and its one repository is not on the
  allow list.
- `council sweep`: the repository that holds the swept path. The sweep code cannot know
  where its chunks came from, so the command passes the name in. Without a name there is
  no Jev step.
- A repository that cannot be named, or that is not on the allow list, is refused. This
  fails closed.

Limits of the rule that a reader should know:

- **Text on standard input, or a diff saved outside any repository, is judged by the
  working directory's repository alone.** The command cannot see where such text came
  from. If a diff from a private repository is piped into `council review`, or saved under
  `/tmp` and reviewed, while the working directory is an allowed repository, the signals
  run on it. To avoid this, run the review from inside the repository the text belongs to,
  or set `COUNCIL_JEV=0` for that command.
- The allow list holds names, not locations. A full clone of another repository whose main
  checkout folder carries an allowed name is treated as allowed: git reports that folder
  name, and nothing else on disk says which project a clone belongs to. Under the runner
  this needs the clone to sit in the projects folder under the allowed name, in the place of
  the real repository.
- The question wording was tested on `code-review` panels. `council review` also runs the
  signals for other panels (a single document goes to `spec-review`), where the words "code
  review" and "code change" in the questions fit less well. The log records the panel, so
  those lines can be set aside when the numbers are compared.
- The word list matches parts of words. An item id that contains "tax", "rent" or "mail"
  inside another word is refused. That costs one missing shadow record and nothing else.

## Kill switch and failure behaviour

- `COUNCIL_JEV=0` (also `off` or `false`) turns every council use of Jev off, including the
  extra line in `backlog-run report` and `show`. Set it in the shell, in the crontab line,
  or in the environment of the process that runs the command. `JEV_DISABLED=1`, the shared
  client's switch for every Jev caller, has the same effect here.
- No key means off. The key is `TYPESAFE_API_KEY`, read from the environment, or else from
  `~/.env` read as plain text. It is never logged and never passed to the runner's worker
  session, whose environment stays an allow list.
- The model version is pinned in `council/jev.py` and passed on every call, so a change of
  the shared client's default cannot move the 0.85 cut. An answer from any other version is
  refused.
- The shared client never follows an HTTP redirect, because a redirect would carry the key
  to another host, and it removes the key from every error message.
- Each call is counted in the shared usage ledger (`~/.local/state/jev/usage.jsonl`, counts
  and cost only) as project `ai-harness`, tasks `council-verdict`, `council-source`,
  `council-same` and `sweep-same`. `jev usage --days 7` shows what it costs.
- The whole pass has a 20 second budget, and it is a real deadline. A call is not retried.
  Each call is given a timeout of 8 seconds or the time that is left, whichever is less, so
  a call that blocks for its whole timeout still ends by the deadline. No call starts with
  less than 1 second left. In `council review` the questions to git that decide the scope
  count inside the same 20 seconds; each of them has its own 3 second limit everywhere.
  Under `backlog-run` that one question to git is asked before the panel runs, so it is
  outside the 20 seconds and bounded by its 3. The review is printed before the first Jev
  call, so an outage delays only the extra section. One limit remains: the timeout is
  applied by the HTTP library to each network step (connect, read), so a server that
  answers very slowly, a little at a time, could run past it. The replies are a few hundred
  bytes and arrive in one piece.
- A failed call or an answer in the wrong shape costs that one answer. The section then
  says how many calls failed. No failure in this step can fail a review, change its exit
  code, or change a recorded review status. Tests cover each of these.

## The shadow log

One JSON line per review or sweep is appended to
`~/.local/state/council/jev-shadow.jsonl`. `COUNCIL_JEV_LOG` moves it. The folder is created
when needed, and the file is readable by its owner only. Each line is written as one write,
ending in a newline, under an exclusive lock on the file, so two reviews that finish
together cannot tear each other's lines. The lock is tried for about one second and never
waited on: if another writer holds it that long, the line is dropped. A log that cannot be
locked or written is ignored and never fails a review.

The log holds no review text. It holds ids, labels, numbers and one hash:

- `ts` (UTC), `kind` (`review` or `sweep`), `repo`, `panel`, `item` (the backlog item id,
  when there is one), `model`
- `verdict`: label, confidence and the three probabilities
- `pair_scores`: every pair that was asked, with its score; `seat_agreement` and `clusters`:
  the pairs and groups at or above `cut`; `pairs_total`, `pairs_asked`, `truncated`
- `block_sources`: for each chair block, the linked finding id or `none`, the confidence,
  and whether the linked finding was eligible at the tier
- the chair's side, for comparison: `chair_review_status`, `chair_blocks`, `chair_error`,
  `tier`, and `findings` (id, seat, severity, confidence, eligible)
- `recommendation_sha256`, so that one line can be matched to a saved review without storing
  the text
- `calls`, `seconds`, `errors`, `budget_exhausted`

### How to compare it later

Read the log with a few lines of Python. Three questions are worth asking once a few weeks
of lines exist.

```python
import json, pathlib
rows = [json.loads(line) for line in
        pathlib.Path("~/.local/state/council/jev-shadow.jsonl").expanduser().read_text().splitlines()]
reviews = [r for r in rows if r["kind"] == "review" and r["verdict"]]

# 1. Does the verdict label agree with the chair when the chair did give a status?
agrees = {"clean": {"approve"}, "changes_requested": {"approve_with_conditions", "request_changes"}}
known = [r for r in reviews if r["chair_review_status"] in agrees]
hits = sum(r["verdict"]["label"] in agrees[r["chair_review_status"]] for r in known)
print(f"verdict agrees with the chair on {hits} of {len(known)}; "
      f"{len(reviews) - len(known)} more reviews had no chair status and now have a label")

# 2. How often does a chair block link to no panel finding, or to one that was not eligible?
blocks = [b for r in reviews for b in r["block_sources"]]
print(sum(b["source"] == "none" for b in blocks), "of", len(blocks), "blocks link to no panel finding")
print(sum(b["eligible"] is False for b in blocks), "link to a finding below the gate's bar")

# 3. Is 0.85 still the right cut? Look at the scores just under and just over it.
near = sorted(p["p"] for r in reviews for p in r["pair_scores"] if 0.6 <= p["p"] <= 0.95)
print(near)
```

Question 2 is the evidence for the provenance work: it shows how often the gate counts a
block that no eligible panel finding supports. Question 3 needs a person to read the saved
reviews for the pairs near the cut, as the offline test did. Use `recommendation_sha256` or
`item` to find them.

## What is deliberately not built

- **No chair input.** The chair never sees a Jev answer. "Raised by N seats" as a labelled
  fact in the chair's digest is a later step, once the log shows the cut holds on reviews it
  has not seen. A comment in `council/signals.py` marks where it would hook in.
- **No gate rule.** `council/gate.py` and `council/review.py` are unchanged. The real
  provenance change (stable finding ids in the chair's digest, `sources` on each chair
  block, a gate that counts only sourced blocks) is separate work. When it is built, the
  block source signal may fail a review closed. It may never pass one.
- **No readiness rule.** A confident `approve` label does not make an item ready, and a
  confident `request_changes` label does not yet make a ready item less ready.
- **No code-slice check and no "can it merge exactly as is?" question.** Both failed the
  offline test.
- **No finding re-scoring.** It tested half well and needs more labelled data first.

House rule for every later step: a Jev signal may inform, or make things stricter. It must
never waive a block. Every use keeps a kill switch and a fallback to the behaviour without
Jev.

## Rollout

The `council` and `backlog-run` commands on this machine are installed with pipx from the
repository. Merging this branch changes nothing until the install is refreshed:

```
pipx install --force ~/projects/ai-harness
```

The weekly sweep and the nightly runner both call the installed commands, so they pick the
change up at the same moment. To run with the shadow off from the first day, add
`COUNCIL_JEV=0` to the two crontab lines. To check that it is on, run one `council review`
in an in-scope repository and look for the last section and a new line in the log.

Cost, from the offline test: about 0.6 seconds and well under a hundredth of a US cent per
call. A typical review makes 10 to 20 calls.

## Checked on 2026-09-19

- Full test suite: 1689 passed, 1 skipped; 163 of these tests are new. No test reaches the
  network: the suite-wide test configuration sets `JEV_DISABLED=1` and `COUNCIL_JEV=0` and
  sends both logs to temporary files, and the new test file puts a tripwire on the shared
  client's HTTP opener that fails any test that reaches it.
- The three questions built by the new code are byte-identical to the requests the offline
  test sent.
- One live run on a saved ai-harness review: 9 calls, 0 errors, 5.4 seconds. The verdict
  label and the agreeing pair matched the offline test's answers for that review. No saved
  in-scope review carries chair blocks, so the two blocks in that run were constructed: one
  restated a panel finding and was linked to it (0.93), one was about something no seat
  raised and was linked to `none` (1.00). One more live call afterwards confirmed the path
  through the shared client, including its usage ledger row.
- After the council's own review of this change (repository identity, the hard deadline, the
  three-answer scope, the locked log), one more live run of 3 calls on the same saved review:
  0 errors, 1.9 seconds, the same verdict and the same agreeing pair. The linked worktree it
  ran in was identified as `ai-harness`, each call was handed its 8 second timeout, and the
  log line was one intact line in a file readable by its owner only.
