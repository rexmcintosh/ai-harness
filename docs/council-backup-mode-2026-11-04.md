# Council backup mode: decisions, flags and measurements

Date: 2026-11-04. How to use it: `council/README.md`, section "Backup mode". This record holds
what was decided, what was checked live and why the flags look the way they do.

## Owner decisions (Rex, 2026-11-04)

- A backup review counts as a passed council review for merges, provided the report shows a
  BACKUP MODE banner.
- Backup replaces real-money spending. When the free daily credits (DIEM) are gone, use the backup
  rather than let Venice fall through to USD.
- No new vendor or API key.

## Decisions made while building, for the owner to check

- **`council sweep` refuses in every mode, including `off`.** The brief says a sweep exits with the
  reset time when credits are out, and also that every script gets `COUNCIL_BACKUP=off`. If `off`
  let a sweep through, `security-sweep.sh` would pay USD for up to 40 files times 4 seats when its
  schedule comes back. So no setting makes a sweep pay on purpose. The cost is waiting for the reset.
- **An unreadable balance stops a sweep but not a review.** The owner's rule is that a failed check
  stays on Venice, and `ask`, `review` and `compare` follow it. A sweep has no backup and is dozens of
  panel runs, so it does not start, and it stops if a later read fails. A review on a key with a USD
  limit can still pay while the balance cannot be read: reviewers on this branch asked to reverse
  that, and it stays because it is the owner's explicit rule.
- **Half of the seats down also exits 3**, not only all of them. It is the bar `run_pr_review` already
  fails closed on, and a review that counts for merges should not clear a lower one.
- **A backup code review is one model family today.** The owner's map sends OpenAI, DeepSeek and Grok
  to Codex, and those are all of `code-review`'s seats, and its chair goes to Codex too. The personas
  differ and the models do not. The banner says so ("This run used 1: OpenAI"). Other panels use both
  families. Sending one seat to Claude is a one-route change in `panels.toml`, and it is the owner's
  call.

## Why these flags

Everything below was read from `claude --help` (2.1.317) and `codex exec --help` (0.166.0) and then
checked with a live call. A child has no tools, MCP servers, hooks, plugins, project or memory
files, and no saved session.

### Claude

    claude -p --output-format stream-json --verbose --model M --effort E --tools ""
      --strict-mcp-config --setting-sources "" --safe-mode --disable-slash-commands
      --no-session-persistence --permission-mode dontAsk
      --settings '{"enabledPlugins":{...four built-ins: false}}' --system-prompt-file F

- `--bare` looks right and cannot be used: it reads `ANTHROPIC_API_KEY` only, never the plan login.
- `--safe-mode` removes CLAUDE.md, skills, hooks, MCP and commands, and keeps the plan login.
- It leaves four built-in plugins listed. Only the `enabledPlugins` setting removes them.
- `claude auth status` runs before the first model call of a run, with the child's own environment.
  It is local and free. Anything but `loggedIn: true` and `authMethod: claude.ai` means Claude is not
  used, because a reply can only report who it billed after the charge.
- `stream-json` makes the first event a full statement of the setup. Each reply is checked:
  `tools`, `mcp_servers`, `plugins` and `skills` must be present and empty, and `apiKeySource` must be
  `none`, or the reply is refused and no further Claude child starts in that run. The same stop applies
  after a crash or a timeout, which leave the setup and the billing unknown. An ordinary failed reply
  (overloaded, say) still reports a clean setup first, so it costs only that seat.
- Paid overage only shows in a `rate_limit_event` after a call, so the first overage call cannot be
  prevented from here. The first Claude call of a run goes alone so its reply can stop the rest, and
  packaged panels never run two Claude calls at once (one seat at most, then the chair). A custom
  panel or a second council process can still overlap. The hard protection is the account: on
  2026-11-04 it reports `overageStatus: rejected`, so no overage can be charged today. Keep it off.
- No project folder appears in `~/.claude/projects` and no entry in `~/.claude.json`.

### Codex

The obvious flags are not enough. `--ignore-user-config` skips only `config.toml`, and `--disable
shell_tool` and friends still leave GPT-6 models with these, as `codex debug prompt-input` shows:

