# Portfolio execution implementation plan

**Goal:** Execute the accepted audit work order through concrete, tested changes and owner decision cases.
**Spec:** [Accepted work packages A–E](../../operating-harness-gap-analysis-2026-09-12.md#proposed-work-order).
**Architecture:** Retain existing queues and owners. Repair their boundaries, feed the current direction into task preparation, and place an authenticated website over their records. Keep source evidence, owner choices, and observed outcomes distinct.
**Stack:** Existing Python runners, Bun session bridge, shell tools; a small Python web service and browser assets for the cockpit.

## Constraints

- User approved execution on 12 September. Do not repeat discovery/design approval.
- Work in isolated branches. Preserve main, running jobs, customer state, existing authority, and unrelated dirty files.
- No unrequested messages, product activation, new spending commitment, publication, or deployment.
- Required Council diff review is already authorized. Main merges/pushes require the working agreement's concrete final recommendation.
- Shared worktree owners edit disjoint files; parent alone integrates and commits.
- Real behavior tests use temporary repositories, fake network/process boundaries, and scratch state. No live sends or jobs during tests.
- Keep the 60-minute target aspirational, missing evidence unknown, EUR75/hour nominal, and experiment next steps reserved for Rex.

## A. Consequential repair

Owner 1: `council/gate.py`, `.github/workflows/venice-review.yml`, `backlogrun/cli.py`, relevant existing tests; Romance worktree `src/ops/actions.py` and tests.

- [x] Reproduce dependency mismatch and privileged-tooling gate miss with existing fixtures; repair the pin and consequence classification without raising all tooling gates.
- [x] Make human backlog approval bind to the reviewed/current work version; preserve explicit owner authority and distinguish advisory readiness.
- [x] Add a bounded existing-branch rework command using the runner's isolation, resource, no-outward-action, and review contracts; wire Romance Changes to it.
- [x] Verify stale version rejection, approved version behavior, rework preserving existing commits, resource bounds, no extra merge authority, and revised evidence.

Owner 2: `bin/tg-send`, `watchdog/run.py`, `watchdog/run-watchdog.sh`, `session-bridge/src/`, associated tests; VPS tools worktree `bin/agents` and tests.

- [x] Reproduce failed-send suppression and changed-prompt approval with fakes.
- [x] Validate provider acceptance, save retryable failure, update suppression only on accepted delivery, and retain exact action/prompt identity.
- [x] Verify failure/retry, stale reply, changed prompt, duplicate/uncertain execution, and no live outward actions. Document narrow installation steps separately.

## B. Commercial choices and E. Thrift

Owner 3: write technical decision analyses under `docs/decisions/2026-09-12-*.md`; read canonical snapshots and current UP/SwimTrack source, scoped live state only where useful.

- [x] Prepare one UP affiliate activation case and one SwimTrack season-pass readiness/release case. Specify test, costs/unknowns, promises, time horizon, stop condition, recommendation, and exact remaining owner choice. Do not activate either.
- [x] Trace a small recent SwimTrack editorial sample to actual consumers, then recommend retain/connect/reduce/pause from the evidence. Do not build a connector to justify unused work.
- [x] Prepare the first joint monthly spend/outcome and Ideal State review using measured evidence, explicit missing allocations, proposed decisions, and a reusable short-check format. No new scheduler or score engine.

## C. Direction and feedback handoff

Parent: `workqueue/` reused from its existing committed implementation; `backlogrun/context.py` (new), related tests, and scoped Attain feedback adapter if needed.

- [x] Supply current accepted criterion, source/version, evidence, and counterevidence during trusted task preparation; keep scoped credentials out of workers.
- [x] Put feedback state reconciliation in a trusted controller, based on verified runner results; retain existing no-live-write worker boundary.
- [x] Verify stale/missing context is explicit, one fresh worker uses the current controller-supplied direction and correction, and feedback updates cannot claim unverified completion. Wider automatic retrieval across both clients is a separately scoped follow-up.

## D. First useful cockpit

Parent: `cockpit/` with server, source adapters, static interface, bounded action adapter, tests, and runbook. Existing stores remain authoritative.

- [x] Add authenticated access, deny anonymous data access, keep credentials out of browser data, and restrict deployment defaults to loopback.
- [x] Show initiative direction/evidence, cash decisions, all pending work, expected-result freshness, resource observations, and coverage gaps with timestamps.
- [x] Implement one explicit, version-bound owner action through an existing queue, tested with temporary state; keep unrelated work running. No generic shell endpoint or implicit merge/release authority.
- [x] Browser-check desktop/mobile, keyboard access, empty/failure states, and all necessary decisions beyond one hour.

## Integration and completion

- [x] Parent reviews changed interfaces and runs focused tests, then relevant existing suites once.
- [x] Required Council diff review; fix actionable findings and verify the revised change.
- [x] Record actual deliverables, measured validation, installation/activation dependencies, and concrete owner decisions. Commit scoped branches.
- [x] Prepare exact merge recommendations after all authorized preparatory work is concrete and reviewable; execution waits for the owner’s final merge instruction.

## Completion evidence

The [execution record](../../portfolio-execution-2026-09-12.md#final-validation) records actual results, limits and activation dependencies. Checked boxes mean the preparation was completed, not that products or services were released. The initial two-client learning probe was narrowed to the accepted work package’s one fresh-worker test; automatic retrieval by both clients remains explicitly unproven.
