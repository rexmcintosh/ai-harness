# Portfolio Operating Model: Ideal State

Version 0.6 | 12 September 2026 | Owner: Rex | Compaction of v0.5

**Status:** working synthesis. D01-D29 are Rex's confirmed decisions. The central promise, criteria, and checks formalize that direction; unapproved design details remain proposals. No operational criterion is declared achieved. This document does not install automation, run services, or change existing execution, publishing, spending, or approval authority. For discovery rationale, scenario dialogue, and full source excerpts behind D18-D27, see the archived [v0.5](history/portfolio-operating-model-v0.5.md); historical, not current.

## Central promise

Rex runs his ventures as the sole human decision maker: he sets direction, chooses priorities, and decides what to build next. AI-supported operations turn that direction into useful, verified progress within agreed limits.

Each morning, one cockpit shows the full portfolio: active initiatives, work in progress, unattended operations, progress toward each Ideal State, blockers, and the decisions that need Rex. It explains what changed and why, and challenges his priorities when evidence supports a better choice. **The cockpit is an authenticated website Rex logs into**, connected to the infrastructure it needs to show that picture and act within its limits (D28).

Routine work and improvement loops continue unsupervised. Ambiguous intent, major tradeoffs, and scarce-resource requests arrive as prepared recommendations, leading to action, checked results, and better-informed later work. OODA is the core decision loop: observe, orient against intent and reality, decide within authority, act, and feed results back into the next observation. Reports use plain business language.

Resources go first to protecting or improving cash flow, then to the greatest useful Ideal State progress, reviewed monthly against whether the spending mattered. A one-hour morning queue is an aspiration, not a current optimization target. Show necessary work in full, adjusting only on a pattern of unfinished reviews or Rex's request. A formal monthly Ideal State Review checks, per initiative, whether the destination remains right and what progress the evidence supports. Evidence that undermines a destination also triggers a challenge in the next morning's cockpit, without waiting for the monthly review. Rex decides any change of direction; D12 governs urgent alerts.

Each initiative also develops an agreed operating model; capabilities and practices that improve its thinking and execution; with maturity shown alongside actual results, earned through useful decisions rather than framework adoption. The whole system runs as lean as possible, including its own infrastructure: Notion, Telegram, backend runners, Cloudflare workers, and similar components should be streamlined around initiative outcomes rather than accumulate as separate, loosely connected tools (D29). Thrift applies to reasoning, execution, coordination, maintenance, and attention; additional machinery must earn its cost.

## Confirmed decisions

Accepted directions from the discovery conversation, not proof the system implements them. D01-D27 preserve the substance of v0.5; see it for originating dialogue.

