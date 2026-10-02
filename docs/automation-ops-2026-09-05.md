# Automation and personal operations: outcome-led assessment

Date: 2026-09-05. Scope: the 25 active shared-backlog items assigned to
`build-ai-automation-workflow` or `none`, plus the unattended jobs that operate
this workspace. This is an assessment and execution order, not a new authority
policy. The canonical working agreement remains `~/.claude/CLAUDE.md`.

## The intended end state

The owner stays solo and can work from any device. Agents do the preparation,
execution, and independent checking. The owner supplies direction, taste, and
the consequential approvals required by the working agreement. Useful work
survives a lost device, interrupted session, or failed job. Durable knowledge
improves later decisions without requiring the owner to maintain the machinery.

This interpretation comes from [OVERVIEW.md](OVERVIEW.md), the one-canonical-home
rule in [ARCHITECTURE.md](ARCHITECTURE.md), Bebop's personal-assistant purpose,
and the accepted measurement/retirement sequence in the
[July assessment](harness-engineering-assessment-2026-07-20.md#11-two-corrections-that-hold-regardless-of-the-bake-off-outcome).
Older documents describe historical deployments; their existence does not prove
their deployment claims remain true.

The owner's instruction for this work is to challenge improvements against that
end state. A backlog entry is a proposed solution, not proof that its mechanism
is still needed. Ask the owner when a missing preference or decision changes the
intended outcome; do not infer permission for external actions from this review.

### The improvement test

Before adding or changing a mechanism, answer:

1. Which owner outcome improves: a commitment handled, a decision prepared,
   a defect prevented, a fact correctly reused, or work recovered?
2. What current evidence shows a problem, and what observable result proves the
   change fixes it?
3. Can an existing component own the job? What duplicate path or manual step can
   disappear?
4. What does the owner see when the job fails, and what is the next action?
5. What authority does the job have, how does it stop, and how is recovery tested?
6. What evidence would make us stop or retire it?

Run counts, tokens consumed, stored articles, and alerts sent are activity
measures. They are not substitutes for accepted results or reduced owner effort.
Do not build a metrics platform to enforce this principle: begin with existing
logs, reports, and a small matched sample of real tasks.

## First findings verified directly

### 1. The decision surface needs more work than another dashboard

`backlogrun/cli.py:council_review` cuts the recommendation at 400 characters.
`notification` output cuts it again. Required conditions can disappear from the
short field, as seen in the reels, math-content, and Freestyle-storefront records.
Full review files for all three exist under
`~/projects/.backlog-run/reviews/<item-id>.md`; the evidence is recoverable, not lost.

`write_report` offers `approve` for every `in_review` item. That status correctly
means awaiting human review, but does not distinguish a clean review from changes
requested, failed review, or a session with no final outcome marker.
`work_one` can describe a branch with changes and no marker as "treating as done".
The owner must reconstruct readiness from prose before deciding.

Recommendation: keep lifecycle and review readiness separate. Preserve the full
review and make unresolved conditions visible at the decision point. Do not
silently reinterpret `in_review` as ready or auto-merge work. This improves the
human judgment step without adding a service or changing approval authority.

### 2. Reuse the operating repository before creating another one

The four backlog-named scripts (`tm`, `agents`, `wiki-promote`, and
`telegram-orphan-reaper.sh`) exist as regular files under `~/.local/bin`.
No same-named source file exists in this repository's file inventory. This
supports the recovery concern; it does not prove the whole VPS has no backup.

Challenge the proposed new `vps-tools` repo. Prefer a versioned home in this
existing automation repository, with an explicit install/check command. Capture
the current bytes first; do not combine source recovery with a behavioral rewrite
or replace live cron entrypoints during the capture. One component should own
installation and rollback, with local config and secrets kept out of git.

### 3. The historical architecture is not an operating inventory

The architecture document still says the VPS is not provisioned. Bebop's README
describes agent-side Telegram delivery, while `bebop/run-briefing.sh` sends the
composed output through `bin/tg-send`. These are evidence of document drift.
Use the [current job contracts](contracts/README.md) for the observed schedule and
entrypoints. A written contract describes and tests a boundary; it does not
install or enforce that boundary by itself.

The inventory is deliberately limited to cron and user timers. The continuously
running [session bridge](../session-bridge/README.md), interactive hooks, and
remote CI are separate parts of the operating surface; do not mistake a complete
scheduled-job map for a complete service inventory.

### 4. Keep product work out of a generic operations queue

`repo: none` mixes local tools, account decisions, and product work. The runner's
"no target repo directory" note can mean "not a git repo" rather than a missing
directory. A held task can still contain useful investigation or preparation.

SplashMe access belongs with the paused MeetTrack decision. Newsletter routing
belongs with SwimTrack's signup flow. A keepalive is only justified by an active
service requirement; preserving recoverable data is a different outcome.
Do not resume paused products merely to clear an operations backlog.

## Disposition of all 25 existing items

These are recommendations. The shared backlog and its statuses are unchanged.
Dates and slugs identify the existing cold-runnable prompt in
`~/projects/backlog/backlog.yaml`.

| Existing item ID | Recommended disposition | Outcome / challenge |
|---|---|---|
| `2026-07-23-cron-loop-contracts` | First batch: document actual jobs and gaps | Know what runs, what it may change, and what proves it helped. |
| `2026-09-05-codex-helper-sandbox-broken-vps` | First batch: diagnose before changing settings | Restore dependable independent review without weakening the sandbox. |
| `2026-08-25-wiki-life-first-effect-audit` | First batch: measure | Test the personal-context policy; article mix alone does not prove usefulness. |
| `2026-08-25-diem-backfill-estimate-poisoning-check` | First batch: investigate | Prefer useful completed work over spending a daily allowance. |
| `2026-08-26-version-vps-loose-scripts` | Next recovery batch; revise proposed repo choice | Preserve working tools in the existing automation repo before refactoring. |
| `2026-09-03-feedback-alert-reply-channel` | Owner decision; reuse session bridge first | A phone alert should lead directly to a prepared decision or action. |
| `2026-07-21-hermes-provider-revokes` | Owner/account verification remains necessary | Remove unused authority; verify consumers before revocation. |
| `2026-07-21-telegram-token-consolidate` | Investigate current consumers, then recommend | One secret owner and a tested rotation path; no live secret move in this batch. |
| `2026-07-23-council-golden-set-harness` | Restore before the next council release | Known false-positive cases remain reproducible; offline validation before paid comparison. |
| `2026-07-23-venice-review-fleet-pass` | Follow the regression harness with a fleet inventory | Check current consumers before applying a historical 17-repo migration. |
| `2026-08-22-loom-content-aware-dedup` | Evaluate after the memory measurements | Stop repeated facts before costly rewriting; compare a cheap deterministic filter with a model call. |
| `2026-07-23-loom-phantom-wiki-tree-reconcile` | Small content-preservation batch after diagnosis | Both `~/wiki/wiki` and `~/wiki-loom-shadow/wiki` still exist with seven Markdown files each, not the historical five. |
| `2026-08-25-loom-triage-two-malformed-bm2-artifacts` | Combine investigation with the wiki repair batch | Preserve uncovered facts; delete only after evidence of coverage. |
| `2026-07-23-wiki-retirement-pass` | Refresh evidence before retirement | Never-read counts from July do not prove a September article has no value. |
| `2026-07-23-personal-layer-bakeoff` | Keep held; define the owner task sample first | A replacement must improve real decisions and reduce effort, not merely provide a cleaner tree. |
| `2026-07-23-define-shared-spine-step` | Recommend a small ownership map, not another framework | Global authority stays in its canonical file; runtime contracts stay with their code; personal facts stay outside product code. |
| `2026-07-29-claude-visibility-aris-mailbox` | Verify current account coverage before choosing access | Unknown inbox coverage must appear as unknown, never as proof of no mail. |
| `2026-07-20-shot-transfer-logic-dedupe` | Combine with script recovery when Mac-side verification is available | One tested transfer implementation; keep current screenshot/clipboard behavior. |
| `2026-07-20-shots-dir-retention-prune` | Low-priority bounded housekeeping | Current matching files total 1,603,305 bytes across eight screenshots; no current storage emergency is established. |
| `2026-07-21-venice-keys-polish-followups` | Finish as one small cross-repo review batch | Existing partial branch has work; avoid recreating it or widening behavior. |
| `2026-07-19-delegate-vscode-slash-command` | Keep its explicit human-check gate | Check the actual client symptom before investigating an old plugin version. |
| `2026-08-26-termius-api-bridge-decision` | Defer procurement research until navigation pain is confirmed | An extra service and plan must remove meaningful friction beyond the existing `tm` picker. |
| `2026-07-27-splashme-json-api-access` | Route to the paused MeetTrack decision | No access project until there is an active product need. |
| `2026-08-05-n8n-branch-on-type` | Route to SwimTrack signup delivery | Verify consent/routing behavior there; external edits remain a separate authorized action. |
| `2026-08-16-swimtrack-coach-keepalive` | Confirm need before keeping an unused service alive | Availability needs a user and a service target; data protection is not the same as artificial traffic. |

## Concrete follow-on proposal: truthful review readiness

Target repo: `~/projects/build-ai-automation-workflow`.
Paths: `backlogrun/cli.py`, `tests/test_backlogrun.py`, `backlogrun/README.md`.
Background: the runner truncates recommendations containing required fixes and
labels incomplete sessions "treating as done"; the full reviews are already saved.

Task: introduce explicit review-readiness metadata derived from structured review
results when available, with unknown as the fallback for historical/free-text
records. Show clean / changes requested / review failed / review unknown in the
morning report, alongside the full review path and unresolved conditions. Retain
the existing lifecycle states and the human's final authority. A missing outcome
marker must say incomplete/unknown, not claim task completion merely from a diff.
Do not infer a clean review from a substring such as "approve" in prose.

Proposed JSON record types, to implement with this task. All fields are required;
unknown values use the explicit enum or `None`, never an omitted success field.

```python
from typing import Literal, TypedDict

class Validation(TypedDict):
    name: str
    branch_sha: str
    status: Literal["passed", "failed", "unknown"]
    evidence_path: str

class ReviewInput(TypedDict):
    schema_version: Literal[1]
    record_id: str
    finished_at: str  # UTC RFC3339, assigned by the runner
    branch_sha: str  # full commit hash
    runner_outcome: Literal["completed", "incomplete", "failed", "unknown"]
    review_status: Literal["clean", "changes_requested", "failed", "unknown"]
    blocking_findings: list[str]  # complete text, never truncated
    required_validations: list[str] | None  # declared before execution
    validations: list[Validation]
    source_record: str  # path to the complete review, relative to cfg.state_dir

class ReviewReadiness(TypedDict):
    schema_version: Literal[1]
    record_id: str | None
    branch_sha: str
    status: Literal["ready", "changes_requested", "failed", "unknown"]
    reasons: list[str]
    evidence_path: str | None
```

Persist immutable inputs as `cfg.reviews_dir/<run-record-stem>.inputs.json` and
the derived output as `<run-record-stem>.readiness.json` beside them. The stem
comes from the existing `cfg.runs_dir` record. Preserve the complete Markdown
review under that same stem; retain the existing item-level review path for
compatibility. Emit the derived fields in the existing report JSON and Markdown,
with full evidence links. Show a local fixture report in the chat for owner
review. No notification send is needed for acceptance. Do not rewrite historical
records; absent versioned evidence displays `unknown`.

Validate schema and commit identity before interpreting a verdict. Malformed or
missing fields, unsupported versions, unreadable evidence, or stale SHA yield
`unknown`. A complete record has every typed field and readable evidence;
completeness does not imply success. Select the greatest `finished_at` for the
current SHA; a newer malformed attempt prevents fallback to an older clean one.
Identical duplicate IDs are deduplicated. Different payloads with the same ID,
or different outcomes tied at the newest timestamp, yield `unknown`. Preserve
older records as history; a later explicit complete review supersedes them.

For the selected valid record, apply these rules in order:

1. A clean verdict with blocking findings, conflicting validation results for one
   name, or a validation for a different SHA means `unknown`.
2. Runner/reviewer/synthesis failure or a failed required validation means `failed`.
3. Unresolved blocking findings or `changes_requested` means `changes_requested`.
4. Incomplete/unknown outcome, unknown review, undeclared required validations,
   or missing/unknown required validation means `unknown`.
5. `ready` requires completed outcome, clean review with no blocking findings,
   and one passed result for every declared required validation, all for the
   same SHA. An empty required list is valid only when explicitly declared before
   execution. Anything else means `unknown`.

These are proposed display rules, not new merge authority or a command to alter
old items.

Constraints: no live backlog changes, sends, pushes, or merges during tests;
temporary git repos and fake reviewers only. Preserve archive and recovery
invariants. Make any change to approval behavior explicit for the owner; this
proposal does not authorize bypassing or adding approval rules silently.

Done: fixtures demonstrate a condition beyond character 400 remains discoverable,
failed/synthesis-error/missing-marker cases never display as ready, old records
show unknown, a clean structured review displays correctly, and existing runner
tests pass. Show the owner the resulting report before merge approval.

## Concrete follow-on proposal: complete the existing alert handoff

The [September 6 alert-handoff assessment](alert-handoff-2026-09-06.md) now records
the event map, reproduced routing gaps, example alert and bounded build proposals.

Target repo: `~/projects/build-ai-automation-workflow`. Sources:
`session-bridge/README.md`, `session-bridge/src/`, `bin/tg-send`, `sessiongc/cli.py`,
`council/scripts/security-sweep.sh`, `watchdog/monitors.toml`, and the installed
`~/.local/bin/agents`. Use `docs/contracts/README.md` for observed triggers.

Task: map each owner-attention event to its current recipient, durable result,
retry behavior, and the next action available on the phone. Identify overlap
between the session bridge and `agents` before removing either. Check whether
the existing watchdog can cover missed expected results; do not add a new
monitoring daemon by default. Distinguish an HTTP send acceptance from receipt
or action by the owner. Store accepted unresolved work in the existing shared
`~/projects/backlog/backlog.yaml`, with evidence under the owning tool's existing
state directory, instead of merely repeating a notification. Proposed additions
still follow the backlog's propose-then-confirm rule.

Constraints: prepare a concrete example message and routing change for review;
do not send messages, start listeners, alter credentials, or disable jobs. The
existing feedback-alert-reply-channel item remains the owner decision for any
new two-way interaction. Reuse its chosen channel once the decision is supplied.

Done: one compact event-to-action table, evidence of each current delivery gap,
an overlap disposition for `agents`, and a bounded implementation proposal with
delivery-failure, duplicate-event, stale-event, and wrong-session test cases.
The proposal must state which owner step it removes and what existing component
owns the result. This is ready for a new session without further discovery of
where the relevant code lives.

## Evidence and limits

The initial assessment uses local source, git state, current files, and read-only
job inventories. It does not verify external account state, revoke credentials,
send messages, install software, or run production jobs. Findings about live
behavior require the specific evidence named in each contract or follow-on task.
The [memory and scheduler audit](automation-memory-audit-2026-09-05.md) records
the calculations, snapshot times, uncertainty, and a reproducible read-only method.
The [Codex helper diagnosis](codex-helper-diagnosis-2026-09-05.md) separates the
reproduced bubblewrap failure from the working tool-free path and unresolved
shell-tool smoke. It does not claim a repaired helper.
