# Attain Prep Notion Product work queue

Owner outcome: moving a clear task to Ready produces a useful recommendation, code change or session handoff without manually copying context into an agent. The original feedback remains linked evidence.

Trigger: user-systemd timer `attain-work-queue.timer`, two minutes after prior completion; entrypoint `~/.local/share/attain-work-queue/current/bin/notion-work-queue tick`. Status Ready is the only task-level launch signal. Initial tasks are Draft. No automatic production release.

Owner surface: Notion Product work queue and its help/health page under the Attain Prep customer feedback hub. Status, run id, timestamps, summary, full result, readiness, branch and resume command expose progress and decisions. A failed check is not described as ready merely because work ended.

Implementation and canonical operating/recovery instructions: [workqueue/README.md](../../workqueue/README.md). Queue-specific state is separate from the shared nightly backlog. Both use the existing backlogrun code for branch isolation, scrubbed environment and review.

Failure evidence: local health.json, atomic journal.json, per-run session and Council artifacts, user-service journal. Notion publication retries never rerun the agent. Unknown launches require a new human Ready action. Polling/health timestamps prove only that the controller checked; useful results require the attached work and review evidence.

Credential custody: controller reads only NOTION_TOKEN_ATTAINPREP. Child agent receives no controller token. External writes are limited to the configured queue/results/health page. Agent work stays in sat-prep worktrees. No Telegram or email delivery is added.

Recovery/stop: disable the timer to stop new launches, stop the service to terminate an active process group, retain journal and branches. Switch to the prior reviewed runtime only after stopping/waiting for the service. Investigate repeated failures in the existing operating workflow; no new monitoring daemon is introduced.

Retirement test: if task completion still requires repeatedly copying context or hunting logs, improve the result handoff before adding more triggers. Remove the bridge when a single replacement reliably owns both launch and result recovery.