- `exec` (JavaScript), `wait`, `request_user_input`, and six sub-agent tools. The model catalog forces
  them (`tool_mode: code_mode_only`, `multi_agent_version: v2`), whatever the features say.
- `~/.codex/AGENTS.md` and the skills list, because they load from `CODEX_HOME`.

So a child gets three more things:

- A private `CODEX_HOME` holding only symlinks to `auth.json` and `installation_id`.
- A one-model catalog with the tool-granting fields removed (`model_catalog_json`).
- `model_instructions_file`, which also replaces Codex's long base prompt.

Input tokens for a one-line prompt: 9,262 with the feature flags only, 4,367 from the real home, 138
with all of this. That is the plan limit protected as well as the isolation.

- **The login symlink is safe.** With fake tokens and a local fake refresh server, a token refresh
  wrote through the symlink to the target and left the link in place. If a later Codex replaces the
  link instead, the client moves that file back before removing the temp folder, because the old
  refresh token is already spent. If that move fails, the temp folder stays, its path goes to stderr
  and no further Codex call runs.
- **One child refreshes at a time.** Seats start together. If the login is near the age Codex
  refreshes it, the first child runs alone behind a lock file, so the others see a fresh token.
  `codex debug models` refreshes a stale login too (checked with fake tokens), so it goes through the
  same gate and normally does the refresh itself. The lock file sits beside the real `auth.json`
  (`.council-refresh.lock`), so it follows the login. It covers council processes, not Codex sessions
  started elsewhere, which have the same exposure to each other today. An unusable lock fails the seat
  when a refresh is due.
- **Before the first call on each model, in each run**, `codex debug prompt-input` shows what the model
  would see. It is local and free. Any tool or extra context means Codex is not used in that run.
- **During each call**, any reply item other than `agent_message`, `reasoning` or `error` is refused.
- **An API key in `auth.json` is refused**, because it would bill the API.

### Environment

A child gets `PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE` and nothing else (plus `CODEX_HOME`, pointing
at the private one). `PATH` gets the tool's folder first, because `codex` is a node shim. Tested with
`env -i`.

### Prompts

The user text goes on stdin. The system text goes in a file in a private temp folder (mode 0700),
next to the empty working folder. Nothing from a prompt reaches argv.

## Model choice

All eight names were accepted by one tiny live call each on 2026-11-04: `gpt-6.1-sol`, `gpt-6-astra`,
`gpt-6.1-terra`, `gpt-6-luna`, `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-fable-5-1`,
`claude-haiku-5-5`.

| Role | Maps to | Why |
|---|---|---|
| Seats | `gpt-6.1-terra` / `claude-sonnet-5-5`, medium effort | Mid-tier, to protect plan limits |
| Code-review chair | `gpt-6-astra`, high | Strongest Codex tier |
| Other chairs | `claude-fable-5-1`, high | Strongest Claude tier. List price is 2.5x Opus, 5x Sonnet, on the same small call |
| Router | `gpt-6-luna`, low | Only picks a panel |

Tier order is the Delegate plugin's for Codex (`luna < terra < sol < astra`) and list price for Claude.

## Measurements

Real runs on 2026-11-04. Times are read from a once-a-second process sample, so they are approximate.

| Run | Input | Seats | Chair | Total | Peak memory of children |
|---|---|---|---|---|---|
| `code-review`: 3 Codex seats, Codex chair | 142 KB diff | about 10 s each | about 30 s (`gpt-6-astra`, high) | 51 s | 402 MB |
| `red-team`: 3 Codex and 1 Claude seat, Claude chair | 162 KB diff | about 15 s each | about 110 s (`claude-fable-5-1`, high) | 143 s | 621 MB |
| `red-team` | 1 KB diff | under 20 s | under 15 s | 33 s | about 860 MB, with unrelated processes in the count |

Timeouts follow from that, with room for the 200 KB input cap: seats 300 s, chairs 600 s, router 60 s.
`max_concurrent` is 4, the size of the largest panel. Tests showed no need for a lower limit: four
children ran cleanly with nothing left behind, and each tool ran isolated from the others' state.

## Live proof, 2026-11-04 about 20:50 UTC, from this branch's code

