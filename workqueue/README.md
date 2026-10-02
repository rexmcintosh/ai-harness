# Attain Prep Product work queue

The Notion Product work queue is the owner-facing task list. The feedback log remains evidence. Choosing **Ready** on a task authorizes one run of its selected mode. The controller polls every two minutes after the prior run finishes; one run executes at a time.

## Use

1. Create a task or use one of the three drafted investigations. Link supporting Feedback rows.
2. Set Repository to `sat-prep`, choose Mode, and fill in Brief and Done when. Build also requires Checks, one verification command per line.
3. Set Status to Ready. Working records the run ID and start time before the runner starts.
4. Read Result and the complete run sections when the task reaches In review or Needs your input. Readiness reports review evidence separately from status. It uses the same labels as `backlog-run report`: Ready for your review; Ready with follow-ups (minor points listed under Follow-ups; they do not block merge); Changes requested; Owner decides (3 rounds) (three review rounds still found a serious point, so you decide); Review or checks failed; Review readiness unknown.
5. Clarify the brief and select Ready for another attempt, or use Resume to continue the retained session yourself. Close the task after accepting the outcome.

Investigate produces findings and recommendations. Build produces scoped changes with checks. Prepare session produces a handoff and retained session for an interactive continuation; it does not open a browser chat automatically. A task can relate to several feedback entries. Only Brief, Done when, Mode and Checks direct the work; source feedback is evidence.

## Boundaries

The controller alone holds the scoped Notion credential, `NOTION_TOKEN_ATTAINPREP` from `~/.env`. Agents use the existing backlog runner's scrubbed environment, isolated worktree and no-push/no-send rules. They do not push, merge, deploy or mutate customer records. Only `sat-prep` is allowed. Original feedback edits do not start runs. The existing shared backlog and its nightly runner remain separate consumers of the same execution code; this queue does not insert tasks into that nightly list.

Build briefs must define checks before launch. In review does not mean ready to ship; review failures and unknown evidence are kept visible. Each session has a 30-minute execution limit, the existing $20 session ceiling, and a separate review step. One Ready task may wait while another runs.

## Recovery

`~/.local/state/attain-work-queue/journal.json` stores each starting brief and full result. Its writes use fsync and atomic replace. An exclusive process lock covers the complete tick. The controller records a launch marker before running an agent. After an interrupted/uncertain launch it reports Needs your input and does not automatically run the job again.

Result publication is an outbox: save the result first, then append stable, run-labelled chunks to Notion. Lost acknowledgements are reconciled with paginated read-back. Publication failure retries the saved output without restarting the agent. Human status changes are preserved; a changed brief/mode is reported and never injected into a running session. Setting a running task to Draft does not stop the process immediately. Stopping the service kills its process group; subsequent recovery requires an explicit new Ready attempt.

A run ID is generated for every new attempt. Prior run sections and branches are retained. Treat the journal and runner state as durable data; do not delete them to clear a stuck task. Reprovisioning does not reset task statuses or overwrite edited seed briefs.

## Operation

Configuration: `~/.config/attain-work-queue/config.json` (ids/paths only, mode 0600). Versioned runtime: `~/.local/share/attain-work-queue/current`. State and logs: `~/.local/state/attain-work-queue/` and the user service journal. The Notion help page has the last queue health check; no Notion outage can update that page, so the local journal is authoritative during an outage.

```sh
notion-work-queue setup   # authorized idempotent Notion provisioning
notion-work-queue status
systemctl --user status attain-work-queue.timer
journalctl --user -u attain-work-queue.service
systemctl --user start attain-work-queue.service
# Stop accepting new work, keeping all results and branches:
systemctl --user disable --now attain-work-queue.timer
# Stop an active run too (subsequent recovery reports uncertainty):
systemctl --user stop attain-work-queue.service
```

Install a reviewed checkout into a dedicated virtual environment, then point `current` at it and install the two units in `systemd/` to `~/.config/systemd/user/`. Do not replace the global council/backlog-run installation. Enable with `systemctl --user enable --now attain-work-queue.timer`. Keep the previous runtime for rollback; disable the timer and wait for/stop an active service before changing `current`.

Verify with `python3 -m pytest tests/test_workqueue.py tests/test_workqueue_runner.py tests/test_backlogrun.py tests/test_backlog_readiness.py -q`. Offline tests use fake Notion and fake sessions. A separately labelled live smoke validates Ready-to-result without modifying customer/application data.

Notion uses API version 2022-06-28, compatible with the established feedback database. Relations are single-property links to that database. See https://developers.notion.com/changelog/releasing-notion-version-2022-06-28 for that API contract.

### Current Ideal State at preparation

The trusted controller now reads Attain's canonical Ideal State before claiming a task, using the existing scoped token. It saves source URL, source edit time, observation time, content hash and text in the run journal before launch. The worker receives that packet without the credential and keeps accepted decisions separate from proposed criteria. A missing or changed source is named as an evidence gap; an already-authorized routine task can continue within its original brief. Crash recovery retains the original task packet and never starts a replacement worker to reconstruct it. This is one initiative handoff, not a new universal memory service.
