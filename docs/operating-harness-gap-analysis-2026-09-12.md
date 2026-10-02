# Portfolio Operating Model: operating harness gap analysis

12 September 2026 | Assessment against [Ideal State v0.6](automation-ideal-state.md)

**Verdict: there is substantial machinery for producing and reviewing work, but no demonstrated portfolio loop that reliably turns current direction into resource choices, verified business progress, and Rex's next decision.** The strongest capabilities are execution, local quality controls, and some recovery paths. The weakest connections are economic prioritization, current-goal inheritance, and returning outcomes to decisions.

The portfolio needs selective repair and connection, plus a serious challenge to recurring work whose consumer is unclear. Replacing Notion, Telegram, runners, or Cloudflare wholesale has no demonstrated case. Building the entire cockpit before addressing commercial opportunities would repeat the current imbalance.

## Basis and limits

This is the integrated assessment following the [harness map](operating-harness-map-2026-09-12.md), superseding the build-order recommendations in the narrower [shared-operations assessment](portfolio-gap-analysis-2026-09-12.md). Discovery covered 38 local project directories, shared skills/commands/hooks, Council, queues, schedulers, learning, infrastructure, and initiative engines. Counts in the map describe discovery breadth, not 38 independent ventures or verified live systems.

The deeper initiative assessments cover Romance, Attain Prep, Ultimate Portugal, and SwimTrack. Evidence includes current source, sampled installed versions, local state and usage records, read-only canonical Ideal States, GitHub run metadata, aggregate Attain activity, a live SwimTrack service flag, and offline dependency/gate probes. Sources and dated observations are retained in the [audit evidence](evidence/gap-analysis-2026-09-12.json), [discovery evidence](evidence/operating-harness-discovery-2026-09-12.json), and [earlier runtime evidence](evidence/portfolio-top-level-2026-09-12.json).

This is not a complete remote-account or financial audit. External-only n8n, Bento, Zernio, other account/device workflows, actual receipts, customer outcomes, and Rex's review time remain partly unverified. An unknown is not zero, a configured schedule is not a successful run, and a successful run is not a useful outcome. The new monthly-review and website requirements are target gaps, not proof that an older tool violated its original mandate.

No live service, queue item, notification, deployment, authority rule, or allocation was changed. Paid helper analysis used existing agent clients; no new paid Council experiment was run.

## The portfolio picture

| Responsibility | Assessment | Practical implication |
|---|---|---|
| Direction and allocation | Ideal States and owner boundaries exist; shared allocation and monthly outcome review are not demonstrated. | Work can be well executed without being the best work to fund next. |
| Offer and product development | Substantial product/content capability and specialist review; customer validation is uneven. | Feature, manuscript, or article completion needs separate evidence that the promise was met. |
| Market and demand development | Acquisition, SEO, social, discovery, and engagement mechanisms exist. Revenue connection is thin in inspected records. | More output is an especially weak default while monetization and attribution remain incomplete. |
| Customer fulfillment and retention | Product journeys, communications, publication controls, and feedback intake exist. End-to-end customer benefit was not established. | Verify delivery of the promise, not only the systems supporting it. |
| Resources and infrastructure | Strongest developed layer: model routing, queues, caps, versioned runtimes, recovery, hosting. Integration and cost justification lag. | Reuse dependable parts; constrain investment in additional infrastructure. |
| Business stewardship | Scoped access and meaningful approval controls exist; action identity, delivery records, and financial reconciliation vary by path. | Make consequential handoffs consistent without adding blanket permission gates. |

These are evidence profiles, not numeric maturity ratings. The proposed maturity rubric is not yet calibrated, and an average would obscure the commercial and reliability gaps.

In OODA terms, there are many ways to **act**, several useful ways to **observe**, and capable tools for **orientation** such as Council and project instructions. What is missing is a dependable connection between them: current intent reaches the worker; observed results change a resource choice; an owner decision completes its journey back into action.

## Findings

### GAP-01. The portfolio decision surface is missing, despite useful initiative interfaces

**Judgment: missing integration; retain the existing contributors.** AUTO-IS-01, 03, 07, 10, 14, 16.

