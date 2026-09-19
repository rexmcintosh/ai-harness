# Backlog runner

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed command:** `backlog-run work`

## Purpose and authority

**Default mode:** bounded change-producing preparation. At 03:00 UTC, work at most two eligible open backlog items in isolated `claude/bl-*` worktrees, council-review the result, and leave it `in_review` or `held`. Local cron documentation and host configuration confirm UTC scheduling. Canonical item state is `/home/dev/projects/backlog/backlog.yaml`; completed and dropped records are stored in `archive.yaml`.

It may create local worktrees and branches, run the scoped agent, write runner records, and transition only still-open items to `in_review` or `held`. It must not push, merge, deploy, send externally except its configured summary, or approve its own work. Human approval under the canonical merge protocol remains the merge authority; `backlog-run approve` is an explicit operator command, not a clock action.

**Secrets, names only:** `VENICE_COUNCIL_KEY` or `VENICE_API_KEY`, `TELEGRAM_BOT_TOKEN`; the runner passes a whitelist environment to the worker.

## Success and evidence

Success is a per-item run record, a council review file, and an item transition consistent with the recorded outcome. Inspect `/home/dev/projects/.backlog-run/cron.log`, `runs/<timestamp>-<id>.json`, `reviews/<id>.md`, `report.json`, `report.md`, and the backlog item. The runner’s own test suite covers its state transitions with fake workers.

## Failure, escalation, and gaps

Lock contention exits 75 and is visible in the cron log. Failed or ambiguous work is held or recorded rather than merged. The owner must use the morning report to judge work. The contract is reinforced by the backlog README safety contract but is not a replacement for it.

## Pre-session hold gate (added 2026-09-19)

Before a session is spent on an item, `backlogrun/gate.py` asks Jev one yes/no question through the shared client (`docs/contracts/jev.md`): does finishing this item need an outward-facing or irreversible action? A score of 0.7 or more turns `work` into `held` with a note that says why and how to release it. The freed slot goes to the next item. The gate may only add a hold. No answer (no key, an outage, `JEV_DISABLED`) means no hold, so the run is what it was before the gate existed.

- The owner's decision wins: `backlog-run reopen <id> --gate-ok` records `gate_ok: true` on the item and the gate never asks about it again.
- Off switches: `backlog-run work --no-gate`, or `BACKLOG_GATE=off`.
- What is sent: the repo name, the title, and the prompt with addresses and token-shaped strings removed. Never the runner note, the council verdict, or the outcome.
- At most 10 items are screened per run; after that the rest are deferred to the next night.
- Evidence: `docs/jev-replays-2026-09-19.md` and `docs/evidence/jev-replays-2026-09-19/backlog/`. The wording and the 0.7 line were measured together; `tests/test_backlogrun_gate.py` fails if the wording changes without a new hash.
- Known gap: Jev cannot read dates. "Do not run before <date>" in a prompt is invisible to this gate.
- This gate reinforces README safety rule 2. It does not replace the session's own duty to stop and report HELD.

