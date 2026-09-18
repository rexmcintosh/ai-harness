# Shared spine: what the last step of the chain should be

- **Date:** 2026-09-18. **Status:** recommendation only. No file was moved and no protocol text was
  changed to produce this document.
- **Closes:** backlog item `2026-07-23-define-shared-spine-step`.
- **Source:** `docs/harness-engineering-assessment-2026-07-20.md`, sections 9 to 13.

## 1. Decision for the owner

**Recommendation: define the step, but narrowly. Rename it from "extract shared spine" to "declare
and guard the spine". Move no protocol text into a new home.**

The spine already exists. `~/.claude/CLAUDE.md` is the single owner of the working agreement, and
every other reader points to it instead of copying it. What is missing is smaller than an
extraction: the owner file is not kept under version control in practice, one stale copy survives,
and nothing checks for drift.

Why:

- **The extraction has already happened.** 0 of 21 per-repo instruction files copy the merge
  protocol. Codex, the site-flow skills, `/close`, and four repo files all point to the owner file.
  Runtime rules live beside their code in `docs/contracts/` (19 contracts plus an index).
- **The real gap is versioning, not location.** 107 of the owner file's 228 lines (47%) are
  uncommitted. Six whole sections have never been committed. The last commit that touched the file
  is dated 2026-08-04. No job commits `~/.claude`.
- **One drifted copy remains and nothing would catch the next one.** `setup/templates/CLAUDE.md`
  still carries the weak merge protocol that was removed from `~/projects/AGENTS.md` on 2026-07-23.
  No test or script reads any of these files.

**Cost:** five small backlog items (section 7). The unattended runner can draft items 2, 3 and 4.
Items 1 and 5 need an attended session because they touch `~/.claude`.

**Risk:** low, not zero. No files move. Item 2 changes what agents in future projects read, because
it rewrites the template's merge section. A fallback rule and a test cover a pointer that does not
resolve. Item 5 changes protocol wording and needs the owner's sign-off on the exact text. Item 1
changes no text and has a snapshot and a rollback step.

**Needed from the owner:** one yes or no. *Approve replacing the chain's last step with the five
items in section 7?* On yes, the held item closes and the five items are appended to the backlog.

## 2. What the assessment actually says

The word "spine" appears twice in the assessment, and is never defined. The first use is the
ratified chain at lines 595 to 596 (section 11, Correction A):

> **version/snapshot → contracts (+ loop-retirement) → retrieval instrumentation →
> context retirement → extract shared spine**

The second is at lines 662 to 663 (section 13): "the fix is 90% gardening on the foundation we have
(contracts → instrument → retire → spine)".

The nearest thing to a definition is section 10. It says the consolidation plan "never names a
**target information architecture**", and it routes "settled procedures → versioned repo contracts;
enforceable lessons → tests / lints / policy checks". Two earlier passages set the rule the spine
must obey. Gap 1 (line 117): "The fix is not to sync them. It is to give the protocol **one** owner
and make the other a pointer." Gap 4 (line 195): "If it matters, it belongs in a verifier owned by
the repo."

**Working definition used here.** The spine is the set of protocol texts that every agent must obey
in every repo, with exactly one owner file per protocol, held under version control, and guarded by
a check that reports when a copy appears or a pointer breaks. It belongs to the operational plane.
Memory files and wiki articles belong to the personal-state plane, which section 12 assigns to the
bake-off, so they are out of scope here.

## 3. Inventory: where protocol text lives on 2026-09-18

Scope searched: `~/.claude` (owner file, 7 commands, first-party skills, `delegate/venice.md`),
`~/.codex/AGENTS.md`, `~/projects/AGENTS.md`, every `CLAUDE.md` and `AGENTS.md` in the 22 git repos
under `~/projects` (archives, worktrees and `node_modules` skipped), `~/projects/backlog/README.md`,
`backlogrun/cli.py`, `backlogrun/README.md`, `setup/templates/`, and `docs/contracts/`.