Romance Ops already joins runner reviews, launches, social plans, engagement, and production gates into owner rows and permitted actions. Its three-repository Romance scope is intentional. Attain has a separate Notion product queue; content engines have decision pollers; backlog, DIEM, and Bebop each report different things. No inspected interface reconciles all of them into the authenticated portfolio website Rex specified. [Romance providers](/home/dev/projects/romance-empire/src/ops/providers.py), [Ops scope](/home/dev/projects/romance-empire/config/ops.yaml), [backlog reports](../backlogrun/cli.py), [Bebop briefing](../bebop/prompts/briefing-morning.md).

The shared backlog snapshot contained 117 items: 44 open, eight in review, and 65 held. That is inventory, not evidence of 65 failures or an overloaded owner. There is no measured portfolio review-time baseline. Neither a formal monthly spend-to-outcome cycle nor monthly Ideal State Review was demonstrated.

**Smallest useful response:** use existing stores as authoritative feeds. Give each initiative a stable identity and authoritative Ideal State, and each work item a source, state, next action, authority, result, and observation time. A decision shows the recommendation and consequence before technical detail. Add the website over those feeds; make unavailable coverage explicit. Keep Romance Ops scoped to Romance.

**Proof:** reconcile the view against the inventory, then trace one decision through action and checked result. Show all necessary work in an 80-minute scenario. Use the same evidence for both monthly reviews; do not create a separate reporting system or score engine.

### GAP-02. Current direction and learned corrections do not reliably reach fresh workers

**Judgment: worthwhile mechanisms with an incomplete handoff.** AUTO-IS-02, 04, 12, 15.

Attain's current canonical Ideal State explicitly identifies missing automatic loading. The installed product runner supplies brief, done condition, checks, and feedback, but no explicit Ideal State retrieval. The local `AGENTS.md` and `IDEAL_STATE.md` pointers were present but untracked at inspection, so a fresh worktree from committed code cannot inherit those files through Git. This does not prove no worker ever finds the direction another way. [Runner prompt](/home/dev/.local/share/codex-worktrees/notion-work-queue/workqueue/runner.py:50), [Attain pointer](/home/dev/projects/sat-prep/IDEAL_STATE.md), [canonical Ideal State](https://app.notion.com/p/3d645d882ebb81db9d50e35698091788).

Loom captures Claude transcripts and produces staged knowledge, with careful secret handling and promotion controls. Its extraction policy prioritizes personal knowledge and limits technical/operational items. The Codex mirror deliberately excludes the incompatible Claude transcript hook. A reliable fresh-Codex-worker retrieval path was not demonstrated. Conversely, Romance's explicit lessons in prompts and project instructions are real examples of learning reaching execution. [Loom](../loom/README.md), [distillation policy](../loom/prompts/distill.md), [mirror](../setup/claude-codex-mirror/README.md).

**Smallest useful response:** the trusted task-preparation step supplies the relevant current criterion, accepted direction, dated evidence, and contradictory result, with a source/version. Preserve a brief enough context to use; do not inject an entire knowledge base or give every worker broad service credentials.

**Proof:** a fresh worker receives one previously accepted correction and demonstrably changes its work; its checked result reaches the next assessment. Prove this across Claude and Codex before expanding memory ingestion or storage.

### GAP-03. Resource controls are real; portfolio value is not what selects the work

**Judgment: useful scheduling controls with an incentive that needs changing.** AUTO-IS-06, 07, 15.

The shared runner selects eligible work oldest first. DIEM sorts by banked status, priority, and creation time; defaults derive priority from task type. It has floors, pause/deadline controls, and protection against repeated dry backfill. These are valuable resource controls, but neither mechanism implements cash-first allocation across initiatives. [Backlog selection](../backlogrun/cli.py:427), [DIEM queue](../diem/queue.py), [backfill](../diem/drain.py:154).

