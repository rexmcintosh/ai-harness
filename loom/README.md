# Loom — session-learning pipeline (v1: live weave)

Distills Claude Code session transcripts into sanitized, classified learnings, then weaves each into
its home (wiki article, `~/wiki/decisions/`, per-project `memory/`, `~/.claude/skills/`) on the wiki's
`loom-shadow` branch. You review one diff, then `loom promote`.

## Commands
    .venv/bin/python -m loom.cli absorb            # shadow: distill only (no weave)
    .venv/bin/python -m loom.cli absorb --live     # nightly: distill + weave (Max session)
    .venv/bin/python -m loom.cli backfill --max-targets 3   # backlog weave on Venice/DIEM
    .venv/bin/python -m loom.cli promote           # apply staged .claude + merge loom-shadow -> master
    .venv/bin/python -m loom.cli requeue <sid>     # return a quarantined/stuck session to pending
    .venv/bin/python -m loom.cli rollback --ts <stamp>     # undo a promote from its backup
    .venv/bin/python -m loom.cli reconcile-phantom [--apply]  # one-time: fold + delete the phantom ~/wiki/wiki/ tree (docs/loom-phantom-wiki-reconcile-2026-09-09.md)
    ./loom/run-absorb.sh                            # cron entry: absorb --live + Telegram summary

## How it stays safe
- **Idempotent (structural):** every loom-shadow commit carries a `Loom-Woven:` trailer + each file an
  `<!-- loom-woven -->` marker (script-written). Lost ledger rebuilds from git.
- **Two shape lints** (trailing-append, excessive-rewrite) on wiki/memory facts; a **sentinel** scan on
  every route. Lint failure bisects the bundle, rejecting only the offender.
- **Transactional promote:** preflight -> backup ~/.claude -> atomic-swap -> merge, rollback on failure.
- **Bounded:** per-run target cap (oldest-first) + a global run deadline; the rest deferred and reported.
- **No silent drops:** `deferred` (retried) vs `rejected` (surfaced every summary until `requeue`).

## Backends
`absorb` = `claude` (Max session). `backfill` = `venice` (DIEM): route `gemini-3-5-flash`, weave
`claude-opus-4-8`. Needs `VENICE_API_KEY` (sourced from `/home/dev/.env`).

## One-time setup (rollout)
`./loom/setup-wiki.sh` — makes `~/wiki` a local git repo (no remote), installs the detect-secrets
pre-commit hook, creates the `loom-shadow` branch + the `~/wiki-loom-shadow` worktree.

## Spec & plan
- `docs/superpowers/specs/2026-06-07-loom-v1-live-weave-design.md`
- `docs/superpowers/plans/2026-06-08-loom-v1-live-weave.md`
