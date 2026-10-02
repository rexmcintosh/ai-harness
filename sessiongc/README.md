# session-gc

Lifecycle hygiene for Claude Code session worktrees & branches across `~/projects`.

The Claude Code harness owns worktree **directories** — it creates them per session and
reliably reaps them (sometimes mid-session). It leaves two layers unmanaged, which this
tool owns:

- **WIP layer** — uncommitted work in a live worktree is lost when the harness reaps the
  directory. `snapshot` captures it out-of-band.
- **Branch layer** — `claude/*` branches outlive their reaped worktrees. `sweep` deletes
  the safe (merged) ones and reports the stranded (unmerged) ones.

The primary defense against branch accumulation is deleting the session branch **at merge
time** (see the merge protocol in `~/.claude/CLAUDE.md`); `session-gc` is the safety net
for merges done outside that protocol and for abandoned sessions.

## Commands

    session-gc snapshot            # cron ~10min: snapshot dirty claude/* worktrees -> refs/wip/*
    session-gc sweep               # dry-run: classify orphan claude/* branches, write report
    session-gc sweep --apply       # delete Tier A (merged); journaled + restorable
    session-gc sweep --apply --delete-tier-b   # also delete content-merged branches >14d
    session-gc sweep --repo NAME   # scope to one repo
    session-gc report              # print the last sweep report
    session-gc restore             # list the deletion journal
    session-gc restore BRANCH      # recreate a deleted branch from the journal

State lives in `~/projects/.session-gc/` (`report.md`, `journal.log`, `lock`, optional
`exclude` = one repo dir-name per line).

## Classification

| Tier | Meaning | Detection | Action |
|------|---------|-----------|--------|
| A | ancestry-merged into default | `rev-list --count base..branch == 0` | auto-delete (`--apply`) |
| B | content-merged (rebase/squash) | `git cherry` all `-`, or `merge-tree` result tree == base tree | candidate; `--delete-tier-b` past 14d |
| C | genuinely unmerged | neither | **report-only, never deleted** (stranded WIP) |

## Safety invariants (I1–I9, enforced in `cli.py`)

1. Never writes inside a worktree directory or its real index/HEAD (snapshot uses a
   private temp `GIT_INDEX_FILE` seeded from HEAD; `commit-tree` only, no checkout).
2. Only `refs/heads/claude/*` and `refs/wip/*` are ever mutated; remote refs read-only.
3. Deletes only: `claude/*` + orphan (not checked out in any worktree) + Tier A (or gated
   Tier B) + default branch resolved + age > 1h grace.
4. Merged-ness is proven against the **default branch** by the tool (not `git branch -d`,
   whose upstream-based check is wrong here); `-D` is used but still refuses a checked-out
   branch (the live-session guard).
5. Creation-race grace via reflog oldest-entry age; fails **closed** (protects) when age
   is genuinely unknowable (reflogs disabled), eligible only when provably old (expired).
6. Every deletion is journaled **before** it happens.
7. Fail closed per repo — any ambiguity (default branch, git error, parse) → skip + report.
8. Tier C has **no** code path that deletes it.
9. One run at a time (flock); all git addressed via the main checkout (`-C repo`), safe to
   invoke from inside a session worktree.

## Cron

    */10 * * * *  session-gc snapshot   >> ~/projects/.session-gc/snapshot.log 2>&1
    0 8 * * 1     session-gc sweep       >> ~/projects/.session-gc/sweep.log 2>&1   # weekly, report-only

The weekly sweep is report-only; deletion is always an explicit `--apply` (or `/sweep`).