In the seven-day local Venice ledger window, Council recorded 513 calls and approximately 14.1 million input tokens; Loom recorded 653 calls and 13.5 million input tokens. Together they represent about 81% of recorded input tokens in that window. This is not 81% of all AI use, cash spending, or waste. From 5 September, the retained DIEM summaries contain 67 successful reviews, 51 successful non-noop backfills, 11 successful noop backfills, and one failed backfill. None establishes benefit by itself. [Dated resource evidence](evidence/gap-analysis-2026-09-12.json).

**The idea to reject is treating credit consumption as success.** Expiring credit can reduce the marginal cash cost of justified work; it does not eliminate review, clutter, maintenance, or displaced capacity. Increasing memory or reviews because credits remain is a poor objective even if the runner executes perfectly.

**Smallest useful response:** retain the scheduler and queue, but attach a purpose and result consumer to recurring work. At allocation time, compare cash protection/generation, Ideal State progress, credible 3/6/12-month horizon, resources, and displaced work. Useful preapproved background work may run; unused capacity is an acceptable result.

**Proof:** follow one allocation through expected benefit, actual resource use, observed outcome, and monthly retain/change recommendation. Keep EUR75/hour explicitly nominal; record what released time actually enabled. Further Council/Loom expansion must earn its cost.

### GAP-04. Council has a reproduced dependency defect and an under-protective tooling gate

**Judgment: good capability, specific execution defects.** AUTO-IS-04, 11, 13, 15.

This repository's review workflow pins Council to `4a01298…`, while its current review script requires `Settings.max_completion_tokens`, absent from that pinned version. An offline probe reproduced `AttributeError`. This establishes a source/dependency incompatibility; no new live CI run was triggered. [Workflow](../.github/workflows/venice-review.yml:30), [consumer](../scripts/venice_review.py:186), [existing rollout work](ci-usage-rollout-2026-09-12.md).

Separately, Council classifies tooling-only changes as reduced tier. A pure-function probe using the review script and workflow paths found that a high-severity, high-confidence, chair-confirmed finding yields no block at reduced tier but one at full tier. The retained chair bake-off also exposes gate eligibility problems in known cases. Review quality cannot compensate for a gate that excludes the finding. [Gate](../council/gate.py:57), [experiment](chair-bakeoff-results-2026-09-12.md), [probe evidence](evidence/gap-analysis-2026-09-12.json).

**Smallest useful response:** repair the dependency through the existing rollout; classify privileged CI, deployment, and external-write tooling by consequence. Keep low-risk tooling checks proportionate. Retain Council's full-file grounding, failure handling, disagreements, and regression fixtures.

**Proof:** the pinned consumer imports and uses the required settings; the known consequential fixture blocks while known false-positive fixtures remain clear. Use offline regression before paying for more model comparisons. These findings do not establish that every repository's CI is broken or justify changing the global model winner.

### GAP-05. Decisions and feedback can stop at a boundary instead of completing the work

**Judgment: uneven integration; three concrete source gaps.** AUTO-IS-04, 05, 12, 13.

1. **Romance “Changes” on an existing branch does not launch its rework.** It records the review and holds the item for a session. Already-held branches remain held with the comment appended. This is explicitly documented behavior, but it falls short of the desired routine decision-to-action loop. An inspected button path does not supply the promised continuation. [Actions](/home/dev/projects/romance-empire/src/ops/actions.py:530).
2. **Attain's feedback brief asks the worker to update live feedback status, while the shared worker forbids live writes.** Standing authorization for triaging clear feedback exists; the mismatch is where status reconciliation belongs. The source finding does not prove a particular customer report is currently stale. [Feedback contract](/home/dev/projects/sat-prep/src/lib/feedbackBacklog.ts:65), [worker boundary](../backlogrun/cli.py:561).
3. **The direct backlog approval path lacks the reviewed-head check used by Romance Ops.** It validates status, branch, checkout, and merge conditions, but does not require the branch to equal the reviewed SHA. Its display presents readiness evidence that the action function does not itself consult. [Direct approval](../backlogrun/cli.py:1358), [Ops check](/home/dev/projects/romance-empire/src/ops/actions.py:514).