| ID | Owner decision | Boundary |
|---|---|---|
| D01 | Rex directs the portfolio and remains its sole decision maker. | AI carries routine execution and prepares consequential choices. |
| D02 | A daily cockpit covers all initiatives, work, unattended operations, progress, blockers, decisions. | Explains practical consequence, not just activity. |
| D03 | Clear improvements proceed automatically within constraints and authority. | Ambiguity, major tradeoffs, and limit-exceeding requests come to Rex. |
| D04 | Money, tokens, execution capacity, time, and attention are limited across the portfolio. | Work is scoped against constraints before commitments accumulate. |
| D05 | First priority: protect/improve cash flow. Second: Ideal State progress. | Extra spend needs a credible, testable expected benefit. |
| D06 | Tangible-result horizons: 3, 6, or 12 months; beyond 12 is a strategic bet. | Strategic bets need a developed case and Rex's decision. |
| D07 | Initiatives pitch for budget; review spend and results monthly. | No quarterly or weekly standing review agreed. |
| D08 | Authorized routine work continues if Rex misses reviews; major decisions wait. | Absence is not approval or extra budget. |
| D09 | The major-decision categories below are a starting point. | Rules must not hide barriers to useful work. |
| D10 | A nonessential public page may break up to one hour during an automated improvement. | Payments, customer data, core service unaffected; recovery available. |
| D11 | Rex reviews experiment results and decides the next step: pass, fail, or inconclusive. | A threshold alone doesn't authorize expansion, repeat, or abandonment. |
| D12 | Completed experiment reviews land in the next cockpit; immediate alerts only for material-loss risk. | Existing mandatory notifications stay in force. |
| D13 | Desired morning queue: no more than one hour including decisions. | Aspirational; D20; not a scheduling cap. |
| D14 | The cockpit should challenge Rex's priorities. | Informs Rex; never silently replaces his direction. |
| D15 | Agreed frameworks should improve thinking/execution, with operating quality visible. | Specific frameworks, rubric, and any score remain proposals. |
| D16 | The operating map covers the whole venture with non-duplicated responsibilities; Rex favors Minto, Pareto, Great Mental Models, Sivers. | Examples illustrate without narrowing scope; stay practical. |
| D17 | OODA is Rex's preferred core framework; explore MAGTF/OSMEAC critically. | No military tone/role-play; adaptations remain proposals. |
| D18 | Run as lean as possible; extend "two pizzas" to the whole system. | Assess total effort and machinery; numeric limits remain open. |
| D19 | Use the short check when targets are met, evidence is fresh, no new issue appears. | Deeper analysis needs a specific question or discovery budget. |
| D20 | Display necessary work even past 60 minutes; adjust only on a pattern of unfinished reviews or Rex's request. | Don't delay work solely to fit the hour. |
| D21 | Restoring a broken form is routine work; testing fewer fields on a working form is an experiment. | Repair proceeds within authority; experiment results return to Rex. |
| D22 | EUR 75/hour is Rex's starting time-value planning assumption. | Theoretical until it contributes to outcomes; not cash income. |
| D23 | A time-saving case names net hours released, what they enable, and its evidence. | Can't repeatedly displace cash work via theoretical savings. |
| D24 | The system challenges an Ideal State when credible evidence undermines its case. | Goes to next cockpit; Rex decides; no silent rewrite. |
| D25 | Conduct a formal Ideal State Review monthly, across initiatives. | Complements the spend review; date/format open. |
| D26 | An interrupted worker's replacement recovers intent, confirms outcome, resumes without duplication. | Hold uncertain actions; escalate consequential uncertainty; other work continues. |
| D27 | Keep a dependable process that meets the initiative's needs; improvement must justify its cost. | Max maturity not required; a higher score alone doesn't justify work. |
| **D28** | **The cockpit is an authenticated website Rex logs into**, connected to the core infrastructure needed to show portfolio state and support decisions and authorized action. | Confirmed direction; build, hosting, and authentication details remain open. |
| **D29** | **Simplify and streamline integrated infrastructure** (Notion, Telegram, backend runners, Cloudflare workers, similar) around initiative outcomes, cutting incidental complexity and duplicated wiring. | Direction for design, not a mandate to replace any vendor; authorizes no live change. Consolidation still goes through the major-decision and funding rules. |

## Scope and ownership

Covers the combined working system across ventures: shared services, project-specific flows, interactive AI work, scheduled jobs, event-driven workflows, external services, and their handoffs. A count of local scheduled jobs is not the full portfolio.

Each initiative keeps its own authoritative Ideal State. Shared infrastructure has explicit outcomes linked to the initiatives it serves; one component owns each operational responsibility, though several may contribute to one outcome.

**Proposed principle:** the cockpit (D28) presents these sources coherently rather than creating competing definitions of an initiative's direction; memory, evidence, queues, and implementation docs support the Ideal State without redefining it. Streamlining (D29) should reduce the number of places a definition could drift, not just relocate it.

## Operating model quality and capability maturity

**Status:** proposed response to D15/D16; a tailored approach for one human supported by AI, not a certification scheme or corporate org chart. Three layers: **Ideal State** (outcomes sought), **operating model** (capabilities/practices meant to produce them), **capability maturity** (evidence of how defined, used, dependable, and improving those practices are). Results and maturity stay separately visible; good results can coexist with fragile operations, and vice versa; a maturity score is evidence about capability, not a guarantee of success.

### Six responsibilities (proposed partition)

