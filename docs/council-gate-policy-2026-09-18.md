# Council gate policy: confident highs on developer tooling

**Date:** 2026-09-18. **Status:** proposed on branch `claude/bl-council-reduced-tier-policy`.
Merging this branch is the owner's decision to adopt option (b) below. Nothing here changes
the installed `council` CLI, the chair model, a CI pin, or any fleet workflow.

## Decision for the owner

| | |
|---|---|
| **Policy in this branch** | On a developer-tooling-only change (the `reduced` tier), a panel finding rated `high` with confidence 8 or more is now *eligible* to block. The chair must still confirm it against the code. |
| **Unchanged** | `critical` on the reduced tier still needs confidence 8. The full tier is untouched. A hedged finding (confidence 7 or less) can never block on the reduced tier. A chair that confirms nothing blocks nothing. Outage handling stays fail-closed. |
| **Why** | The one known-real defect in the regression set (`baw-pr11`, comment ownership, high c9) sits on a tooling-only diff. A chair confirmed it 3 times out of 3 and the gate still returned zero, because no panel finding was `critical`. |
| **Cost and risk** | One comparison in `council/gate.py`. Replayed over all 36 saved bake-off reviews, it adds no block to any clean fixture. The evidence is narrow: three fixtures, one real case. |
| **Not decided here** | Per-finding provenance (below) and the chair model. Both stay separate owner decisions. |
| **To reject** | Do not merge. `backlog-run drop 2026-09-12-council-reduced-tier-policy` deletes the branch. |

## What changes, measured offline

`tests/test_gate_policy_replay.py` re-runs `decide_blocking` over the 36 paid chair answers
saved in `docs/evidence/chair-bakeoff-2026-09-12.json`, against the panels saved in
`tools/regress/panels/`. No network call is made.

| Fixture | Tier | Expected blocks | Chair | Runs | Blocking runs, old bar | Blocking runs, new bar |
|---|---|---|---|---|---|---|
| `aris-pr1` (clean) | full | 0 | all four | 12 | 0 | 0 |
| `stw-pr11` (Node-compat false alarm) | reduced | 0 | all four | 12 | 0 | 0 |
| `baw-pr11` (real ownership defect) | reduced | 2 | `openai-gpt-56-sol` | 3 | 0 | 3 |
| `baw-pr11` | reduced | 2 | `claude-sonnet-5` | 3 | 0 | 1 |
| `baw-pr11` | reduced | 2 | `claude-opus-4-8` (incumbent) | 3 | 0 | 0 |
| `baw-pr11` | reduced | 2 | `z-ai-glm-5-3` | 3 | 0 | 0 (all three runs errored) |

Two readings matter:

1. `stw-pr11` is the case the reduced tier was created for (audit F6, the Node-compat
   block that failed a pull request four times). Its two highs (c9 and c8) are eligible
   under the new bar, and every saved chair still refuted them. Grounding, not the tier,
   is what keeps that false alarm out.
2. With the incumbent chair the real case still passes, because that chair confirmed
   nothing. This policy makes a confirmed high *able* to block. Whether the chair confirms
   it is the separate chair-model decision (`2026-09-12-council-chair-model-decision`).

## Tests that pin the policy

- `tests/test_gate.py`: the reduced-tier bar (critical c8 yes, high c8 and c9 yes, high c7
  no, critical c5 no); a confirmed high c9 blocks; a refuted high c9 does not; a high c7 or
  critical c7 cannot block even when the chair lists it.
- `tests/test_gate.py`, two `test_known_gap_*` tests: they pin today's behaviour for the
  provenance gap below (an unrelated chair block counts once any eligible finding exists,
  on both tiers), so it cannot drift silently. The provenance change should flip them.
- `tests/test_review.py`: the same three outcomes end to end through `run_pr_review` on a
  `tools/` diff.
- `tests/test_acceptance_audit.py`: unchanged and green. The historical PR #11 and `ROOT`
  false positives still pass; the PR #9 true positive still blocks.
- `tests/test_gate_policy_replay.py`: the table above.

Each new test was run against the old bar first and failed for the expected reason.

## Separate gap: confirmed blocks are not tied to panel findings

This branch does **not** close it. Today `decide_blocking` asks one question, "did the
panel raise at least one eligible finding?", and then counts every block the chair lists.
A chair can confirm an unrelated issue and it counts. `ConfirmedBlock` carries `point`,
`severity` and `why`, and no reference to a panel finding. Widening the reduced bar makes
the "at least one eligible finding" condition true more often, so this gap matters slightly
more after this change than before it. That is the honest cost of taking (b) first.

### Concrete plan (own branch, after an owner yes)

1. **Stable ids.** `_panel_digest` in `council/synthesize.py` numbers every finding it shows
   the chair: `F<seat index>.<finding index>`, for example `F3.1`. Ids are derived from
   panel order, not from prose, so the same saved panel always yields the same ids. Add an
   `id` field to `Finding`, filled at digest time.
2. **Chair contract.** `REVIEW_SYNTH_OUTPUT` asks for `"sources": ["F3.1", ...]` on each
   `blocking_findings` entry, with one rule: list the panel findings this block confirms.
   `ConfirmedBlock` gains `sources: list[str]`.
3. **Gate.** `decide_blocking` counts a block only when at least one of its sources is an
   id of an eligible candidate at the current tier. Unknown ids, ids of ineligible
   findings, and duplicates add nothing. Two blocks that cite the same single source count
   once.
4. **Missing sources are explicit, never silent.** A block with no `sources` field comes
   from an old saved answer or a chair that ignored the contract. Proposed rule: it does not
   count as a block, and the review is reported as *unavailable* (the existing fail-closed
   path) when the panel did raise an eligible candidate. A real defect then cannot slip
   through as a pass because of a formatting miss. This rule is the part that needs the
   owner's yes, because on a non-compliant chair it fails checks closed.
5. **Old evidence.** Saved bake-off answers have no `sources`. The replay test keeps a
   legacy reader for them; the live path does not.
6. **Tests first:** unrelated source id, missing sources, invalid id, duplicate ids, a
   source that names an ineligible finding, and outage behaviour unchanged.
7. **Compliance check before any pin moves.** Whether each chair model actually returns
   usable `sources` can only be measured with paid runs. That needs its own capped budget
   and a fresh balance check; it is not part of this plan's offline work.

Size: about 60 lines of code and 120 lines of tests. No fixture is rewritten.

### What the owner chooses

- **Stage it (default in this branch):** merge (b) now, run the provenance plan as its own
  item. Risk accepted: an unrelated chair block can count when any eligible panel finding
  exists, as it already can today on the full tier.
- **Bundle it:** hold this branch until provenance lands on it too.
- **Neither:** drop the branch; the reduced tier stays critical-only.

## Rollout, if merged

Merging changes the repository only. The installed CLI changes on the next
`pipx install --force ~/projects/ai-harness`. CI in other repositories keeps its pinned
Council commit until a pin is deliberately moved; a merged local gate is not proof that
any remote workflow uses it.