Each row is a real run. DIEM was 0 and `auto` switched on its own unless stated.

| Run | Trigger | Tools | Result |
|---|---|---|---|
| `review --panel red-team` on a 1 KB diff | `diem` | Codex x3, Claude x2 | exit 0, 33 s |
| `venice-delegate council-review` with `council` on `PATH` pointing at this branch | `diem` | Codex x4 | exit 0, 17 s |
| `ask` with the router | `diem` | Codex x4, Claude x1 | exit 0, 18 s |
| `review` with the floor removed and a USD=0 key | real `402` | Codex x4 | exit 0, 17 s |
| Canary, both tools (`tests/test_council_backup_live.py`) | none | both | no file read, no listing, no shell, no memory text |

After each run: no child process left, no `council-backup-*` temp folder left. The required review
of this branch is recorded with the merge recommendation.

## Review points weighed and not changed

The review rounds on this branch also raised these. They stay as they are, for these reasons.

- **`auto` stays on Venice when the balance cannot be read.** It is the owner's explicit rule.
- **One-family backup code review, and a backup review counting for merges.** Both are owner
  decisions. The banner states the family count, and the first section above puts the map to the owner.
- **Seats are told apart by model and persona, and the chair and router by model.** Two seats with the
  same model and persona share one row, and a router sharing a seat's model can show the seat's route.
  No packaged panel does either.
- **A login that cannot be saved stops only this run's Codex calls.** That takes a future Codex that
  replaces the link, then a failed save. Another council process would not know. Today's Codex writes
  through the link, so none of this path runs.
- **Force mode loads no Venice key, and the scrub still covers it.** `get_api_key` reads only
  `VENICE_COUNCIL_KEY` and `VENICE_API_KEY` from the environment, and the scrub takes every variable
  named like a secret. A test holds this.
- **A timeout or shutdown kills Codex with SIGKILL, which could land mid-write on the login.** The write
  takes microseconds about once a week, and any crash of Codex carries the same risk today. The fix
  would be `codex login`.
- **A sweep call already past the guard when another call gets a 402 still runs.** It counts as in
  flight. No new call starts after the 402 is seen, and a test holds that boundary.
- **A token that crosses the refresh age during a run can bypass the lock.** The gate starts a day
  before Codex's refresh age, and no call runs for anywhere near a day.
- **An operating-system sandbox, pinned tool paths, and per-run consent to use another vendor.** Not
  in this build. This machine cannot run a sandbox for Codex, the tools are the ones the owner already
  runs by hand, and consent per run would defeat an automatic backup.

## Limits and risks

- **Not live-tested: a real plan limit.** The words for it come from the tools' known messages and
  Claude's `rate_limit_event`. If the wording changes, the seat still fails, with the tool's text.
- **The floor can leak a little USD.** A run that starts just above 0.5 DIEM on a key with a USD limit
  can start new calls past 0 for up to 30 seconds before the next balance read, or longer if reads
  fail, and calls in flight finish. One review costs about 0.5 to 1 DIEM, so the worst case is well
  under 1 USD per run. A USD limit of 0 on the key is the only hard stop: then Venice sends the 402.
- **`temperature` and `max_completion_tokens` are not available**, so these runs cost whatever they cost
  in plan terms, and are less repeatable than a temperature-0 run.
- **Tool updates can break this on purpose.** A new built-in plugin, a new Codex tool or a changed
  output format makes the backup refuse, with the reason in the message. That is correct for
  isolation. It could happen on the day it is needed, so run the live proof after an update. Nothing
  runs that proof on a schedule yet.
- **Unattended scripts default to `auto`.** The known ones set `off`. A new one must too.
- **Isolation rests on the tools' flags.** The checks read what the tools report. They are not an
  operating-system sandbox, which this machine cannot run for Codex (no user namespaces).
- **Prompts now also reach Anthropic and OpenAI.** The scrub covers the Venice key and anything in
  the environment named like a secret. A secret in a diff under another name goes along, as it does
  on Venice.
- **Memory.** A red-team run peaks at about 620 MB of children. This machine has no swap and often
  about 2 GB free, so several backup runs at once could get tight.