**Smallest useful response:** add bounded continuation for already-requested rework; make a trusted controller reconcile feedback status from checked results; bind consequential action to the exact work/version Rex reviewed. Carry readiness and unresolved conditions into the decision itself.

**Proof:** one “Changes” request becomes revised work and returns for review without a new design-approval ritual; feedback state matches the verified work state; changing a branch after review invalidates that approval context. Advisory readiness and Rex's legitimate explicit overrides must remain distinct from mandatory gates. This is not a recommendation to block every non-ready item with a new blanket rule.

### GAP-06. Notification delivery and monitoring can conceal a broken handoff

**Judgment: concrete reliability defects plus incomplete coverage.** AUTO-IS-01, 05, 09, 11, 13.

The installed `agents` nudge records its state after a failed send attempt, which can suppress another nudge. The shared Telegram sender discards the response body and treats HTTP 200 as success without retaining a provider receipt. Watchdog writes suppression state before its wrapper attempts delivery. The session bridge binds approval to a topic and time window rather than a durable exact pending action. These are source findings, not observed lost notifications or wrong approvals. [Agents](/home/dev/.local/bin/agents:120), [sender](../bin/tg-send), [watchdog](../watchdog/run.py:225), [bridge](../session-bridge/src/router.ts:25).

Watchdog also checks only a subset of the active responsibilities. A healthy result cannot establish portfolio health when several runners and result streams are outside its expected-output coverage. [Collection scope](../watchdog/run.py:37).

**Smallest useful response:** reuse the [existing handoff repair proposal](alert-handoff-2026-09-06.md). Retain the unresolved event independently of its notification, validate provider acceptance, expose failed/uncertain delivery, and bind replies to exact action identities. Extend existing health collection with expected result freshness and explicit paused/unobserved states.

**Proof:** a failed send remains retryable; an old reply cannot authorize a different action; an interrupted outward action is checked before replay; one deliberately missing expected result becomes visible. Receipt means provider acceptance, not proof Rex read or acted. Preserve existing mandatory alerts while applying the new cockpit cadence.

### GAP-07. Some skill rules manufacture ceremony rather than judgment

**Judgment: useful practices with overbroad triggers and conflicting instructions.** AUTO-IS-04, 13, 14, 15.

The installed brainstorming skill requires approval even for a bounded one-file fix. TDD and review skills have broad mandatory triggers, and independent review can overlap the established Council route. Higher-priority working instructions already preserve authorization and proportionality, but workers must reconcile these competing rules repeatedly. This establishes avoidable interpretation burden, not a measured number of blocked hours. [Brainstorming](/home/dev/.codex/skills/brainstorming/SKILL.md), [TDD](/home/dev/.codex/skills/test-driven-development/SKILL.md), [review](/home/dev/.codex/skills/requesting-code-review/SKILL.md), [working agreement](/home/dev/.claude/CLAUDE.md).

**Smallest useful response:** align the maintained skill sources and mirrors around one route per task: short intent and focused verification for authorized routine repairs; deeper design and independent review for consequential work. Preserve project-required tests and genuine release boundaries. Count copied skills as deployment artifacts, not independent capabilities.

**Proof:** an authorized broken-form repair proceeds while Rex is absent; an experiment on a working form receives the appropriate hypothesis and result review; a pricing change still escalates. A larger checklist or more reviewer calls is not evidence of higher operating quality.

### GAP-08. Experiments and performance reports do not consistently become owner decisions

**Judgment: useful measurement, incomplete return path.** AUTO-IS-02, 03, 07, 08, 12.

Council's chair bake-off retains controlled evidence and correctly leaves a consequential choice to the operator. The inspected machinery does not demonstrate an automatic next-cockpit decision record; it does not prove Rex never reviewed the report elsewhere. [Bake-off results](chair-bakeoff-results-2026-09-12.md).

