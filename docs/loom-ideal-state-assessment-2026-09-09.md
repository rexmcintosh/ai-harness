# Loom, Ideal State, and the return to useful work

Date: 2026-09-09. Read-only operating assessment and proposed next work.
No runtime, queue, Notion page, or product behavior was changed. Recommendations
below are not approved implementation instructions or queued jobs.

## Owner outcome

Rex wants a self-sufficient, improving AI-supported working system. He remains
the sole human operator. Knowledge should survive sessions and improve later
work without requiring him to reconnect the pieces or repeat decisions.

The existing automation outcome statement already expresses this purpose:
[automation-ops-2026-09-05.md](automation-ops-2026-09-05.md). Extend that discipline
rather than creating another overlapping framework by default.

## Verified gaps

1. **Codex capture is absent from the installed Loom path.** The live cron calls
   `/home/dev/loom-runtime/loom/run-absorb.sh` at 02:00 UTC. Its `loom/cli.py:56`
   configures `~/.claude/projects`. `loom/discovery.py:50` searches `*/*.jsonl`
   beneath that directory. It does not discover `~/.codex/sessions`.
2. **Changing the discovery directory alone is insufficient.** The installed
   `loom/transcript.py:34` expects user/assistant records with Claude-style
   message content. Independent helper and parent checks produced zero extracted
   characters from sampled real Codex session files. This is a local deployment
   finding, not a claim that Codex cannot support memory systems.
3. **The extraction policy serves a different primary purpose.**
   `loom/prompts/distill.md` prioritizes personal facts and limits technical or
   operational learnings to two per session. Its output types are fact,
   decision, preference and procedure. Reusing that compressed output as the only
   source for project outcome evidence risks losing relevant material.
4. **Attain Prep's direction is readable but not automatically supplied to work.**
   Its canonical Notion Ideal State v0.2 explicitly documents this limitation.
   Inspection of the installed queue's `workqueue.runner._prompt` confirms no
   Ideal State or wiki context-loading step. Its output path publishes task
   results, not verified revisions to Ideal State.
5. **Project discovery currently depends on the main local checkout.**
   `/home/dev/projects/sat-prep/AGENTS.md` and `IDEAL_STATE.md` are untracked and
   absent from HEAD. The queue creates a fresh git worktree from a committed
   base, so those files are not inherited through that path. The pointer is
   useful here, but it is not a complete agent integration.
6. **Stored memory does not establish useful retrieval.** The September 5
   [memory audit](automation-memory-audit-2026-09-05.md) measures a shift in what
   Loom stores, while explicitly leaving improved later decisions unproven.
   No automatic wiki retrieval step was found in the inspected queue prompt or
   global Codex instructions. This is not an exhaustive audit of all clients.
7. **Later turns can be missed even in Claude sessions.** Installed discovery
   identifies a session by filename stem and excludes committed or quarantined
   sessions. It checks no content cursor, size or modification time. Later turns
   appended to an already committed transcript therefore do not enter that path.
   Atomic state writes protect the record; they do not solve incremental capture.

Loom itself is operating: the helper inspected the September 9 nightly run,
which completed absorption and promotion, with promotion visible on wiki master.
That establishes operation of its current path, not Codex coverage or usefulness.

## Responsibilities and one canonical home

| Component | Responsibility |
|---|---|
| Session and result records | What was said, decided, attempted and observed, with source identity |
| Loom | Capture, classify, reconcile and route durable knowledge |
| Project memory and evidence | Supporting facts, decisions, failed attempts and context |
| Ideal State | Current intended outcomes, constraints, checks and concise evidence assessment |
| Work queue | Selected, authorized next actions |
| Session preparation | Supply the current direction and relevant evidence to the next worker |

One Ideal State per project is compatible with many supporting documents. It
means one authoritative statement of direction, not one file containing every
source, observation, plan and result. Long-term memory should link to that
authority rather than independently redefining it.

Keep Attain Prep's Notion page canonical while Rex edits it there. A generated
Markdown snapshot can make that same content accessible to agents, with page ID,
version, fetched time and content hash. It must be clearly derived, not a second
editable authority. Unavailable or stale source access must remain visible.
Do not quietly introduce two-way Notion/Markdown synchronization.

## Proposed loop

Session/result -> durable source -> classified insight -> existing evidence or
proposed Ideal State revision -> selected queue work -> fresh session context ->
observed result -> evidence update.

Use Loom's existing capture and routing infrastructure, with a distinct project
evidence path from normalized source material. Do not pass project evidence
through the two-item personal-memory filter first. A second independent service
would add maintenance before this shared path is proven.