| Responsibility | Owns | Boundary |
|---|---|---|
| Direction and allocation | Purpose, Ideal State, model, priorities, start/scale/pause/close. | Sets the mandate; Rex keeps major decisions. |
| Offer and product development | Scope, design, standards, roadmap, validation. | Defines the promise; doesn't win/fulfill demand. |
| Market and demand development | Customer evidence, positioning, acquisition, sales. | Wins customers; fulfillment owns the rest. |
| Customer fulfillment and retention | Onboarding, delivery, support, renewals, recovery. | Delivers the promise; new capability is offer's. |
| Operating resources and infrastructure | Tools, agents, suppliers, capacity, infrastructure. | Owns the means of work; D29's streamlining lives here. |
| Business stewardship | Records, contracts, access policy, compliance. | Owns records/controls; no new permission gate. |

Day-to-day execution against agreed standards and targets belongs within each responsibility. Assign a recurring task by the primary output that changes, not a generic "delivery" bucket; the same people/agents/tools may cover several responsibilities. OODA structures the shared learning loop: owners produce their own evidence, the loop defines consistent measures and preserves decisions without duplicating every outcome. **Coverage checks:** score the smallest useful practice once, each handoff with a named output and owner (e.g. a price change separates evidence, Rex's decision, implementation, and recording); shared services are assessed as both a service and a dependency, without double-counting cash gains; unknown ownership, omitted work, and duplication are audit findings.

### OODA at the core (proposed civilian adaptation)

Boyd's OODA; observe (gather dated signals; a worker's claim isn't outcome evidence), orient (interpret against current intent, separating observation from forecast and inference), decide (an authorized response or escalation, respecting allocations; Rex interprets experiments), act (complete, check, feed results back; completion isn't benefit, no unbounded retries); as concurrent loops per MCDP 6, not a neat sequence. Intent for consequential work stays brief and stable: outcome, why it matters, conditions to protect, resources, decisions reserved for Rex. One loop runs at several scales: routine work, initiative improvement (observe long enough to read the result), daily portfolio review, monthly allocation review, and the monthly Ideal State Review. Shortening harmful delay is useful; speeding loops up for its own sake is not.

### Command discipline and thinking disciplines

**Proposed:** MAGTF's task organization (one accountable lead assembles capabilities around an outcome, Rex supplies intent, no permanent hierarchy), OSMEAC as an assignment checklist, and Minto structuring upward reports (recommendation first). The analogy has limits; business success means customer value and trust, and visibility must not create micromanagement. Minto/MECE, Pareto (real distributions, not assumed 80/20), Sivers' *Anything You Want*, and selected Great Mental Models (first principles, inversion, second-order thinking, probabilities, feedback) are the default thinking tools, used when they improve a consequential judgment; no model quota or adoption score.

### Proposed maturity rubric

A custom scale inspired by capability-maturity thinking, not a CMMI rating; **no maximum score required** (D27).

| Level | Meaning |
|---|---|
| 0: Absent | No established practice (vs. "unknown" for missing access). |
| 1: Defined | Coherent, scoped, assigned; recurring use unproven. |
| 2: Used | Demonstrably shapes real work, with traceable examples. |
| 3: Dependable | Consistent under agreed conditions without Rex reconnecting the steps. |
| 4: Improving | Feedback improves it while it stays dependable, with retained counterevidence. |

Profiles show current level, stage target, dated evidence/confidence, and the next valuable improvement, across the six responsibilities plus OODA effectiveness. Scores can fall; critical gaps can't hide in an average; no aggregate score or pass threshold is approved. Under D27, dependable practices meeting the initiative's needs count as success; further work needs a credible future benefit, not just a higher score.

## Resource and decision rules

Cash-flow gains and avoided losses are first priority; other Ideal State improvement is second; no numeric weighting formula agreed. **Proposed conventions:** report the earliest credible horizon for first tangible result (by month 3 / 3-6 / 6-12 / beyond 12), separating first evidence, first benefit, and payback, without crediting one benefit to several components.

D22/D23 govern time-value: EUR 75/hour is a planning figure only; three hours released is EUR 225 of **nominal** capacity, not cash earned, checked in monthly review against realized use without double-counting the nominal figure and any eventual cash result. **This is not a time-saving treadmill**; nominal savings alone can't beat a credible cash opportunity or perpetually defer cash-generating work.

