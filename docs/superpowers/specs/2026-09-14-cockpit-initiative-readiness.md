# Initiative readiness and operating strength

Approved by Rex in the September 14 cockpit conversation: build an initiative view
showing readiness, operating strength, Ideal State progress, and linked work.
Start with Attain Prep. Existing project direction and execution authority remain
owned by their existing sources.

## Experience

Place Initiatives before All work. A compact initiative selector opens a focused
view. Show purpose, assessment date, scope, and the next useful step. A project
can contain several workstreams, each with its own pilot/use/operation stages.
Stage segments represent concrete requirements, with visible state labels and
counts of verified checks. Selecting a stage reveals requirements, evidence,
dependencies, and work. Missing capabilities remain visible with no queued task.

Operating strength uses Missing, Defined, Used, Dependable, Improving, with
Unknown separate. Show the recorded level and a stage target, marking proposed
targets explicitly. Cover the six existing operating responsibilities and OODA.
Ideal State criteria show outcome evidence independently of implementation.
Do not calculate an overall percentage or infer outcomes from completed tasks.

Reuse the cockpit's navy/blue palette and Aptos/Segoe UI typography. Reserve
green for verified checks, amber for work remaining, red for explicit blockers,
and a patterned neutral segment for unknown evidence. Use a horizontal stage
map on desktop and a vertical arrangement on small screens. All interactions
work with native buttons, selects, and disclosures and visible keyboard focus.

## Ownership and evidence

`cockpit/readiness.py` owns validation and presentation derivation. Private
prepared assessments live at `<projects>/.cockpit/initiative-profiles.json`,
outside this public source repository and package. The source work queues retain
all task state. No task or external page is written by assessment reads.

Version 1 contains profiles keyed by catalog initiative ID. Each profile names
its assessment date, review date, scope, summary, sources, workstreams/stages,
requirements, capabilities, outcomes, and exact work links. Each claim has source
IDs and a note explaining evidence limits. Local Markdown source identities bind
to a SHA-256; changed or missing evidence downgrades dependent claims to Unknown.
External references show when they were inspected and are not refetched on a
page refresh. A past review date downgrades all claims pending reassessment.

Malformed profiles fail individually and visibly. Empty stages never appear
ready. Missing evidence differs from a demonstrated missing capability. Critical
gaps remain explicit. Dependencies must exist and form an acyclic graph. A check
cannot count as verified while its prerequisites remain unverified.

Work links use source, exact ID, and source revision. Changed, missing, duplicate,
or foreign-initiative records do not inherit a prepared mapping. Completed work
stays a recorded result and never changes readiness or outcome states. Unmapped
active work remains accessible in the initiative and All work.

An authenticated source route resolves only configured local Markdown sources
within the projects root. It displays escaped text and never accepts an arbitrary
path. The existing snapshot includes assessments in its review fingerprint.

## Delivery and checks

Implement on `claude/cockpit-readiness-20260914`. Verify absent/invalid/stale
profiles, unsupported claims, dependencies, exact work links, unchanged source
files, authenticated source access, and stale brief acknowledgements. Browser
checks exercise initiative/stage navigation, evidence, grouped work, keyboard
interaction, and desktop/mobile layout, plus the existing cockpit regression.
Prepare a private Attain assessment using inspected sources, retaining draft
status and historical limits. Review the code through the existing Venice Council
workflow. Production merge and release require the owner's normal approval.