| Protocol | Owner today | Other places the text appears | Copy or pointer | State |
|---|---|---|---|---|
| Merge to `main` | `~/.claude/CLAUDE.md` lines 151 to 176 | `~/projects/AGENTS.md`; `ship-site` and `adjust-site` skills; `/close`; 2 romance site repos; `setup/templates/CLAUDE.md`; `backlogrun/cli.py` rule 10 | Pointers, except the template (a copy) and rule 10 (a stated exception) | **Template has drifted.** Rest is clean |
| Review routing | Owner file lines 107 to 124 | `delegate/venice.md`; backlog README rule 3; `backlogrun/cli.py`; `docs/contracts/backlog-run.md` | Mixed | **Two commands named for one step** (finding 4) |
| Venice routing and keys | `setup/delegate-venice/venice.md` (versioned), installed to `~/.claude/delegate/venice.md` | Marked pointer block in the owner file and in both Delegate skills; key names in 6 contracts | Pointer plus installed copy | Installed copy is byte-identical to the source (93 lines) |
| Runner safety contract | `backlogrun/cli.py` `compose_prompt` (11 rules, plus 2 for rework passes) | Backlog README (4-rule summary); `backlogrun/README.md`; `docs/contracts/backlog-run.md` | Summaries of the code | Consistent. A test pins "Never push" in the prompt |
| Session closure and backlog | Owner file lines 187 to 200 | `/close` command; backlog README; `~/projects/AGENTS.md` | Pointers | Clean |
| `session-gc` lifecycle | Owner file lines 178 to 185 | `/sweep`; `sessiongc/README.md`; `docs/contracts/session-gc.md` | Pointers | Clean |
| Response style | Owner file lines 3 to 48 | None | Single copy | Clean |
| External documents | Owner file lines 50 to 60 | None | Single copy | Clean, but uncommitted |
| Notion connector rule | Owner file lines 69 to 98 | `sat-prep/AGENTS.md` (5-line restatement); `~/.codex/AGENTS.md` (older general rule) | One copy, one older rule | Minor drift (finding 6) |
| Supabase rule | Owner file lines 100 to 105 | None | Single copy | Clean |
| Secrets handling | No single section. Stated inside several owner-file sections | `venice.md`; template; 3 repo files; "names only" lines in contracts | Scattered but consistent | No contradiction found |
| Site-flow precedence | Owner file lines 139 to 149 | 5 site-flow skills; 2 romance site repos | Pointers | Clean |
| Per-loop contracts | `docs/contracts/*.md` (20 files) | Cited from `ai-harness/AGENTS.md` | Single copy | Descriptive only. The index states they do not validate or enforce anything |

Per-repo instruction files: 17 of 22 repos have at least one readable file, and one more has only an
unreadable pair (finding 3). There are 21 regular files and 5 symlinks. 4 of the 21 point at the
owner file, as does `~/projects/AGENTS.md`. None copies the merge protocol block. The old repo name
`build-ai-automation-workflow` appears in 0 protocol files. It remains in 45 tracked files in this
repo, all historical documents, test fixtures, or code comments.

## 4. Drift findings

**Finding 1. The owner file is 47% unversioned.** `~/.claude` is a git repo with a remote, 481
tracked files, and 0 unpushed commits. But `git diff --numstat` on `CLAUDE.md` shows 107 lines added
and 4 removed against a committed version of 125 lines. Six sections exist only in the working copy:
External-facing documents, Account connector preference, Automatic approval for required Venice code
reviews, the two approvals dated 2026-09-07, and Venice helper routing dated 2026-09-13. The last
commit touching the file is dated 2026-08-04, 45 days ago. Nine tracked skill files are also
modified, and `skills/synced/` is untracked. No cron entry commits `~/.claude`. Gap 2 asked for "a
revision, a diff, and a revert" for every harness change. For the most important file, that is not
true today.

**Finding 2. One stale copy of the merge protocol is still shipped.** `setup/templates/CLAUDE.md`
holds a 14-line merge section against the owner's 27. It contains 0 mentions of pushing, of the
`git status -sb` check, of the scope clause ("exactly the work in the block"), of branch deletion,
or of the `Push:` line. This is the same defect that Gap 1 named and that was fixed in
`~/projects/AGENTS.md` on 2026-07-23. The template was last changed on 2026-06-04.
`setup/PLAYBOOK.md` line 150 tells the reader to copy it into every new project, and line 155 says
"Never merge", which contradicts both the template and the owner file. That is three versions of one
rule.

**Finding 3. Three repos have a broken `CLAUDE.md` and `AGENTS.md` pair.** Eight repos hold both
names. Three pairs are identical through a symlink. Two are one-line pointer pairs (`swimtrack`,
`swimtrack-coach`). Three are broken:

