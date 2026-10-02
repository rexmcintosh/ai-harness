# Output ceilings on every council call — 2026-09-11

## The gap, confirmed

```
$ grep -rn "max_tokens\|max_completion_tokens\|maxTokens" council/ diem/ scripts/ setup/ venice_usage/ tools/ tests/
(no hits)
```

Nothing bounded a completion. A one-line `council ask` and a 200 KB code review
both bought one unbounded synthesis from `claude-opus-4-8`, whose catalogue
ceiling is 128,000 completion tokens at 30 DIEM per million.

## Which field

Venice's own OpenAPI spec, build `20260911.122036`, fetched the day this landed:

- `max_tokens` — "This value is now deprecated in favor of max_completion_tokens."
- `max_completion_tokens` — "An upper bound for the number of tokens that can be
  generated for a completion, **including visible output tokens and reasoning
  tokens**."
- `model_spec.maxCompletionTokens` — "the upper bound for the max_completion_tokens
  request parameter".

`max_completion_tokens` it is. The reasoning half of that definition is the half
that matters: every current council seat reports `supportsReasoning: true`, and
`openai-gpt-53-codex`'s long tail in the ledger is mostly reasoning, not prose —
two of its rows burned five figures of completion tokens to emit a short answer.

A value of 0 or less sends nothing at all, which is Venice's own "use the model
default". That is the operator's kill switch: set `max_completion_tokens = 0` in
`panels.toml` and the caps are off, no code change.

## Sizing, from the ledger and not from a guess

`~/.local/state/venice-usage/ledger.db`, `project IN ('council','backlog-runner')`,
1,732 rows spanning 2026-07-20 to 2026-09-11:

```
model                   role        n    p50    p95    p99   p99.9    max  maxComp
grok-4-3                member    426    945   1381   1741    2077   2187    32000
openai-gpt-53-codex     member    422   3164   9630  13816   16091  17142   128000
deepseek-v4-pro         member    421   1294   4119   5594    7166   7766    32768
claude-opus-4-8         chair     420   1579   2912   3656    4690   5174   128000
openai-gpt-56-sol       member     24   3812   7079   7620    7751   7765   128000
gemini-3-1-pro-preview  member     17   1846   2679   2746    2761   2763    32768
openai-gpt-56-luna      member      1    141    141    141     141    141   128000
claude-opus-4-7         member      1   2513   2513   2513    2513   2513   128000

ALL MEMBER SEATS POOLED   n=1312 p99=10804 p99.9=14549 max=17142
CHAIR                     n=420  p99=3656  p99.9=4690  max=5174
ROUTER                    n=0    (the router only runs when --panel is omitted)
```

Chosen, and what they cost in truncation:

```
member 24000: 1.40x observed max, 1.65x p99.9  -> rows it would have truncated: 0/1312
chair   8000: 1.55x observed max, 1.71x p99.9  -> rows it would have truncated: 0/420
```

The asymmetry is the point. The member seats need 24,000 because one model
(`openai-gpt-53-codex`) reasons eight times longer than another (`grok-4-3`,
max 2,187). The chair is a JSON digest of a short panel summary and has never once
gone past 5,174.

**The direction of error is not symmetric.** A cap set too high wastes nothing —
it is a ceiling, not a reservation. A cap set too low truncates the JSON body,
which fails `loads_lenient`, which is an errored seat; on the CI gate path
`errored * 2 >= len(results)` sets `unavailable`, which fails the merge **closed**.
So the caps are set above the observed maximum, not near the median, and
`test_shipped_defaults_clear_every_output_ever_observed` keeps a future tidy-up
from lowering them without new evidence.

**The router's 2,000 is the one number with no ledger behind it.** `gemini-3-5-flash`
has zero rows, because every logged council invocation named its panel explicitly.
2,000 is ~100x the visible output (`{"panel": "code-review"}`); the slack is for
reasoning tokens, which the cap also bounds. Revisit it once router rows exist.

`deep` rigor gets 30,000 / 12,000. Not 32,000: `grok-4-3`'s catalogue ceiling **is**
32,000 and there is no reason to sit on that edge.
`test_no_shipped_cap_exceeds_its_model_catalogue_ceiling` holds the whole table
against a dated snapshot of `model_spec.maxCompletionTokens`.

## What this actually buys — and what it does not

Worst-case completions for one four-seat code-review round, live DIEM per Mtok
from `GET /models` on 2026-09-11:

