# Notion Product Work Queue Implementation Plan

> **For agentic workers:** Use scoped parallel implementation lanes and integrate/review each boundary before activation.

**Goal:** Turn an explicit Notion Ready selection into one bounded agent session and publish its result on the same task.

**Architecture:** A separate Product work queue under the Attain Prep feedback hub links to existing feedback. A small Python service polls every two minutes, validates and snapshots Ready briefs, journals claims locally, runs the existing backlog runner adapter, and publishes results. Original feedback edits never launch work. One process lock serializes runs; ambiguous interrupted launches require a deliberate retry.

**Tech Stack:** Python standard library, existing requests/PyYAML and backlogrun, Notion API 2022-06-28, user systemd timer. Scoped NOTION_TOKEN_ATTAINPREP only.

**Spec:** Rex approved the conversation design on 2026-09-09: Draft -> Ready -> Working -> Needs your input / In review; investigate/build/prepare session modes; results in Notion; no automatic production releases.

## Global constraints

- Separate customer evidence from executable tasks. Link multiple feedback rows to one task using a relation.
- Only allowed repo sat-prep. Validate Task, Brief, Done when, Mode and required build checks before launch.
- Token remains in controller process, never in agent prompt or runner environment.
- Do not push, merge, deploy, message people, or mutate customer records from a queue run.
- Investigation produces evidence/recommendations; Build follows a clear approved brief; Prepare session produces a handoff and resumable session reference, not an unattended interactive terminal.
- Initial FB-010, FB-011 and FB-012 tasks stay Draft, mode Investigate. User selects Ready to authorize each.
- A recorded run with an uncertain launch is never automatically relaunched. Ready after a completed/held run means an explicit new attempt; preserve prior result history.
- Original Ready snapshot governs work. Later edits are not injected into an active session. Record them as a conflict/next-run brief.
- Notion failures must preserve local results and retry publication without rerunning the agent. A service error is visible in its durable local log and queue health page when Notion reconnects.

## Task 1: Existing runner adapter (helper)

**Files:** workqueue/runner.py, tests/test_workqueue_runner.py.
**Interface:** execute(task: dict, run_id: str, state_dir: str, projects: str) -> dict. Task keys: title, brief, done_when, mode ('Investigate'|'Build'|'Prepare session'), repo ('sat-prep'), checks (list[str]), source_url. Result keys: status ('In review'|'Needs your input'), result (full readable text), session (string), branch (string), readiness (string), cost_usd (number), resume_command (string), worktree (string).

- [ ] Write failing adapter tests with injected/fake runner calls and temp git repos.
- [ ] Implement through backlogrun Config/plan/work_one with a queue-local YAML store/state, git/tg disabled, isolated worktree, bounded runtime and budget, exact run id.
- [ ] Test mode-specific prompts, existing isolation/no-push/no-send behavior, full result preservation, unexpected code in Investigate, incomplete review reporting and resume instructions.

## Task 2: Notion controller (root)

**Files:** workqueue/__init__.py, notion.py, service.py, cli.py, tests/test_workqueue.py; package entrypoint.
**Interfaces:** Notion.call/query/get_task/update_task/append_result; Service.tick() with injected runner and Notion client. Atomic JSON run journal plus flock.

- [ ] Write fake-Notion tests proving Draft ignored, valid Ready launched once, validation failure never launches, result write failure retries without launch, crash uncertainty never retries, mode/brief edits detected, manual status edits preserved, completed retry gets new run id.
- [ ] Implement strict property parsing, paginated query, bounded HTTP retries and property-length handling.
- [ ] Before launch persist claim, confirm Working with Run ID, reread to detect conflicting edits, then record launching marker. Persist result before any Notion result update.
- [ ] Store full output as run-id-labelled result blocks; paginate read-back to prevent duplicates after lost acknowledgements. Status and result publication are retryable.

## Task 3: Queue setup and operation (root)

**Files:** workqueue/setup.py; workqueue/README.md; docs/contracts/notion-work-queue.md; systemd service/timer templates.

- [ ] Read hub/database before mutation; create or reuse exact Product work queue and health/runbook page. Properties: Task, Status, Mode, Brief, Done when, Checks, Feedback relation, Started, Finished, Run ID, Session, Branch, Result, Readiness, Resume.
- [ ] Seed three Draft investigation tasks using existing feedback briefs, with self-contained repo paths and acceptance criteria. Link back to feedback log; do not promote captured items automatically.
- [ ] Verify fixture integration plus one bounded live controller/runner smoke with no customer/application changes; archive the smoke task after evidence is recorded.
- [ ] Run regression tests, Council and fresh technical review. Resolve material findings.
- [ ] Install exact reviewed source into a private runtime, enable two-minute timer, verify timer/health/read-back and draft isolation. Record rollback: disable timer, retain journal/branches/results.
- [ ] Provide queue URL and exact Ready/Needs your input/retry instructions.

## Verification and review

The live smoke used a separately labelled temporary Notion task. Ready launched a real read-only agent, returned the requested source facts and session reference, and transitioned to In review. The task was archived after read-back; no application files or customer records changed. The three real investigations remain Draft.

Council completed its review. Accepted findings removed feedback reads from publication/claim confirmation, strengthened setup type/option validation, made home paths portable, and limited CLI exception logging. YAML safe loading was verified in the existing backend; verification commands are agent prompt content, not shell interpolation by the controller. Unconditional status overwrites were rejected because they would erase deliberate owner edits; ownership/conflict tests cover the actual transitions.

Opus timed out without a result. A fresh Sol review found a crash window between the adapter saving its completed response and the controller journalling it. Recovery now validates and adopts that saved response. The reviewer verified the fix and reported no remaining findings. Regression tests include both a valid finished marker and a marker from another run.

Operational handoff: the queue and help page URLs are recorded by `notion-work-queue setup`; runtime activation uses the reviewed package in a separate virtual environment and the user timer. Source feedback FB-010 through FB-012 link directly to their drafted work items. No shared backlog items are silently promoted or duplicated.
