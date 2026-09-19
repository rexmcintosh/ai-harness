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
(`IN_SCOPE_REPOS` in `council/jev.py`, the five the signals were measured on; adding one is
an owner decision). No `TYPESAFE_API_KEY`, or a repository that is not on the list, means no
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
