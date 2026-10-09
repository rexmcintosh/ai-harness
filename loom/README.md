# Loom (retired 2026-10-09)

Loom was the session-learning pipeline. Every night at 02:00 UTC it read Claude Code session
transcripts, distilled them into sanitized learnings, and wove each one into its home (wiki
article, `~/wiki/decisions/`, per-project `memory/`, `~/.claude/skills/`) on the wiki's
`loom-shadow` branch, then promoted the result. A Venice/DIEM `backfill` job, fed by the
`diem drain` filler, worked through the backlog.

## Why it was retired

The 2026-10-09 audit (`~/projects/hq/loom-audit-2026-10-09.md`) found that almost nothing
read the woven output, the Bebop briefing line never arrived, wiki quality was getting worse,
and Loom used about a sixth of fresh Claude input tokens plus about 33 fix sessions since
June. Claude memory already covered the useful part. Rex approved the shutdown on 2026-10-09. The code and tests were removed
on branch `claude/retire-loom`; the design docs stay in `docs/` (see
`docs/contracts/loom-absorb.md` and `docs/superpowers/specs/*loom*`).

This folder now holds only this note and `.gitignore`. The `.gitignore` keeps any leftover
runtime data (state, logs, spool, quarantine, ledgers) out of git until it is archived.

## Where the archives live

- Runtime data and spool: `~/projects/_archive/loom-2026-10/`.
- Wiki: git tags `loom-final-master` and `loom-final-shadow` mark the last woven state.
- Code: git history before the `claude/retire-loom` merge.

## How to restore

1. `git revert -m 1 <merge commit of claude/retire-loom>` in this repo.
2. Run `bash loom/setup-runtime.sh` from the reverted tree to rebuild `~/loom-runtime`.
3. Untar the data archives from `~/projects/_archive/loom-2026-10/` into this repo.
4. Re-enable the cron line: uncomment the `# LOOM-OFF` line in `crontab -l`.
5. Restore `backfill_max_per_night` in `~/.config/diem/config.toml` from its
   `config.toml.bak.*` copy.

The audit's "Full undo, in one place" section has the complete list, including the wiki.