Romance's engagement pilot is at day six of fourteen in the inspected scorecard: 12 posted, ten measured, eight improved placement. Its measure is placement among the top 25 comments, not traffic or cash. Day-14 code produces a recommendation, while Ops exposes daily watches rather than the final experiment decision. That is a pending handoff gap, not a failed pilot before its agreed endpoint. The separate social scorecard is dated 5 September and contains metrics for ten of 139 listed posts, with major commercial fields blank; the listed denominator is not established as published posts. [Pilot](/home/dev/projects/romance-empire/series/heron-creek/engage/engage-scorecard.md), [day-14 logic](/home/dev/projects/romance-empire/src/social/engage_scan.py:1578), [social scorecard](/home/dev/projects/romance-empire/series/heron-creek/social-scorecard.md).

**Smallest useful response:** on completion, create one prepared decision in an existing owner surface: hypothesis, agreed test, result, uncertainty, resource use, recommendation, and source. Route pass, fail, and inconclusive outcomes alike to Rex's next review. Keep routine monitoring short when nothing warrants a decision.

**Proof:** a completed experiment reaches a retained owner choice and authorized next action. The threshold alone never scales, repeats, or abandons the experiment. Urgent interruption remains limited by the agreed material-loss rule and existing notification obligations.

### GAP-09. Commercial activation trails supporting machinery in two initiatives

**Judgment: incomplete commercial paths; deployment holds are not malfunction.** AUTO-IS-02, 03, 06, 12, 15.