- `rmpeacockwriter.com`: `CLAUDE.md` links to `AGENTS.md` and `AGENTS.md` links to `CLAUDE.md`.
  Neither can be read. Commit `2792316` (2026-09-15, "docs: cutover runbook, project rules, readme")
  replaced the real `AGENTS.md` with the second symlink. The project rules that commit meant to add
  do not exist in the repo. Until item 4 merges, this repo has the status **held: broken
  instructions**.
- `romance-elliecalloway.com` and `romance-tessacross.com`: `CLAUDE.md` holds the project rules (109
  and 131 lines). `AGENTS.md` is 22 lines of Astro boilerplate with no pointer. An agent that reads
  only `AGENTS.md` sees no project rules. The global Codex pointer softens this by telling Codex to
  read both names.

**Finding 4. The owner file names two commands for one required step.** Line 108 sends a
working-tree diff to `council review --diff`, which line 66 says needs `VENICE_API_KEY`. Line 223
says to use `venice-delegate council-review --diff`, which loads `VENICE_SECOND_OPINION_KEY`. The
backlog README rule 3 names the first form. The wrapper calls the same panel, but an agent is given
two instructions and two credentials for one gate.

**Finding 5. Dated one-off approvals sit inside the standing agreement.** Two sections dated
2026-09-07 (25 lines, 11% of the file) record a single batch budget and its continuation. One sits
under the "Session closure" heading, where it does not belong. Gap 3 ("everything accretes; nothing
retires") applies to the owner file itself.

**Finding 6. The global Codex pointer carries one older rule.** `~/.codex/AGENTS.md` (18 lines,
identical to `GUIDANCE` in `setup/claude-codex-mirror/settings.py`, installed 2026-09-06) tells
Codex to reuse the Claude client for account connectors. The owner file overrode that for Notion on
2026-09-09. The pointer also says to follow the owner file, so the newer rule wins, but the two
texts disagree. `~/projects/AGENTS.md` is in no git repo and has no versioned source.

**Finding 7. Sanctioned exceptions are recorded only where they apply.** Two exceptions to the merge
protocol exist: `backlog-run` (prompt rule 10 suspends the protocol for unattended work, and
`backlog-run approve N` merges and pushes on a human command), and the Ultimate Portugal engine
(scheduled runs push operational data to `main`). Both correctly live with their code. The owner
file lists neither, so a reader of the owner file alone would call them violations.

Not counted as spine drift: 18 wiki articles and 19 memory files mention the merge protocol. They
describe it, and no agent is told to read them as rules. They belong to the held wiki retirement
item.

## 5. Options considered

| Option | What it means | Verdict |
|---|---|---|
| A. Extract | Create a new versioned home (for example a `spine/` directory or repo), move protocol text into it, and generate `CLAUDE.md`, `AGENTS.md` and the template from it | **Reject.** It rebuilds what exists. `~/.claude` is already the versioned home, and Claude Code loads `~/.claude/CLAUDE.md` by that fixed path. A generator adds a build step and a second place where the text lives |
| B. Drop | Declare the step done because earlier steps covered it | **Reject, narrowly.** The shape is done, but findings 1 to 3 are real, and finding 1 is the exact property (one versioned core) the step exists to deliver |
| C. Declare and guard | Write down the ownership map, fix the three live defects, add one drift check | **Recommend.** It is mechanical, it moves nothing, and it turns prose rules into a verifier, which is what Gap 4 asked for |

## 6. The spine, defined

**Rule:** one protocol, one owner file. Every other file either points to the owner or states a
named exception. Copies are defects.

**In the spine (global, one owner each):**

| Owner file | Owns | Versioned in |
|---|---|---|
| `~/.claude/CLAUDE.md` | Response style, external documents, merge protocol, review routing, skill precedence, `session-gc` house rule, session closure, connector and Supabase rules | `~/.claude` repo |
| `~/.claude/commands/*.md`, `~/.claude/skills/*` | The procedures the owner file names (`/close`, `/sweep`, site-flow skills) | `~/.claude` repo |
| `setup/delegate-venice/venice.md` | Venice routing policy. `~/.claude/delegate/venice.md` is its installed copy | `ai-harness` repo |
| `setup/claude-codex-mirror/settings.py` (`GUIDANCE`) | The global Codex pointer text | `ai-harness` repo |
| `backlogrun/cli.py` `compose_prompt` | The unattended runner's rules | `ai-harness` repo, pinned by test |
| `docs/contracts/*.md` | Per-loop purpose, authority, evidence | `ai-harness` repo |

**Stays per-repo, and must not move:** project purpose and architecture; build, test and deploy
commands; repo-specific secrets by name; framework notes (the Astro and Next.js boilerplate); scoped
tokens for that project; and named exceptions to a global rule (the Ultimate Portugal engine's
pushes to `main`).

**Target layout:** unchanged. No new directory, repo, or generator. The only new files are a
one-page ownership map (`docs/spine.md`) and the check with its allowlist and tests.

## 7. Migration order

Each item is one backlog entry. **Runner-safe** means branch-contained and fit for the unattended
nightly run. A human still approves every merge. **Attended** means it touches `~/.claude` or live
configuration, which the runner's rule 1 forbids.

**Item 1. Commit the owner file.** Mode: attended, about 20 minutes.
- Target: the `~/.claude` repo. Paths: `CLAUDE.md`, the 9 modified files under `skills/`, and the
  untracked `skills/synced/`.
- Why: finding 1. 107 lines of the working agreement have no revision or revert.
- Do, in order: (1) Record the pre-commit sha from `git -C ~/.claude rev-parse HEAD`. (2) Snapshot:
  copy `CLAUDE.md` to `~/.claude/backups/CLAUDE.md.<date>.pre-commit` and save
  `git -C ~/.claude diff` beside it as `pre-commit.<date>.patch`. That folder is git-ignored. (3)
  Review the diff. Commit `CLAUDE.md` and the skill files in logical commits. Decide whether
  `skills/synced/` is tracked or ignored. (4) Push.
- Constraints: change no wording. The working tree is the live configuration, so `cmp` of
  `CLAUDE.md` against the snapshot must show no difference at the end.
- Rollback: before the push, `git -C ~/.claude reset --soft <pre-commit sha>` undoes the commits and
  leaves the files alone. After the push, use `git revert`, then copy the snapshot back over
  `CLAUDE.md` and run `cmp` again, because a revert rewrites the live file.
- Done when: `git -C ~/.claude status --porcelain` and `git -C ~/.claude log origin/main..HEAD` both
  print nothing; `cmp` is clean; the report states the pre-commit sha and the snapshot path.

**Item 2. Replace the stale template copy with a portable pointer block.** Mode: runner-safe to
draft. The owner reads the new wording before the merge.
- Target: `ai-harness`. Paths: `setup/templates/CLAUDE.md`, `setup/PLAYBOOK.md` lines 148 to 155,
  and one new test under `tests/`.
- Why: finding 2. The template is the seed for the next drifted copy.
- Do: replace the 14-line "Merging to `main`" section with a short pointer block of three parts. (1)
  The operator's global working agreement governs merging when one exists. (2) Where to find it: a
  path line that the operator fills in when copying the template. `~/.claude/CLAUDE.md` is named as
  the Claude Code default, not as the only source. This is the documented local override, and the
  PLAYBOOK must explain it. (3) A conservative fallback for a machine with no global agreement: do
  not merge or push to `main`, post a recommendation, and stop for the human. The fallback must say
  that it yields to a global agreement. Make PLAYBOOK line 155 agree.
- Constraints: the template serves other operators and machines, so it must work where `~/.claude`
  does not exist. Do not paste the owner's protocol into `ai-harness`, because a second full copy is
  the defect being removed. The versioned source of the block is the template file itself. The
  template dropped a bare "never merge" rule on 2026-06-04 because agents worked around it, so the
  yield clause is required. Leave the project-level sections alone.
- Risk: **this item changes what future agents read.** Every project later made from the template
  gets the new block. A pointer that does not resolve would leave a new project with no merge rule,
  which is worse than a weak one. The fallback and the test cover that case. No existing repo
  changes, because none carries the template's merge section.
- Done when: the template has 0 occurrences of "Merge recommendation format"; the test asserts that
  all three parts are present; the PLAYBOOK and the template agree; the merge recommendation states
  the behavior change.

**Item 3. Add the drift check, its allowlist, and the ownership map.** Mode: runner-safe. It follows
item 2, so that repo mode passes on the day it lands.
- Target: `ai-harness`. New paths: `tools/spine_check.py`, `tools/spine_allowlist.toml`,
  `tests/test_spine_check.py`, `docs/spine.md`.
- Why: Gap 4. Only one test reads any protocol text today.
- Do: build one read-only script with two modes, because a test run must not depend on host state.
  **Repo mode** (the default) reads only the `ai-harness` checkout and may hard-fail. It runs in the
  repo's pytest suite, and in CI if a test workflow is added later (today the only workflow is
  `venice-review.yml`). **Host mode** (`--host`) reads `~/.claude`, `~/.codex`, and the other repos
  under `~/projects`. It never hard-fails. Each result is `ok`, `warn`, `held`, or `skipped`, and
  the exit code is 0 unless the script itself crashes.

