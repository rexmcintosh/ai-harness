# backlog-run

The 3am backlog runner and the morning-review tool for `~/projects/backlog`. (The name is from its
first schedule. Since 2026-09-21 cron fires it at 22:00 UTC, two hours before the DIEM reset.)
Spec: `../docs/superpowers/specs/2026-09-03-backlog-runner-design.md`.
Contract it obeys: `~/projects/backlog/README.md` § "Safety contract for the 3am runner".

Each `open` item is worked by a cold, headless Claude Code session inside its own git
worktree on a fresh `claude/bl-<slug>` branch of the item's repo, council-reviewed, and left
as `in_review` (or `held`) for you. Nothing is ever pushed or merged by the clock.

## Commands

    backlog-run work --dry-run           # what tonight would do; changes nothing
    backlog-run work                     # the nightly run (cron); open -> in_review | held
    backlog-run work --only <id>         # one specific item (also --repo NAME, --max-items N)
    backlog-run rework <id> --dry-run     # show the rework target; changes nothing
    backlog-run rework <id> --no-notify   # continue its existing held/in_review branch
    backlog-run report                   # morning report, numbered
    backlog-run show 1  |  diff 1        # details / full diff (number from the report, or an id)
    backlog-run approve 1 3              # merge --no-ff into main, push, delete branch, archive done
    backlog-run approve <id> --held      # same for a held item's branch (read its note first)
    backlog-run drop 2                   # delete branch (journaled), archive dropped
    backlog-run hold <id> "why"          # park an item;  backlog-run reopen <id> returns it
    backlog-run list

`work` flags: `--max-items` (2) · `--item-timeout` seconds (3600) · `--deadline` seconds
(10800) · `--budget-usd` per session (20; 0 = none) · `--model` (sonnet) · `--effort`
(medium) · `--no-council` ·
`--no-notify` · `--keep-worktree`. `rework` accepts the same session flags except
`--max-items` and `--deadline`; it always targets one existing branch.

## Rework

`rework <id>` continues one `held` or `in_review` item on the branch it already has. It is
the only command that touches a branch with work on it; the nightly `work` still holds
such items.

It refuses, with one sentence on stderr and exit 1, when:

- the id is not an active item (`not an active item`);
- the item is `open` (`item is open, not held/in_review`): use `work --only <id>`;
- the item has no `branch` field matching `claude/bl-<slug>` (`item has no matching
  backlog-run branch`);
- the branch is gone (`branch claude/bl-<slug> does not exist`);
- the repo or its local default branch cannot be resolved.

A refusal changes nothing. `--dry-run` runs the same checks, then prints the status, repo,
branch, head, commits ahead, worktree path and the number of review notes. It takes no
lock, starts no session, and leaves `reviewed_sha` alone. A real rework clears
`reviewed_sha` first, so the branch needs a fresh review before `approve`.

The session prompt ends with a numbered `REVIEW TO ADDRESS` section, oldest first. It
lists every `Rex's review (<date>): ...` paragraph in the item's prompt, plus the note when
the note starts with `Rex's review`. No review notes, no section.

## What the session gets

- cwd = the worktree; prompt = runner contract + the item's `prompt`; the repo's own
  CLAUDE.md applies.
- A whitelist environment (no `~/.env`, no Claude-session vars); `git push` to any
  https/ssh/git remote rewritten to a dead path via `GIT_CONFIG_*` `pushInsteadOf` (local-path
  remotes, i.e. temp repos in test suites, still work); deny rules for push/deploy/send/spend
  commands (held in `~/projects/.backlog-run/claude-settings.json`); no MCP servers.
- It must end with a `RUNNER-OUTCOME: done|held|failed` block; `held` is its escape hatch
  for anything that needs a human or an outward action.

## Guarantees under failure