```
seat                     out $/Mtok  uncapped     DIEM   capped     DIEM
openai-gpt-53-codex          17.500    128000    2.240    24000    0.420
deepseek-v4-pro               3.301     32768    0.108    24000    0.079
grok-4-3                      2.830     32000    0.091    24000    0.068
claude-opus-4-8              30.000    128000    3.840     8000    0.240
TOTAL                                            6.279             0.807
```

**7.8x tighter worst case, and zero effect on the observed distribution.** That is
the honest claim. The cap is a blast-radius guard.

It is *not* a cut to the typical bill, and should not be sold as one. The most
expensive chair call in the whole ledger:

```
claude-opus-4-8: 125,886 in, 2,808 out
  live catalogue 6/30 -> input 0.7553 + output 0.0842 = 0.8396 DIEM
  output is 10% of it
```

Ninety percent of the chair's money is the prompt. The levers that move the
typical bill are the chair model's price, `byte_cap = 200000`, and the fact that
three pushes to one PR is three full panels — not the output ceiling.

**Cost-shape caveat.** Venice injects Anthropic prompt caching itself and bills
cache writes at a TTL-specific premium (see the `venice-model-pricing` memory
note). On an unbounded call that premium rides on the prompt, which the output cap
does not touch. Nothing here changes it.

## Configuring it

Three independent knobs, each resolved most-specific-first:

```
seat  >  panel+rigor  >  panel  >  settings+rigor  >  settings  >  Settings class default
```

```toml
[settings]
max_completion_tokens        = 24000   # any panel seat
chair_max_completion_tokens  = 8000
router_max_completion_tokens = 2000

[settings.rigor.deep]                  # deep is meant to surface more
max_completion_tokens       = 30000
chair_max_completion_tokens = 12000

[panels.code-review]                   # optional, per panel
max_completion_tokens = 20000
[panels.code-review.rigor.deep]        # optional, per panel per rigor
max_completion_tokens = 28000

[[panels.code-review.members]]         # optional, per seat
name = "Adversary"
model = "grok-4-3"
max_completion_tokens = 6000
```

The member, chair and router ladders are deliberately **not** connected: raising a
panel's seat ceiling must not quietly raise what the chair may spend.

Two deliberate details:

- **`0` is a setting, not an absence.** The resolver tests `is not None`, never
  truthiness, so a `0` written to disable a cap cannot be mistaken for "unset" and
  silently fall back to the parent's ceiling.
- **`--rigor` now buys something beyond rendering.** It previously reached only
  `render()`; it now selects the output budget for the seats and the chair. It
  still does not change models or seat counts — that remains an open audit item.

## Back-compatibility

`VeniceClient` takes a client-level default, so every call the process makes is
bounded even if a call site forgets to pass its own. `run_pr_review` gained an
optional `settings=`; the 18 repos' un-updated `scripts/venice_review.py` copies
call it without one and fall back to the `Settings` class defaults — the same
24,000 / 8,000 — so they are capped from the moment they pick up the new pin,
with no per-repo edit. A `~/.config/council/panels.toml` that predates these keys
loads unchanged and gets the class defaults.

## Verified

Real payloads for one full PR review (code slice + doc slice), printed from the
transport:

```
model                      max_completion_tokens  temperature
openai-gpt-53-codex                        24000            0
deepseek-v4-pro                            24000            0
grok-4-3                                   24000            0
claude-opus-4-8                             8000            0
gemini-3-1-pro-preview                     24000            0
grok-4-3                                   24000            0
openai-gpt-53-codex                        24000            0
deepseek-v4-pro                            24000            0
claude-opus-4-8                             8000            0

calls: 9   uncapped calls: 0
deprecated max_tokens present anywhere: False

--- deep rigor, red-team panel ---      --- kill switch: = 0 ---
grok-4-3                     30000      payload keys:
deepseek-v4-pro              30000        ['messages', 'model',
openai-gpt-53-codex          30000         'response_format', 'temperature']
claude-opus-4-7              30000
claude-opus-4-8              12000
```

883 tests pass. Both evidence guards were mutation-checked: raising the deep cap
to 40,000 fails `test_no_shipped_cap_exceeds_its_model_catalogue_ceiling`, and
lowering the chair cap to 4,000 fails
`test_shipped_defaults_clear_every_output_ever_observed`.

**Not verified: a live Venice call.** The account balance is about 6 DIEM and this
task was explicitly barred from spending it, so the payload shape is checked
against the catalogue and the spec, not against a 200 response. Before trusting
the gate on 18 repos, spend one cheap call — `council ask "say ok" --panel decision`
against a flash model — and confirm no 400. If a model family ever rejects the
field, `max_completion_tokens = 0` in `panels.toml` turns it off without a deploy.
