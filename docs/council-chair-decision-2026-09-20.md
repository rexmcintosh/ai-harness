# Council chair decision: a new chair for the code-review panel

**Date:** 2026-09-20. **Decided by:** the owner. **Tracks:** backlog item
`2026-09-12-council-chair-model-decision`.

## The decision

The chair is the model that reads the panel's answers and writes the verdict. On a pull
request it also decides which findings block the merge.

| | |
|---|---|
| **Changed** | The chair of the `code-review` panel is now `openai-gpt-56-sol`. |
| **Unchanged** | Every other panel (`decision`, `brainstorm`, `red-team`, `spec-review`) keeps the global chair, `claude-opus-4-8`. The global setting `[settings] chair_model` is untouched. |
| **Why** | In the chair bake-off of 2026-09-12 the new chair confirmed the one known real defect in 3 runs out of 3. The old chair confirmed it in 0 runs out of 3. |
| **How** | One line in `council/panels.toml`: `chair_model = "openai-gpt-56-sol"` under `[panels.code-review]`. A panel can now name its own chair. A panel that names none uses the global chair. |

## The evidence

The bake-off made 36 paid chair calls: 4 chair models, 3 saved pull requests, 3 repeats
each. All of them ran on the `code-review` panel. Two of the pull requests were clean: the
panel raised alarms that the code itself refutes. One (`baw-pr11`) had two real defects
that shipped and were fixed later the same day.

| Question | `claude-opus-4-8` (old chair) | `openai-gpt-56-sol` (new chair) |
|---|---|---|
| Clean pull requests: runs with a false block | 0 of 6 | 0 of 6 |
| `baw-pr11`: runs that confirmed the comment-ownership defect | 0 of 3 | 3 of 3 |
| `baw-pr11`: runs where the merge gate blocks, replayed offline | 0 of 3 | 3 of 3 |
| `baw-pr11`: runs that confirmed the second defect (retry handling) | 0 of 3 | 0 of 3 |
| Answers that parsed as valid JSON | 9 of 9 | 9 of 9 |
| Median time per answer | 12 s | 19 s |
| Slowest answer (the 45 KB case) | 16 s | 90 s |

Sources: `docs/chair-bakeoff-results-2026-09-12.md` (the scores and the quoted answers),
`docs/evidence/chair-bakeoff-2026-09-12.json` (every raw answer), and
`tests/test_gate_policy_replay.py`, which feeds the saved answers through the merge gate
with no network call. The third row depends on the gate rule of 2026-09-18
(`docs/council-gate-policy-2026-09-18.md`): a confident `high` finding on developer tooling
can block once the chair confirms it. That rule made a confirmed finding able to block.
This decision supplies a chair that confirms it.

No chair found the retry defect. This change does not fix that.

## The scope, and why it is narrow

The swap covers the `code-review` panel only, because that is the only panel the bake-off
tested. It applies wherever that panel runs:

- `council review` on code, and `council ask` whenever it runs the `code-review` panel
- `council compare` (its default panel is `code-review`)
- the backlog runner's review step
- the code part of a pull request review (`run_pr_review`). The docs part of the same pull
  request runs on the `spec-review` panel and keeps the global chair.

`council sweep` uses the `red-team` panel by default, so it keeps the old chair.

## What was not tested

- **Other panels.** `decision`, `brainstorm`, `red-team` and `spec-review` were never run
  with the new chair. That is why they keep the old one.
- **Large inputs.** The largest chair prompt in the bake-off was 45 KB, about 12,600
  tokens. The largest chair prompt on record is 125,886 tokens, ten times that. Nobody
  tested either chair at that size. The new chair was already the slowest candidate on the
  45 KB case (72 to 90 seconds, against a 180 second timeout).
- **The retry defect.** See above. The best score any chair reached on `baw-pr11` was 1
  defect of 2.
- **Effect size.** Three pull requests and three repeats can detect a difference. They
  cannot measure how big it is.
- **Live use.** Every number here comes from saved panels replayed to the chair. No live
  review has run with the new chair yet.

## Independence caveat

The new chair is an OpenAI model. One seat on the `code-review` panel, the Eng Manager,
is also an OpenAI model (`openai-gpt-53-codex`). On `baw-pr11` the finding the new chair
confirmed was the Eng Manager's finding. The bake-off cannot tell "this chair judges
better" apart from "this chair agrees with its own family".

Two facts argue against the second reading, and neither is proof. The new chair dropped
the Eng Manager's other findings on the same pull request as not blocking. On the two
clean pull requests it refuted confident claims instead of deferring to any seat. The
cheap way to settle it is one more graded case where the right answer comes from a seat
that is not an OpenAI model. That test has not been run.

## Cost

Live Venice prices on 2026-09-20, from `venice-billing models`, in DIEM per million tokens:

| Model | Input | Output |
|---|--:|--:|
| `openai-gpt-56-sol` | 5 | 25 |
| `claude-opus-4-8` | 6 | 30 |

The repository's own pricing tables were not used for these figures.

The new chair costs about 17% less per token. The real saving is smaller. In the bake-off
the two models were sent the same 9 prompts. The new chair was billed fewer input tokens
(68,511 against 107,877) and wrote more output tokens (18,886 against 7,019). At the live
prices above, those 9 answers cost about 0.81 DIEM with the new chair and 0.86 DIEM with
the old one: about 5% less. Prompt caching is not counted in either figure.

The bake-off document estimated a much larger saving. It used the price of 2026-09-12
(2.5 and 12.5). The live price is now double that. The bake-off design notes record a third
price, 6.25 and 37.5, on 2026-09-03. This price moves often, so treat cost as roughly
equal. The decision rests on review quality.

## How to make it global, or roll it back

- **Make it global:** in `council/panels.toml`, set `[settings] chair_model =
  "openai-gpt-56-sol"`. Every panel then uses it. Do this only after the other panels have
  been tested.
- **Roll it back:** delete the `chair_model` line under `[panels.code-review]`. The panel
  returns to the global chair.

## Rollout

Merging this change does not change any running review by itself.

- **The installed `council` and `backlog-run` commands** change only after
  `pipx install --force ~/projects/ai-harness`. Until then they keep the old chair.
- **The pull request check in other repositories** installs Council at a pinned commit.
  Each repository keeps that commit, and the old chair, until someone deliberately moves
  its pin. The per-repository review script needs no edit: it still passes the global
  chair, and Council now picks the panel's own chair on its side.
- **A local override file** at `~/.config/council/panels.toml` replaces the shipped file
  completely. A machine that has one keeps whatever chair that file names.

## What keeps this honest

- `tests/test_config.py`: the override loads; an empty or non-text value counts as not set;
  the shipped file gives `code-review` the new chair and every other panel the old one.
- `tests/test_cli.py`, `tests/test_compare.py`, `tests/test_sweep.py`,
  `tests/test_backlogrun.py`, `tests/test_review.py`: each place that picks a chair asks
  the panel's own chair, and the global one when the panel names none. A failure of the
  panel's own chair on a pull request still fails closed.
- `tests/test_venice.py`: every shipped chair is still asked for strict JSON output, the
  mode the bake-off measured.
- `tests/test_token_budget.py`: the chair's output limit (8,000 tokens, 12,000 at `deep`
  rigor) is inside the new chair's own limit of 128,000. That limit was read from the
  Venice catalogue on 2026-09-11 and was not read again for this change.
- `tests/venice_usage/test_refresh_prices.py`: the price refresher counts a panel's own
  chair as a configured model, so its calls are priced.