- A new item is only transitioned if it is **still `open`** when the run finishes. A rework
  item must still have its exact starting status. If you
  held/reopened it meanwhile, your state wins and the run's result becomes a
  `runner: CONFLICT …` note (the branch is kept).
- An empty leftover `claude/bl-*` branch (no commits, no worktree) is reclaimed on the next
  run, journaled; a leftover branch **with** work holds the item instead.
- Every branch that carries work is council-reviewed — held ones too. A council failure is
  recorded as the verdict (`REVIEW FAILED: …`); the item still goes to `in_review` — the
  morning report shows it, you review by hand.
- `approve` takes `in_review` items; a held item's branch needs `--held`. Both require the
  branch head to equal `reviewed_sha`; advisory readiness does not add another gate.
- `repo:` must resolve inside `~/projects` (no `..`, no absolute escapes) or the item is held.
- `approve` records the merge in the backlog **before** deleting the branch and is safe to
  re-run (an already-merged branch is not merged twice). `drop` records before deleting.
- An archive move writes `archive.yaml` first; an id found in both files is reconciled in
  favour of the archive on the next write.
- Lock contention exits 75 (`EX_TEMPFAIL`), so a skipped nightly run shows up in `cron.log`.

## Item fields the runner writes

`status`, `branch`, `worked`, `council`, `note`, `session` (claude session id), `cost_usd`,
`reviewed_sha` (the exact branch commit submitted to review).
`approve` adds `merged`, `merge_commit`; `drop` adds `dropped`. Both move the item to
`archive.yaml`.

## State

`~/projects/.backlog-run/`: `report.md` + `report.json` (number → id), `reviews/<id>.md`
(full council output), `runs/<ts>-<id>.json` (raw session result), `journal.log`
(branch deletions: ts, repo, branch, sha, action — restore with `git branch <name> <sha>`),
`lock`, `cron.log`.

Environment overrides: `BACKLOG_PATH`, `BACKLOG_RUN_STATE`, `BACKLOG_RUN_PROJECTS`,
`BACKLOG_RUN_CLAUDE` (binary), `BACKLOG_RUN_GIT=0` (no backlog commits),
`BACKLOG_RUN_TG=0` (no Telegram), `BACKLOG_RUN_TG_CHAT`, `BACKLOG_RUN_TG_SEND`,
`BACKLOG_RUN_ENV_FILE` (where the Venice key is read from for the council step).

Council reads only `VENICE_SECOND_OPINION_KEY`. It never falls back to the old
Council or generic Venice keys. The worker receives none of these credentials.

## Cron

    0 3 * * *  /home/dev/.local/bin/backlog-run work >> /home/dev/projects/.backlog-run/cron.log 2>&1

## Install / update

    pipx install --force ~/projects/ai-harness

## Tests

    python3 -m pytest tests/test_backlogrun.py -q

They run the whole `work`/`approve`/`drop` flow against temp git repos with a fake `claude`
binary (no network, no real sessions) and assert the scrubbed environment, the pushurl
guard, the state transitions, the YAML round trip and the lock.

## Review readiness

`in_review` means the work awaits your decision. It does not mean checks passed.
The report now shows one of four evidence states for each worked branch:

| State | Meaning | Suggested next step |
|---|---|---|
| Ready for your review | Explicit completed outcome, explicit clean council verdict, and the declared checks passed for the current branch commit. | Read the full evidence, then decide. |
| Changes requested | The council names unresolved fixes or conditions. | Address the listed conditions. |
| Review or checks failed | The runner, reviewer, synthesis, or a required check failed. | Inspect the failure evidence. |
| Review readiness unknown | Evidence is missing, malformed, incomplete, contradictory, or covers another commit. | Inspect or refresh the evidence. |