A funding case names outcome, expected benefit, agreed test, resources, constraints, alternatives, horizon, and stopping conditions; the monthly review compares results against it. Money, tokens, execution capacity, time, and attention are distinct constraints; token counts alone aren't total cost. Budgets, concurrency limits, materiality thresholds, and accounting conventions remain open.

**Major decisions that come to Rex:** start/pause/close an initiative or change its audience, business model, or Ideal State; displace agreed work by moving resources; exceed an allocation, create a new ongoing obligation, or fund a bet beyond 12 months; materially change pricing, guarantees, customer promises, or data use; expand automation authority or remove an approval; sacrifice an agreed outcome or standard; take a hard-to-reverse action risking material loss, customer trust, or destruction without proven recovery. The goal is reusable standing boundaries, not repeated permission. A blocking rule gets named with the blocked outcome and a proposed change, while other authorized work continues.

**Failure allowance (D10):** a nonessential public page may break up to one hour during an automated improvement if payments, customer data, and core service are unaffected and recovery is available. **Proposed:** measure from actual impairment, not detection; define nonessential pages and demonstrate recovery before this becomes unattended authority; repeated disruptions stay visible. Other allowances must state scope, max duration/loss, recovery method, and escalation boundary. An experiment stops at its agreed limit while results await Rex; the packet carries hypothesis, results, uncertainty, cost, and a recommendation. **Confirmed (D21):** restoring agreed behavior is routine repair; changing a working feature to test a hypothesis is an experiment, regardless of label.

## Ideal State criteria and checks

All criteria are **proposed**; all attainment is **not assessed**; this defines the target, not a live-operation finding. Each eventual check records date, source, sample, window, and limitations.

| ID | Desired outcome | Basis | Check | Counterevidence |
|---|---|---|---|---|
| AUTO-IS-01 | An authenticated web cockpit accounts for every initiative, work, unattended responsibility, and handoff; gaps visible. | D01, D02, D28 | Verify the logged-in view, reconcile its sources against an independent inventory, and trace sampled work. | A flow is absent, abandoned work looks active, or a handoff hides behind working parts. |
| AUTO-IS-02 | Progress claims reference the current Ideal State with dated evidence, distinct from activity. | D02, D05 | Trace claims to their criterion; inspect a missed outcome despite successful runs. | Run counts stand in for success; unknowns shown as pass/fail. |
| AUTO-IS-03 | Decisions arrive prepared (problem, options, recommendation, evidence, tradeoffs); priorities get challenged when warranted. | D02, D03, D14, D24 | Review real packets for needed reconstruction; include a priority-challenging case. | Rex digs through logs, gets agreeable summaries, or a priority is silently substituted. |
| AUTO-IS-04 | Clear authorized improvements are diagnosed, executed, checked without repeated permission. | D03, D08, D09, D21 | Trace a routine improvement including an absent-owner period. | Redundant approval waits, blind execution, or "complete" means only "attempted." |
| AUTO-IS-05 | Major decisions arrive prepared; actions stay in-authority while unrelated work continues. | D03, D08, D09, D11 | Exercise an in-budget tradeoff, an overrun, and an already-approved action. | Silence grants permission, a budget permits a strategic change, or one block freezes unrelated work. |
| AUTO-IS-06 | Work is scoped and selected against shared limits, cash-flow first. | D04-D07, D20, D22, D23 | Compare simultaneous requests and displaced work. | An initiative assumes full capacity, forecasts omit cost, or ease beats usefulness. |
| AUTO-IS-07 | Monthly review compares each allocation with real results to inform the next decision. | D06, D07, D23 | Follow a case from expectation to outcome to decision. | Habitual renewal, activity as justification, or a 12-month goal called failed in month one. |
| AUTO-IS-08 | Both passes and failures reach the cockpit with criteria, evidence, and a recommendation; Rex decides. | D11, D12, D21 | Trace a pass, a failure, an inconclusive case. | A pass auto-scales spend, or a failure closes as strategic without review. |
| AUTO-IS-09 | Routine decisions wait for the cockpit; material-loss risk gets a timely, prepared alert. | D02, D12 | Sample routine/urgent events and delivery evidence. | Low-value alerts pile up, a threat waits overnight, or a sent alert proves action taken. |
| AUTO-IS-10 | Cockpit shows all necessary work; the one-hour target stays aspirational; patterns or a request trigger adjustment. | D13, D20 | Show an 80-minute scenario in full; track actual time and unfinished-decision age. | Work hidden to hit 60 minutes, effort shifted elsewhere, or a dial-back request ignored. |
| AUTO-IS-11 | Automation moves fast within failure allowances, detecting and recovering before limits are exceeded. | D09, D10, D26 | Drill the nonessential-page case; stop a worker mid-action and verify clean resumption. | Recovery needs Rex within-window, incident exceeds an hour, or an allowance is assumed elsewhere. |
| AUTO-IS-12 | Each initiative observes, orients, decides, and acts with checked feedback; a fresh worker gets context without rewriting goals. | D02, D03, D14, D17, D26 | Follow a real signal to the next worker's context; include a contradictory result. | Rex repeats a decision, a proposal becomes policy, or job runs pass as learning without evidence. |
| AUTO-IS-13 | Every boundary has a clear purpose; a blocked path gets exposed and a resolution proposed. | D03, D09 | Pair each rule with a case it should stop and one it should allow. | Silent abandonment, vague risk invoking new gates, or friction removal bypassing a real boundary. |
| AUTO-IS-14 | Each operating model suits its stage; the capability profile reflects real use and learning without unnecessary gates. | D15, D16, D27 | Trace a practice to changed work; compare claimed maturity with real use. | Scores rise from documents alone, or maturity is treated as proof of a viable initiative. |
| AUTO-IS-15 | The portfolio hits outcomes with the least justified total effort and infrastructure complexity. | D18, D19, D27, D29 | Compare a flow and its dependencies with a simpler alternative across outcomes and cost. | Extra review changes no decision, or simplification drops required coverage. |
| AUTO-IS-16 | Monthly, Rex receives and conducts a formal review of whether each Ideal State is still justified. | D25 (+D02, D14, D24) | Reconcile the record with the inventory; trace a challenged assumption. | Review lists only tasks/spend, omits an initiative, or a proposal becomes direction without Rex's decision. |


