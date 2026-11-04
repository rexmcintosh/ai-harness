# council

A multi-model Venice AI council. Fan a question/artifact out to a panel of
personas — each backed by a *different* model family — then a "chair" model
synthesizes a recommendation with typed disagreements. Members answer in
parallel, blind to each other, so their agreement is meaningful.

## Install

    pipx install .            # from this repo (recommended)
    # or: pip install -e .
    cp .env.example .env      # add your VENICE_API_KEY

`council ask` / `council review` read `VENICE_API_KEY` from the environment
(or a `.env` you've sourced). `council panels` needs no key.

## Use

    council ask "Postgres or SQLite for a single-user app?"     # router auto-picks → decision
    council ask --panel red-team "Critique this plan" --file plan.md
    council review path/to/file.py
    council review --diff                                       # reviews `git diff`
    council compare --task "make f handle empties" a.py b.py    # rank candidates, pick a winner
    council sweep path/to/repo                                  # repo-wide security sweep
    council panels                                              # list councils + seats

Flags: `--panel NAME` (skip the router), `--rigor daily|deep` (noise gate),
`--format md|term`, `--file PATH` (repeatable; `-` = stdin), `--panels FILE`.

## compare — waste tokens, save time

`council compare` is the *selection* half of "throw N models at it": you generate N
candidate solutions however you like, then the panel ranks them and the chair picks a
winner + says what to graft from the runners-up. The end-to-end loop for a single
self-contained prompt:

    council/scripts/parallel-attempts.sh "Write a retry decorator with backoff"

generates K candidates across K Venice models in parallel and pipes them to `compare`.
For repo-wide code building, generate candidates with `claude` / parallel git worktrees
instead, then `council compare` the results.

## sweep — autonomous security research

`council sweep <path>` walks a whole tree, fans the `red-team` panel across it chunk
by chunk, **dedups** findings, **gates** by confidence (critical always kept), sorts
worst-first, and has the chair write a phone-glanceable summary. It is deliberately
**bounded** — `--max-chunks` caps coverage and the report states how many files were
dropped, so it never silently claims to have scanned everything. It reports; it opens
nothing. `council/scripts/security-sweep.sh` is the scheduled runner (Telegram summary).

## Panels

`code-review` · `decision` · `brainstorm` · `red-team`. Seats are spread across
model families (OpenAI / DeepSeek / xAI / Google / Claude) — different families
disagree differently, and the Adversary is always a non-Claude model for genuine
independence. Edit `council/panels.toml` (or drop a `~/.config/council/panels.toml`
override) to change personas/models or add seats.

**The chair, per panel.** `[settings] chair_model` is the chair for every panel. A panel
can name its own with `chair_model = "..."` under `[panels.<name>]`, and that model then
synthesizes every run of that panel: `ask`, `review`, `compare`, `sweep`, the backlog
runner's review, and that panel's slice of `run_pr_review`. A panel that names none uses
the global chair. Today only `code-review` names one (`openai-gpt-56-sol`). The evidence,
the scope and the rollback are in `docs/council-chair-decision-2026-09-20.md`.

## Rigor (the noise gate)

- `daily` (default): show findings with confidence ≥ 8; demote 5–7 to a
  collapsed section; drop < 5 — **unless** severity is `critical` (always shown).
- `deep` (default for `red-team`): show everything ≥ 2, flagged tentative.

The gate is presentation-only — the chair always sees the full set.

## Merge gate (PR review)

`run_pr_review` (used by `setup/templates/venice_review.py`) splits a PR diff into a
**code** slice (gated) and a **doc** slice (advisory). What blocks a merge is decided
by `council.gate`, not by any single panelist:

- **Chair-arbitrated + grounded.** Only findings the chair lists in `blocking_findings`
  block. The chair sees the full contents of the changed files (passed as `file_context`
  by the shim) and is told to **drop** any finding the code refutes — so "X used before
  declaration" when X is declared, or "no engines pin" when `package.json` has one, no
  longer fails the build. A raw panelist finding can no longer gate on its own.
- **Blast-radius tiered.** `risk_tier` puts production source on the `full` bar
  (`critical`, or `high` ≥ 8) and developer tooling (`tools/`, `scripts/`, configs) on a
  `reduced` bar (`critical` or `high`, each only at confidence ≥ 8). The bar decides what is
  *eligible*; the chair still has to confirm it against the code. High-risk segments
  (auth/payment/…) force `full`. Decision record: `docs/council-gate-policy-2026-09-18.md`.
- **Deterministic + fail-closed.** The shim runs the gate at temperature 0. A genuine
  chair/panel outage still fails closed; `COUNCIL_ENFORCE=0` makes findings advisory.

See `docs/council-audit-2026-06-25.md` for the failure modes this replaced.

## Backup mode

When Venice's free daily credits (DIEM) are gone, `council ask`, `review` and `compare` run the
same panel, chair and gate through the owner's Claude and ChatGPT plans, using the `claude` and
`codex` command-line tools. No extra vendor and no API key. A complete backup run counts as a
council review for merges, provided its report shows the banner (owner decision 2026-11-04).
Flags, models, measurements and limits: `docs/council-backup-mode-2026-11-04.md`.

    COUNCIL_BACKUP=auto    # default for ask / review / compare
    COUNCIL_BACKUP=force   # backup now, whatever the balance; needs no Venice key
    COUNCIL_BACKUP=off     # Venice only, as before; the deliberate way to pay (not for `sweep`)

**When it switches (`auto`).** Before the run, if this key's DIEM is under `diem_floor` (0.5). A key
with a USD limit gets no error at DIEM 0, it just starts spending, so reacting to errors is not
enough, and the balance is read again every 30 seconds during the run. During the run, on the first
HTTP 402: that seat re-runs on the backup, the rest of the run follows, and seats that already
answered on Venice keep their answers. Nothing else switches: 401, 429, 5xx, timeouts and bad-model
errors behave as they always did.

**How far that protects money.** Only a key whose USD limit is 0 is a hard stop, because it gets the
402. On a key with a USD limit, a run can start new calls past the floor for up to 30 seconds, and
calls already in flight finish. If the balance cannot be read, the run stays on Venice (owner's
rule), and such a key can pay until a later read works or its limit is hit.

**What you see.** A `BACKUP MODE` banner at the very top in `md` and `term`: why, when credits
reset, how many model families really answered, and which seat ran on which tool and model. One
line on stderr at the moment of the switch. The raw panel names the model that really answered.

**Exit codes.** A run that stays on Venice exits as it always did. A backup run exits 3, never 0,
when every seat failed, at least half failed (the bar `run_pr_review` fails closed on), the chair
failed, or neither tool can be found. It never falls back to paying.

**Where it never runs.**
- `council sweep` is Venice credits or nothing. With credits out, or a balance it cannot read, it
  exits 3 and does not start, in every `COUNCIL_BACKUP` mode, so `off` cannot make a sweep pay on
  purpose. During a sweep the balance is read every 30 seconds: under the floor, a failed read or an
  HTTP 402 stops further calls, marks the report incomplete and exits 3.
- Python entry points never reach it: `run_pr_review`, the CI shim, `backlog-run` and `diem` build
  their own `VeniceClient`. A `COUNCIL_BACKUP` variable does not change them.
- The diem drain, `backlog-run` sessions, `security-sweep.sh` and `parallel-attempts.sh` set
  `COUNCIL_BACKUP=off` for any `council` they start. A new unattended script must do the same: it
  would otherwise default to `auto` and spend the plans.

**What differs from Venice.**
- The model spread: at most 2 families (Claude and OpenAI) rather than 5. Today `code-review` maps
  to Codex for all three seats and the chair, so a backup code review is 1 family.
- `temperature` and `max_completion_tokens` have no equivalent on these tools. The cap is accepted
  and ignored, and neither is faked.
- Plan limits are not credits. `plan limit reached` shows as a seat failure.

**Seat map.** Data, under `[settings.backup]` in `panels.toml`. `tests/test_council_backup_run.py`
walks every panel, seat, chair and router, and fails on a missing route or a Claude Adversary.

**Isolation.** A child has no tools, MCP servers, hooks, plugins, project or memory files, or saved
session, and an environment built from an allow list. The login is checked before the first model
call of each tool, each run: a login that is not the plan is not used. Codex's prompt is previewed
once per model per run, and Claude's reply carries its own setup. Either way a child that is not
isolated is refused. These checks fail closed, so a tool update can stop backup mode outright, with
the reason in the message. After updating `claude` or `codex`, run the live proof:

    COUNCIL_BACKUP_LIVE=1 python3 -m pytest tests/test_council_backup_live.py -q

**Log.** `~/.local/state/council/backup-runs.jsonl`: time, trigger (`diem`, `402`, `force`), panel,
seat-to-tool map, ok and failed counts. No prompts, diffs or keys. Backup calls never reach the
Venice usage ledger.

## Jev shadow signals (display only)

After the chair has answered, `council review` asks Jev (TypeSafe's small typed judge, model
`jev-1.13.0`) three label-and-score questions and prints the answers as one last section:
how it reads the chair's verdict, which findings two seats both raised ("raised by 2 of 3
seats"), and which panel finding each chair block confirms. `council sweep` adds one note
with the findings Jev would also group. This is **shadow mode**: the answers are shown and
logged (`~/.local/state/council/jev-shadow.jsonl`, ids and numbers only) and nothing reads
them. The panel, the chair, the gate, `run_pr_review` and every exit code are unchanged.
Every call goes through the shared client (`jev/`, `docs/contracts/jev.md`). `COUNCIL_JEV=0`
turns it off, and so does the shared `JEV_DISABLED=1`. Scope is an allow list of repositories
(`IN_SCOPE_REPOS` in `jev/scope.py`, shared with the backlog hold gate: the five the signals
were measured on; adding one is an owner decision). No `TYPESAFE_API_KEY`, or a repository that is not on the list, means no
call and byte-identical output. Text on stdin, or a diff saved outside any repository, is
judged by the working directory's repository alone. Details, the data rule and what
is deliberately not built: `docs/council-jev-shadow-2026-09-19.md`.

## Secret

`VENICE_API_KEY` from the environment / `.env`. Never commit it.

## Architecture

Pure Python (`requests` + `concurrent.futures`), no agent framework.
`engine.run_panel` fans out → `synthesize` (the chair) → `render`
(synthesis-on-top, raw panel below). The PR reviewer
(`setup/templates/venice_review.py`) is a thin front-end on the same engine.