**Ultimate Portugal:** the accepted model is affiliate-only. All 15 entries in the current local partner registry have status `none`; none has an affiliate URL. The repository can publish and improve guidance, but this registry cannot currently connect a recommendation to an affiliate commission. Remote partner-account status and all possible revenue sources were not audited. [Registry](/home/dev/projects/ultimate-portugal/src/data/partners.json), [canonical direction](https://app.notion.com/p/3d645d882ebb81fc951fd6b4918dd767).

**SwimTrack:** the live anonymous season-account endpoint returned HTTP 200 with `account: true`, `checkout: false` at 18:29 UTC. The current local code contains substantial season payment and entitlement machinery. Accounts are now enabled, correcting the older Ideal State draft's account-off description. The flag does not prove successful sign-in, SMTP, or end-to-end payment behavior. [Live service endpoint](https://swimtrack.ai/api/season/account?seasonId=2026-2027), [service contract](/home/dev/projects/swimtrack-website/docs/season-services.md), [canonical draft](https://app.notion.com/p/3d745d882ebb8162be7fe53e04539ce9).

**Smallest useful response:** bring forward one credible affiliate activation case for UP and one evidence-based season-pass release decision for SwimTrack. State the customer promise, first tangible result by three months if credible, cash test, costs, dependencies, and stop condition. Preserve UP's editorial independence and SwimTrack's actual coverage promise. Neither ads for UP nor prematurely charging SwimTrack parents follows from “cash first.”

**Proof:** an approved UP route can attribute a qualifying conversion and reconcile a commission; an approved SwimTrack release can demonstrate purchase, entitlement, delivery, and recovery/refund. A shipped mechanism is not revenue: report receipts separately. Preparing these cases should proceed alongside essential harness repairs, without waiting for the full cockpit.

### GAP-10. SwimTrack's separate recurring editorial scan has not earned its ongoing machinery

**Judgment: strongest candidate to simplify or pause, conditional on a short consumer check.** AUTO-IS-06, 07, 12, 15.

The separate editorial workflow completed successfully on each day from 1 to 12 September. Its configured pipeline uses three Opus stages and selects up to five items; it creates review artifacts/issues. The website engine separately collects, judges, drafts, and requests publication decisions. Scoped source searches found no runtime transfer from the editorial archive/queue into that engine. The current editorial sources also emphasize US/global/European material, while Portugal-specific scrapers are deferred and the accepted current initiative focus is Portugal. Foreign material can still help Portuguese parents; relevance and actual use need evidence. [Editorial configuration](/home/dev/projects/swimtrack/editorial/sources.yaml), [workflow](/home/dev/projects/swimtrack/.github/workflows/editorial-scan.yml), [latest observed run](https://github.com/rexmcintosh/swimtrack/actions/runs/34687518796), [website loop](/home/dev/projects/swimtrack-website/engine/MORNING.md).

**Smallest useful response:** inspect a small recent output sample for an actual consumer and changed decision. If useful, choose one discovery owner and the smallest necessary handoff. If not, recommend pausing the recurring scan or reducing it to demand-driven use. Do not build an integration merely to justify an unused engine, and do not silently stop an authorized initiative or obligation.

**Proof:** a retained scan produces a relevant used item or decision at a justified total cost, including owner review. If it cannot, its continuing schedule has no established case. The successful-run history establishes execution, not net contribution.

## Initiative conclusions

| Initiative | Purpose and execution | Next most useful move |
|---|---|---|
| Shared harness | Substantial reusable execution, review, and recovery. Portfolio orchestration and outcome learning are partial. Extra infrastructure currently has a weaker case than repairing the named handoffs. | Repair GAP-04/05/06; prove one current-goal-to-result journey; build the smallest authenticated cockpit over existing sources. |
| Romance | A coherent commercial production/distribution system with unusually developed domain checks and the closest existing owner-action loop. Actual incremental cash benefit of social, engagement, and repeated QA is unproven in inspected evidence. | Complete the rework path, prepare the pilot's day-14 owner decision, and connect existing commercial reporting to recommendations. Keep specialist QA and human taste gates until defect/cost evidence supports a change. |
| Attain Prep | Accepted direction is retained independent learning, usable student/parent journeys, and sustainable solo operation. The product queue has bounded execution and durable recovery. Activity is observable, but learning benefit is not established by it. | Make workers inherit current criteria; reconcile feedback through the trusted controller; test one important student outcome before funding feature volume. |
| Ultimate Portugal | Affiliate-only, trusted actionable guidance with organic and AI visibility ambitions. Writing, refresh, SEO recommendations, and spend guards exist. Affiliate activation and visibility evidence lag. | Prepare a narrowly scoped affiliate cash test. Retain decision-first SEO reporting and the refresh wrapper. Measure visibility before claiming progress. |
| SwimTrack | Accepted Portugal-first direction makes Navigator the primary product, supported by useful parent content. Website publishing has owner controls; paid access is built but checkout is off. Separate discovery lacks a demonstrated consumer. | Prepare the season-pass readiness/release case; challenge the separate editorial schedule; show publication decisions with age, evidence, and consequence. |
| Other sites, research, finance, personal work, MeetTrack/coaching | Mechanisms were inventoried; comparable initiative outcome and funding evidence was not inspected in depth. Some loops are intentionally paused. | Keep explicit “unassessed” or “paused” state, resolve active ownership/remote coverage, and assess material active initiatives next using the same method. Do not treat lack of cron as inactivity or a paused product as a broken engine. |

Two outcome cautions matter. Attain's read-only market-deployment sample contained 2,762 events in seven days, including 265 question submissions, but includes testing activity and no cohort separation. It cannot establish independent retained learning, conversion, or revenue. UP's visibility benchmark has 12 questions and five target engines but zero recorded answer observations; an unavailable traffic observation is not zero traffic. [Aggregate evidence](evidence/gap-analysis-2026-09-12.json), [UP benchmark](/home/dev/projects/ultimate-portugal/engine/visibility/benchmark.json).

For Romance, an owner “Done” action is valid human attestation. Label its provenance appropriately; do not invent a mandatory external confirmation gate for every Rex-reported upload. Historical manuscript defects recorded in instructions are useful leads, not newly reproduced incidents. SwimTrack's five locally ready publication cards similarly do not establish owner overload or a failed reminder: waiting by choice, missing delivery, and process failure must be distinguished.

## What to retain, repair, connect, or challenge

The [mechanism map](operating-harness-map-2026-09-12.md) supplies the entrypoints and sources for these families. This table records disposition, not authorization to alter them.

| Mechanism family | Disposition and reason |
|---|---|
| Council decision/brainstorm/red-team panels, comparisons | **Retain, justify each use.** Useful judgment tools; mode availability does not establish usage or benefit. Do not invoke every panel by default. |
| Council code/spec review and security sweep | **Repair and retain.** Fix pin/gate issues; carry findings to owned work. Security sweeps report rather than silently implement. |
| Council regression and model bake-offs | **Retain, bound experiments.** Controlled evidence already finds consequential defects; return completed experiments to Rex. |
| Skills, commands, hooks, mirrors, model delegation | **Simplify policy and retain specialist capability.** Remove conflicting ceremony at maintained sources; preserve correct project boundaries and mirror compatibility. |
| Shared backlog runner and `/close` | **Retain and connect.** Existing intake, isolated work, review, and preservation; repair exact-version/rework handoffs and selection context. |
| Attain Notion queue | **Retain.** Versioned installation, bounded worker, durable journal/outbox are useful. Add current-goal context; do not rebuild it because its source is on another checkout. |
| `fixit` | **Retain as an available bounded tool.** Default dry-run and existing review route; a fully autonomous intake was not established. No reason to add a competing universal queue. |
| DIEM and usage/billing reconciliation | **Retain controls, change success measure.** Resource accounting supports allocations; draining credit is not a business outcome. Billing recovery's recurring installation is not fully established. |
| Romance Ops | **Retain within its mandate.** Strong reviewed-SHA and durable-intent patterns; fix branch rework and expose outcome evidence. |
| Romance production, quality, copy, images/video, distribution | **Retain; measure marginal contribution.** These form a commercial workflow. Repeated checks and content volume must justify cost; no blanket QA deletion. |
| Romance engagement and performance | **Complete the agreed pilot and owner review.** Placement and activity are intermediate evidence, not cash. |
| UP writing, SEO, refresh, rent verification | **Retain and connect to purpose.** Decision-first reporting and wrapper-level exit reporting are useful references. Activate a credible commercial path. |
| SwimTrack website writing/publishing/translation/images | **Retain within current direction.** Connect to parent/product outcomes, expose pending decisions; do not restart deferred language expansion. |
| Separate SwimTrack editorial discovery | **Challenge; pause/merge/demand-driven candidate.** Actual downstream use determines which option is justified. Another adapter is not the default answer. |
| Attain product telemetry, feedback, parent digest, contact sync | **Connect and verify.** Repair feedback status ownership; prove customer benefit/delivery. External communications were not end-to-end audited. |
| Loom, memory, wiki promotion | **Keep guarded capture; prove retrieval before expansion.** More stored text is not necessarily improved work. Preserve secret gates and rollback. |
| Watchdog, Telegram bridge, nudges | **Repair and reconcile coverage.** Preserve unresolved events and exact action identity; no new competing bot. |
| Session GC, versioned releases, VPS tools | **Retain.** Work preservation and tool recovery have a clear purpose. `vps-tools` now exists, correcting the older missing-versioned-home concern. |
| Bebop, finance/bidding/research tools, paused supply loops | **Keep scoped; benefit or live state partly unassessed.** Personal briefing is not the portfolio cockpit; manual analysis is not automated transaction authority. |
| Hosting, access, remote connectors | **Reuse where suitable; finish read-only coverage.** Existing Workers/VPS/services are assets. Unverified account coverage does not justify a vendor migration. |

## Assessment against every Ideal State criterion

“Partial” means some relevant capability is evidenced, not that the whole criterion passes. “Not demonstrated” distinguishes absent evidence from an observed failure. The criteria remain proposed tests of Rex's accepted direction.

| Criterion | Current assessment | Findings |
|---|---|---|
| AUTO-IS-01: complete authenticated cockpit | Not demonstrated; several disconnected owner interfaces exist. | GAP-01, 06 |
| AUTO-IS-02: evidence of current-goal progress | Partial; current context and customer/cash outcome linkage are incomplete. | GAP-02, 08, 09 |
| AUTO-IS-03: prepared decisions and challenges | Partial; Ops and UP reporting are useful examples; portfolio choices remain fragmented. | GAP-01, 08, 09 |
| AUTO-IS-04: authorized routine improvement | Partial; bounded workers exist; rework/context/skill conflicts impede the desired loop. | GAP-02, 04, 05, 07 |
| AUTO-IS-05: correct authority and continued unrelated work | Partial; strong local safeguards coexist with inconsistent action identity. | GAP-05, 06 |
| AUTO-IS-06: shared limits and cash-first work | Local limits supported; shared economic selection not demonstrated. | GAP-03, 09, 10 |
| AUTO-IS-07: monthly allocation versus results | Not demonstrated as a portfolio practice. | GAP-01, 03, 08 |
| AUTO-IS-08: all experiment results return to Rex | Reports exist; dependable cockpit handoff not demonstrated. | GAP-08 |
| AUTO-IS-09: appropriate timing and dependable alerts | Partial, with delivery/suppression defects in inspected source. | GAP-06, 08 |
| AUTO-IS-10: necessary work visible; aspirational hour | Not demonstrated at portfolio level; no time baseline or reason to impose a cap. | GAP-01 |
| AUTO-IS-11: bounded failure and clean recovery | Partial; durable recovery patterns exist, but notification gaps and untested allowances remain. | GAP-04, 06 |
| AUTO-IS-12: complete OODA and fresh-worker context | Partial; especially weak at context and result handoffs. | GAP-02, 05, 08, 10 |
| AUTO-IS-13: explicit useful boundaries | Partial; safeguards are often clear, but contract conflicts and silent continuation gaps remain. | GAP-04, 05, 06, 07 |
| AUTO-IS-14: useful stage-appropriate practices | Specialist practices are used; a calibrated evidence-based profile is not established. | GAP-01, 07 |
| AUTO-IS-15: least justified total machinery | Not established; recurring-work incentives and overlapping discovery are counterevidence. | GAP-03, 07, 10 |
| AUTO-IS-16: monthly review of the destinations | Not demonstrated as a recurring portfolio practice. | GAP-01 |

No whole-portfolio criterion is declared passed on this sample. That is an evidence conclusion, not a claim that nothing works or a mandate to maximize maturity scores.

## Proposed work order

These are concrete follow-on scopes, not changes made by this audit. Do not queue an unbounded portfolio rebuild. Existing authorization remains in force; the proposals do not add approval gates to already-authorized routine work.

| Work package | Bounded deliverable and completion check | Authority and value |
|---|---|---|
| **A. Repair known consequential handoffs** | Use existing CI/alert work: compatible Council dependency; consequence-appropriate gate regression; retryable delivery; exact action/version identity. Show the failing cases now pass without broadening unrelated gates. | Routine repairs proceed under existing boundaries. Any authority expansion remains Rex's decision. Protects useful execution and avoids loss. |
| **B. Bring forward the cash decisions** | One UP affiliate activation case and one SwimTrack season-pass readiness case, with customer promise, cash test, resources, dependencies, credible horizon, and stopping rule. Prepare alongside A. | Rex decides new commitments/release choices. Cash outcomes are measured as receipts or avoided loss, never nominal time. Do not wait for the complete cockpit. |
| **C. Close one existing initiative loop** | Supply Attain's current criterion at preparation; reconcile feedback through its controller. Repair Romance branch rework as a separate bounded continuation. Trace accepted intent → work → checked result → next decision. | Keep worker privileges and review requirements intact. Proves the smallest reusable handoff before wider rollout. |
| **D. Deliver the first useful website** | Authenticated portfolio view over existing feeds: initiative, current goal/evidence, unattended health, work, decisions, allocations/use, and unresolved coverage. One exact-action journey works end to end; all necessary decisions remain visible. | Prepare a scoped implementation and budget using current hosting options. Do not relocate every store, replace every queue, or build a generic analytics platform first. |
| **E. Reduce unjustified recurring work and establish review** | Short consumer check for SwimTrack editorial; one fresh-worker learning test; small-task skill-path check. Prepare one monthly spend/outcome and Ideal State review from existing records, with retain/change/pause recommendations. | Rex decides material allocation/direction changes. Keep adequate processes; deeper investigation requires a specific question or discovery budget. |

The near-term test of this program is practical: useful authorized work finishes with fewer handoff failures, the next commercial decisions become actionable, and Rex can see what changed and why it matters. Additional machinery earns a place only when those outcomes require it.