## How the later audit should use this artifact

The [12 September operating harness gap analysis](operating-harness-gap-analysis-2026-09-12.md) applies this method. Attainment findings live there; this document remains the target and accepted-direction record.

Proposed method, not a completed assessment. For every initiative and flow: identify intended outcome, primary criterion, entrypoint, authority, dependencies, resources, and observed result, including missing capabilities and duplicate responsibilities.

Keep three judgments separate: **purpose** (worth pursuing), **execution** (delivers reliably within its limits), and **net contribution** (benefits justify total cost versus alternatives); yielding findings like worthwhile-and-effective, worthwhile-but-broken, well-executed-but-low-value, harmful, missing, or unproven, each linked to dated evidence rather than a percentage that hides gaps.

Recommendations (repair, simplify, combine, retire, add, gather evidence) state the outcome and gap first, then benefit, horizon, resources, authority, test, and stopping rule. **Recommendations are not automatic permission to change live systems.**

## Evidence and revision rules

- Separate owner decisions, agent proposals, user reports, and observed outcomes; use explicit attainment states (not assessed, not measured, partial, supported within scope, contradicted) with date, source, and limits.
- Missing evidence stays unknown; repeated AI summaries aren't independent corroboration.
- Preserve failed checks and superseded decisions with sources; reopen stale or contradicted claims; never weaken a criterion to look successful.
- Record Rex's explicit decisions without re-approval; inferred strategic changes remain proposals.
- Canonical home, remaining budgets/materiality thresholds, and next review date are still unknown; that gap doesn't block a read-only audit from proceeding against this Ideal State.

## Open decisions before operational rollout

Guides a read-only audit before these are resolved; missing values must not become invented execution authority: complete initiative list including shared services; initial cash/token/capacity/time allocations; material-loss thresholds and urgency rules; concrete major-decision boundaries per flow; eligible nonessential pages and repeat-incident tolerance; other acceptable failure durations/losses; monthly review date/format/accounting conventions; morning-review baseline and observation window; canonical home and evidence ownership; framework/capability targets and rubric calibration; and a concrete cockpit build plan and target infrastructure shape for D28/D29.