| Check | Mode | What it verifies | Result on a miss |
|---|---|---|---|
| R1 | Repo | No marker phrase of an owned protocol ("Merge recommendation format", "exactly the work in the block", "Simplified Technical English") in `setup/templates/CLAUDE.md`, `AGENTS.md`, `setup/PLAYBOOK.md`, or `GUIDANCE` | Fail |
| R2 | Repo | The template holds the item 2 pointer block, and `AGENTS.md` points to the owner | Fail |
| R3 | Repo | Every repo-relative path named in `docs/spine.md` exists | Fail |
| H1 | Host | Owner files are under version control. **Level 1 threshold:** any output from `git -C ~/.claude status --porcelain -- CLAUDE.md commands skills`. **Level 2 threshold:** level 1 is true for a tracked file, and that file's last commit date (`git log -1 --format=%cI -- <file>`) is more than 7 days before the run | Level 1: `warn`, "uncommitted changes exist". Level 2: `held`, "stale" |
| H2 | Host | The R1 markers appear in no `CLAUDE.md` or `AGENTS.md` under `~/projects`, nor in `~/projects/AGENTS.md` or `~/.codex/AGENTS.md` | `warn` |
| H3 | Host | Every instruction file can be read. Where a repo has both names, they are identical, or one links to the other, or one holds a pointer line to the other | `warn`, or `held` for a pair that cannot be read |
| H4 | Host | `~/.claude/delegate/venice.md` and `~/.codex/AGENTS.md` match their sources | `warn` |
| H5 | Host | Every path named in the owner file exists | `warn` |