Every extracted item needs project, source/session identity, speaker, date,
claim type and supporting location. Separate an explicit Rex decision, a user
report, an agent proposal and independently checked evidence. Repeated AI
summaries are not independent corroboration. Preserve contradictions and
superseded claims rather than averaging them into agreeable prose.

Evaluate each session for relevance; do not force an Ideal State rewrite after
every session. Update evidence frequently. Revise criteria when an observation
clarifies the desired outcome. Change purpose, constraints or tradeoffs only on
Rex's direction. An explicit decision can be recorded without asking him to
approve it again. An inferred strategic change remains a proposal.

Examples from Attain Prep:

- “No additional team members” is an explicit operating constraint for IS-09.
- A student's difficulty stopping a skill check is evidence for IS-03.
- Dark mode is a feedback request to assess, not automatically a new central goal.
- A shipped tutorial is implementation evidence. It does not establish that a
  new student can navigate without help.

## Challenge to the proposed model

The limiting issue is not the number of documents. It is responsibility at the
handoffs: what enters, what becomes authoritative, what the next agent reads,
and what result changes our belief. More storage can worsen this if it increases
stale copies or repeats agent claims as facts.

The system should become more independent in execution while remaining guided
by Rex's goals. Editing a memory document does not itself authorize a new task.
Success means fewer repeated explanations, fewer corrections, recovered work
and better project outcomes. Document counts, automatic runs and rewritten
criteria do not prove improvement. Include time spent maintaining this system
in the solo-operation test.

## Concrete next work, proposed for design approval

Target repository: `/home/dev/projects/build-ai-automation-workflow`.
Product pilot: `/home/dev/projects/sat-prep`.
Read the current global agreement, repository AGENTS, operating contracts and
this assessment before starting. The queue source currently lives on branch
`claude/notion-work-queue`, worktree
`/home/dev/.local/share/codex-worktrees/notion-work-queue`; its installed private
release is `/home/dev/.local/share/attain-work-queue/current`. Verify current
source and deployment before changing either. Do not assume main contains it.

First prove use of an existing accepted decision in a fresh worker, then connect
new session capture and evidence updates to that same path. This avoids building
another write-only memory pipeline.

Scope:

1. Define one project-context loader for the current Ideal State and relevant
   evidence. Use it for an Attain Prep queue worker and a fresh interactive Codex
   session. Ensure project discovery survives clean worktrees without copying
   unrelated private notes into product releases.
2. Add Codex source discovery and normalization to Loom alongside Claude. Inspect
   `loom/cli.py`, `discovery.py`, `transcript.py`, `state.py` and their tests.
   Handle appended turns, resumed sessions, provider-qualified identities,
   duplicate event representations and helper/self-generated material. A session
   filename alone must not imply that all later content was processed.
3. Route selected Attain Prep decisions and evidence to the canonical project
   records. Preserve human edits with revision checks. Agents may record evidence
   and explicit decisions; unapproved strategic changes remain proposed.
4. Reuse existing logs/health reporting for missed capture, stale context and
   failed updates. Do not add another dashboard or scheduler by default.

Acceptance examples:

- A fresh worker identifies the solo-operation constraint and its source without
  Rex explaining it, and uses it to assess a proposal requiring staff.
- It distinguishes the shipped tutorial from unverified student comprehension.
- A real saved Codex source yields attributable project insight, once; a later
  appended decision is processed without duplicating earlier content.
- A contradictory observation remains visible and changes the next check.
- Failure to read Notion displays missing/stale context rather than inventing
  the current version or silently using a competing copy.
- Completion writes dated evidence linked to the applicable criterion without
  marking a learning outcome achieved merely because code tests passed.
- No new task starts solely because evidence or Ideal State was edited.

The first pilot has a narrow stop condition: demonstrate the complete path on
these Attain Prep cases before broad rollout or historical transcript backfill.
Record Rex's corrections and required manual steps. The pilot can establish
those cases; it cannot prove general system self-sufficiency.

## References

- [Attain Prep Ideal State](https://app.notion.com/p/3d645d882ebb81db9d50e35698091788), read v0.2 on September 9.
- [Project pointer](/home/dev/projects/sat-prep/IDEAL_STATE.md).
- [Installed Loom discovery](/home/dev/loom-runtime/loom/discovery.py:50).
- [Installed Loom parser](/home/dev/loom-runtime/loom/transcript.py:34).
- [Queue prompt source](/home/dev/.local/share/codex-worktrees/notion-work-queue/workqueue/runner.py:53), independently checked against installed module.
- [Official OpenAI instructions discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md): Codex loads discovered AGENTS instructions at session/run start; arbitrary memory documents require an explicit access path.