## Framework sources and provenance

- [Miessler: Articulation of Ideal State](https://danielmiessler.com/blog/ai-ideal-state-articulation), [ISA system](https://docs.ourlifeos.ai/ISA__ISASystem)/[format](https://docs.ourlifeos.ai/ISA__ISAFormat): source of this artifact's method; one evolving artifact, disprovable claims, retained unknowns, no minimum criterion count.
- [Amazon two-pizza teams](https://aws.amazon.com/executive-insights/content/amazon-two-pizza-team/)/[Frugality](https://www.aboutamazon.com/about-us/leadership-principles): small teams, clear ownership, extended to total system overhead as Rex's direction, not a literal agent cap.
- [Attain Prep](https://app.notion.com/p/3d645d882ebb81db9d50e35698091788), [Ultimate Portugal](https://app.notion.com/p/3d645d882ebb81fc951fd6b4918dd767), [SwimTrack](https://app.notion.com/p/3d745d882ebb8162be7fe53e04539ce9) Ideal States; [shared automation assessment](automation-ops-2026-09-05.md), [scheduled-loop contracts](contracts/README.md), [Loom assessment](loom-ideal-state-assessment-2026-09-09.md): prior conventions and inventory evidence this follows, historical not current.
- [CMMI levels](https://dev.cmmiinstitute.com/learning/appraisals/levels): grounds the maturity scale's shape; ours is custom, not a CMMI appraisal.
- [AWS product management](https://aws.amazon.com/executive-insights/content/product-management-at-amazon/), [Apple's org for innovation](https://www.apple.com/jobs/pdf/HBR_How_Apple_Is_Organized_For_Innovation-4.pdf), [Amazon's 2016 letter](https://www.aboutamazon.com/news/company-news/2016-letter-to-shareholders): judgment over process-as-proxy.
- [Minto](https://www.barbaraminto.com/concept), [Juran on Pareto](https://www.juran.com/blog/a-guide-to-the-pareto-principle-80-20-rule-pareto-analysis/), [Great Mental Models](https://fs.blog/tgmm/) ([reference](https://fs.blog/mental-models/)), [Sivers' Anything You Want](https://sive.rs/a): the thinking disciplines above.
- [USMC MCDP 6](https://www.marines.mil/Portals/1/Publications/MCDP%206.pdf?ver=2019-07-18-093633-990) ch.2, [Combat Orders Foundations](https://www.trngcmd.marines.mil/Portals/207/Docs/TBS/B2B0287XQ-DM%20Combat%20Orders%20Foundations.pdf) pp.9-10, [MAGTF Supply Operations](https://www.marines.mil/Portals/1/Publications/MCTP%203-40H.pdf?ver=2017-03-23-102358-587) ch.1: OODA/OSMEAC/task-organization sources, our adaptation, not doctrine.

## Revision history

All dated 12 September 2026. None verifies deployment or changes live authority. Full v0.1-v0.5 detail, including the seven scenario dialogues behind D18-D27, lives in the archived [v0.5](history/portfolio-operating-model-v0.5.md).

- **v0.1:** D01-D14 and initial criteria for authority, funding, experiments, attention, learning.
- **v0.2:** D15, the maturity profile, AUTO-IS-14.
- **v0.3:** D16; six responsibilities and thinking disciplines.
- **v0.4:** D17; OODA central via AUTO-IS-12, bounded MAGTF/OSMEAC.
- **v0.5:** D18-D27 from seven scenario clarifications (short checks, the aspirational hour, repair-vs-experiment, time-value allocation, direction challenges, monthly reviews, interruption recovery, adequate maturity).
- **v0.6:** renamed to Portfolio Operating Model; compacted for length while preserving every D01-D27 decision and AUTO-IS-01..16 criterion. Added D28 (cockpit as an authenticated website connected to core infrastructure) and D29 (simplifying Notion/Telegram/runners/Cloudflare workers around outcomes); both confirmed owner directions; implementation details remain open, with no vendor mandate or live-change authorization. Moved scenario dialogue and verbose sourcing to the archived v0.5.