- H1 uses git only, never file mtime. Git cannot date an uncommitted edit, so level 2 measures the
  time since the last clean point. That is an upper bound on the age of the oldest uncommitted
  change. Untracked files have no commit date and stay at level 1. If commit dates prove unreliable,
  item 1 can commit an audit-stamp file (date plus file hash) for level 2 to read.
- Allowlist: `tools/spine_allowlist.toml` names repos that are intentionally `absent`, `unreadable`,
  `no-instructions`, or `held-broken`, each with a reason and a date. An allowlisted repo gives
  `skipped`, or one standing `held` line for `held-broken`, and never a new alert. A missing
  `~/.claude`, `~/.codex`, or repo folder gives `skipped`, not an error. First entries: `backlog`,
  `roam`, `splash_poller` and `vps-tools` as `no-instructions`, and `rmpeacockwriter.com` as
  `held-broken` unless item 4 has merged. `_archive/`, worktrees and `node_modules` are skipped.
- Constraints: read-only, no network. Tests use temporary fixture trees, never the live home
  directory. Host mode is not part of the pytest run.
- Done when: tests pass, with fixtures for a symlink loop, an absent repo, an allowlisted repo, and
  both H1 levels; repo mode passes on the branch; one host run on the VPS reports only what is still
  open from findings 1 and 3, and `skipped` for the four `no-instructions` repos.

**Item 4. Repair the three broken pairs.** Mode: runner-safe, one branch per repo.
- Target: `AGENTS.md` in `romance-elliecalloway.com` and `romance-tessacross.com`; `AGENTS.md` and
  `CLAUDE.md` in `rmpeacockwriter.com`.
- Why: finding 3. One repo gives an agent no readable rules. Two hide them from an agent that reads
  only `AGENTS.md`.
- Do: for the two romance sites, add a first line to `AGENTS.md` that points to `CLAUDE.md`, and
  keep the Astro text. For `rmpeacockwriter.com`, restore `AGENTS.md` as a regular file from commit
  `ebb89e0`, and keep `CLAUDE.md` as the symlink to it.
