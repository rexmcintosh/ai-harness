# Session worktree hygiene

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed command:** `session-gc`

## Purpose and authority

**Default mode:** snapshot preserves session work and expires old private snapshot refs; weekly sweep is report-only. The ten-minute snapshot preserves dirty eligible worktree state in private `refs/wip/*`. The Monday sweep classifies orphan branches and writes a report. Canonical lifecycle state is `~/projects/.session-gc/` and the owning Git refs.

Snapshot may create and expire only `refs/wip/*`; `_expire_wip` removes refs whose
commit timestamp is more than 30 days old. Sweep may read and report only;
branch deletion requires a separate explicit `--apply`. Neither scheduled
command may write a worktree, change remote refs, delete Tier C branches, or use
a fuzzy session-end event.

Eligibility is owned by `sessiongc/cli.py:discover_repos` and `cmd_snapshot`:
non-excluded repositories directly under `~/projects` with a `.git` directory;
an existing worktree directory; either a `claude/*` branch or a detached session
worktree under `.claude/worktrees/`; and nonempty `git status --porcelain`.
Clean worktrees and unrelated detached checkouts are skipped.

**Secrets:** none expected.

## Success and evidence

Success is a timestamped cron record plus snapshot refs for dirty eligible worktrees, or a fresh report for the weekly sweep. Inspect `~/projects/.session-gc/snapshot.log`, `~/projects/.session-gc/sweep.log`, `~/projects/.session-gc/report.md`, `~/projects/.session-gc/journal.log`, and `refs/wip/*`. The implementation’s invariants in `sessiongc/cli.py` are the relevant safety evidence. Proposed outcome measure: each dirty eligible worktree has a recoverable snapshot before reaping, while scheduled sweeps delete nothing.

## Failure, escalation, and gaps

Ambiguity should fail closed per repository and appear in the report. The scheduled sweep does not pass `--notify`; manual `--notify` and `--notify-strict` exist for Tier C WIP. Review the weekly report and cron logs. Recovery checks must use a snapshot within the 30-day retention window; a snapshot ref is not an indefinite backup guarantee.