The outcome marker must occupy its own line; merely mentioning it in prose cannot
establish completion. Markdown decoration around the marker is supported.
The report preserves full conditions and links to the full review. Its JSON adds
`review_readiness`, keyed by item ID; the existing `numbers` mapping stays intact.
`show` displays the same readiness and full evidence. Short notifications state
readiness and point to `show`, instead of clipping a possibly conditional verdict.
Only ready rows suggest the numbered `approve` command. This is display guidance, not
a readiness gate. `approve` still uses the existing human authority, including `--held`,
but now refuses when the branch head differs from its exact recorded review SHA. There is
no automatic merge.

`report` and `show` may print one more line under the readiness lines, for example
`Jev reads the chair's verdict as: approve_with_conditions (0.91) [shadow, display only]`.
It is a second reading of the chair's written recommendation by a small model (Jev, see
`docs/council-jev-shadow-2026-09-19.md`), saved as `jev_shadow` in the review's
`inputs.json`. It is display only: it never changes the readiness state, the reasons,
`report.json`, which rows suggest `approve`, or `approve` itself. The council step runs in
the parent runner process, so the key (`TYPESAFE_API_KEY`, else `~/.env` read as text) never
enters the worker session's environment. Only items in repositories on the shared allow
list (`IN_SCOPE_REPOS` in `jev/scope.py`) get a Jev call. The repository is identified by
git (the main checkout's name, not a folder name), and that identity must also equal the
item's `repo` field; any mismatch means no call. `COUNCIL_JEV=0` turns the step and the line
off.

The pre-session hold gate (`docs/contracts/backlog-run.md`) follows the same allow list, by
the item's `repo` field: an item from any other repository, or with none, is never sent to
Jev. It skips that gate and runs as it did before the gate existed.

For new items, an optional `required_validations` list declares check names before
the session starts, for example `required_validations: [pytest, lint]`. Names are
labels, not commands that the parent runner executes. The session follows the
repo's instructions to run them and returns a one-line `RUNNER-VALIDATIONS` JSON
array with `name`, `branch_sha`, `status` (`passed`, `failed`, or `unknown`), and
`evidence` (command, exit code, and output, as a string or JSON object). The runner saves that evidence and
includes it in the council's input. Results are explicitly **session-reported**;
the parent does not independently rerun the commands. All reported checks must
refer to the reviewed commit. Missing or malformed results cannot imply success.

An absent required list means validation coverage is unknown, even if the review
is clean. An explicit empty list is appropriate only for work with no required
commands. Neither the session nor this upgrade fills in or relaxes that list.
Existing backlog records are not rewritten. A historical prose verdict, including
one that says "approve", stays unknown with its old full review still accessible.

Each new attempt creates `runs/<timestamp>-<id>.json` before starting the session,
then fills in the raw result. Evidence lives under `reviews/` with the same stem:
`.md` (complete review and conditions), `.validation-N.txt` (reported check output),
`.inputs.json` (version 1 inputs), and `.readiness.json` (derived snapshot). The
legacy `reviews/<id>.md` path remains the latest full review. Prior attempt files
remain intact. Reports recompute from the inputs and the current branch commit;
they never trust a cached ready snapshot. An unfinished or malformed newest
attempt cannot reuse a clean older review.

The evidence validator checks types, readable files inside the state directory,
UTC timestamps, commit identity, and contradictory outcomes. A later complete
review supersedes older evidence for the same commit. Conflicting duplicate IDs
or conflicting newest records remain unknown. A truncated review input or an
incomplete panel also remains unknown; reviewer or synthesis errors are failures.

Run the focused checks with:

    python3 -m pytest tests/test_backlogrun.py tests/test_backlog_readiness.py tests/test_synthesize.py -q

For older review records without `reviewed_sha`, the owner can inspect the exact current review and use `approve <id> --reviewed-sha <full-40-character-SHA>`. That explicit identity applies to one item. Romance Ops passes its displayed review SHA into this same entrypoint. The merge names the immutable commit, so movement of the branch after validation cannot add unreviewed commits. A revised brief during rework preserves the owner's item and stores `runner_conflict` instead of marking the newer request complete.