- Status of `rmpeacockwriter.com`: from 2026-09-18 until the repair merges, the repo is **held:
  broken instructions**. This backlog entry carries that status until the item 3 allowlist exists,
  and the allowlist then records it as `held-broken`. No unattended work is scheduled in the repo
  (the backlog holds 0 items for it today), and an attended agent is told first that the pair cannot
  be read. When the repair merges, remove the allowlist entry. The project rules that commit
  `2792316` meant to add were never written, so this item ends `held` and names that owner step.
- Constraints: change no other file. No deploy command. These sites may redeploy on a merge to
  `main`, so each merge follows the normal merge protocol.
- Done when: both names can be read in all three repos and each pair is linked. If item 3 has
  landed, H3 gives `ok` for all three and the allowlist no longer names `rmpeacockwriter.com`.

**Item 5. Tidy the owner file and put host mode on a clock.** Mode: attended. The owner approves the
exact wording before it is committed.
- Target: `~/.claude/CLAUDE.md`. In `ai-harness`: `setup/claude-codex-mirror/settings.py`,
  `watchdog/`, `docs/contracts/watchdog.md`.
- Why: findings 4 to 7. Also, a check that nothing runs guards nothing.
- Do: (a) Retire or archive the two sections dated 2026-09-07 if the batch is spent. (b) Name one
  review command in "Review routing" and say the other is the same panel. (c) Add a three-line
  "Sanctioned exceptions" list that points to `backlog-run` and to the Ultimate Portugal engine
  rules. (d) Make the Codex `GUIDANCE` connector paragraph defer to the owner file. (e) Have the
  existing watchdog run host mode once a day and send only `held` results and new `warn` results
  through its normal channel. No new cron entry.
- Constraints: it follows items 1 and 3. Use the item 1 snapshot and rollback steps for every edit
  to the owner file. No wording change without the owner's approval.
- Done when: host mode on the VPS reports no `warn` or `held` apart from allowlisted lines; the
  owner file names one review command; `docs/contracts/watchdog.md` lists the new check.

## 8. What "done" measures

The step is complete when all of these hold, and the check keeps them true:

| Measure | Today | Target |
|---|---|---|
| Uncommitted changes in the owner file | 107 of 228 lines; last commit 45 days old | H1 level 2 (`stale`, 7 days) never fires |
| Instruction files that copy an owned protocol | 1 (the template) | 0 |
| Repos with an unreadable or unlinked instruction pair | 3 of 8 | 0 |
| Installed policy copies that differ from source | 0 of 2 | 0, checked daily |
| Commands named for the required review step | 2 | 1 |
| Owner-file paths that do not exist | 0 | 0, checked daily |
| Verifiers that read any protocol file | 1 (the runner prompt test) | 2 (adds repo mode in the test suite), plus host mode daily |
| Alerts raised by repos with no instruction files | Not measured | 0 (allowlist) |

## 9. Sequencing

The chain places this step after contracts and after context retirement. Contracts merged on
2026-09-16. Context retirement (the wiki retirement item) is still held.

That ordering does not bind the five items. The chain put the spine last so that nobody would
consolidate text that was about to be retired. Context retirement targets memory files, wiki
articles and unused skills. No owner file in section 6 is in that set, and a skill retired later
simply leaves the check's file list. Items 1, 2 and 4 can run now. Item 3 follows item 2. Item 5
follows items 1 and 3, because the check is what proves the tidy-up broke nothing. If the
personal-layer bake-off later adopts a separate home for personal facts, the per-project Notion
token notes (owner file lines 78 to 94) are candidates to move there. That decision does not block
this work.

## 10. Limits of this inventory

- The Venice installer's plan mode (`setup/delegate-venice/install.py` with no flags, expected
  output "No changes") was not run. The installed policy file was compared byte for byte with its
  source instead.
- Whether the two 2026-09-07 batch keys have expired was not checked. It needs the Venice account.
  Item 5 (a) depends on the owner confirming it.
- Vendor plugin skills under `~/.claude/plugins/` were not inventoried, apart from confirming the
  marked Venice pointer block in both active Delegate skills.
- The Mac mini and the GitHub workflow files of other repos were not inspected.