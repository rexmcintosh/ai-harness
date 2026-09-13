# Owner's Venice delegation policy

Rex approved these standing routes on 2026-09-13. They take precedence over the
Delegate plugin's provider matrices, subscription tie-breakers, cross-provider
review rules, and dispatch mechanics. Exact model requests still win. Keep the
saved Lean/Balanced/Max profile for work outside these Venice routes and fallbacks.

## Choose the route

| Work | First route | Credential |
|---|---|---|
| Working-tree code review required by the working agreement | Existing Council panel through `venice-delegate council-review --diff` | `VENICE_SECOND_OPINION_KEY` |
| Independent opinion on a plan, analysis, or other completed work | One fresh Venice `deepseek-v4-pro` call | `VENICE_SECOND_OPINION_KEY` |
| Bounded code proposal, test-case design, debugging, or extraction with sufficient supplied context | Venice `qwen3-coder-480b-a35b-instruct-turbo` | `VENICE_CODE_HELPER_KEY` |
| Work requiring repository tools, iterative execution, broad architecture, or high-consequence implementation | Existing Claude/Codex route; use Venice for a separable bounded slice when useful | Existing subscription authentication |

Venice is the API service, not a model family. For a single second opinion, use a
different underlying model family from the author: DeepSeek is the default for
Claude, OpenAI, or Qwen authors; use `qwen3-coder-480b-a35b-instruct-turbo` for a
bounded code review of DeepSeek-authored work. For significant DeepSeek-authored
technical work use the existing multi-family Council panel. State the actual
model in the delegate roster. Never claim a provider switch proves independence.

This is a standing preference, not a requirement to create extra work or a second
review layer. A required Council review already supplies the independent review;
do not add a Claude/Codex review by habit. Tiny tasks can still finish inline.
Normal and voice-critical prose keep their existing Claude routes.

## Dispatch

Read the relevant source locally and prepare one concise, self-contained brief:
task, selected context, constraints, deliverable, and acceptance checks. Give a
coherent batch to each helper. The API helper cannot inspect paths, run tools,
edit files, or create agents. Include the actual needed source, not just paths.
It returns proposed code or findings. The main agent applies changes and runs
tests in the authorized workspace. Use the native Claude/Codex helper when the
task needs an agent with tools. Do not build a new agent harness for a small slice.

```sh
venice-delegate code --file /tmp/task-brief.md > /tmp/proposal.md
venice-delegate review --file /tmp/review-brief.md > /tmp/review.md
venice-delegate council-review --diff --format md > /tmp/council-review.md
```

Inspect the command's exit status and the returned content before using a result.
Council's exit status alone does not establish a clean review: read all seat
failures, conditions, and findings. `--diff` reviews unstaged tracked changes;
include intended new files with `git add -N <paths>` before collecting that diff.
for a committed branch prepare `git diff <base>...HEAD` plus relevant new-file
context, then pipe the curated payload to `venice-delegate council-review -`.
Retain the reviewed commit or diff identity alongside the review evidence.

The default single-helper output ceiling is 8,000 completion tokens including
reasoning, and the request timeout is 180 seconds. Use `--max-completion-tokens`
and `--timeout` to fit the actual slice; output can be 1..32,000 tokens and the
request timeout 1..300 seconds. Qwen Coder does not support reasoning effort, so
report effort as not applicable. DeepSeek uses its API default reasoning effort
(currently low); do not invent a Claude/Codex effort flag. Model IDs were checked
against the live catalogue on 2026-09-13; check availability on model errors.

## Resources and failures

Keys are loaded from their exact environment name or `~/.env`, without shell
evaluation. No automatic swap to a generic, Council, admin, or other role key.
Do not print secrets or put them in briefs, command arguments, or committed files.
Only selected source, tests, and task-relevant technical context may be sent to
`https://api.venice.ai/api/v1/chat/completions` for authorized work. This owner
instruction authorizes those routine helper and review calls without asking again.

Both keys are inference-only and USD=0 as verified on 2026-09-13. Their configured
DIEM ceilings are unset; they share the account balance with other initiatives.
Respect owner budgets and other committed work. Do not change key limits or buy
credits. More calls do not mean more value. Prefer useful accepted work, and spend
up only when the expected improvement justifies it.

Single-helper calls do not retry automatically. On an auth, quota, model, timeout,
truncation, or empty-result failure, state the failure and use the appropriate
existing subscription route. For technical review fallback, preserve the original
opposite-provider rule: a Codex author gets a fresh Claude reviewer (Opus `xhigh`
on Balanced/Max, Sonnet `high` on Lean); a Claude author gets the profile's Codex
technical reviewer. A Venice-native author gets a fresh subscription reviewer
from a different model family. If an explicit project or CI gate requires a
successful Council result, a subscription opinion does not clear that gate;
report the required review as incomplete. One corrected follow-up is reasonable when the
problem is missing context or a specific fix; do not loop on weak proposals.
If fallback is also unavailable, complete independent work and report the exact
remaining gap. Never treat a failed or partial review as approval. Council keeps
its existing bounded retries and panel behavior.

Usage goes to the existing Venice ledger: single calls use project `delegate`
and task `code` or `review`; Council retains its existing ledger attribution.
Separate API keys give Venice-side role attribution. Ledger token costs remain
estimates until reconciled with billing. No new dashboard or drain job is added.
