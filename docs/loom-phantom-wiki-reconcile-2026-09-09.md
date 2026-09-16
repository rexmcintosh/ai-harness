# Phantom `~/wiki/wiki/` tree — reconcile runbook (2026-09-09)

Backlog item `2026-07-23-loom-phantom-wiki-tree-reconcile`. Prepared by an
unattended `backlog-run` session that may only edit this repo, so the wiki-side
step is packaged here as one command plus a push.

## What happened

Until 2026-07-23 loom's router emitted targets prefixed with `wiki/` and
`confirm_route` took them verbatim, so seven articles were woven to
`~/wiki/wiki/<dir>/...` — a second tree beside the real one. The cause is fixed
(`loom/route.py:normalize_target` strips the prefix and refuses `..`); the
2026-07-24 promote then landed the phantom tree into `master` and pushed it to
`origin/master`. As of 2026-09-09 03:28 UTC (`fd94964`, master == loom-shadow ==
origin/master) the tree is still there:

| phantom file | real counterpart | phantom marker ids |
|---|---|---|
| `wiki/_index.md` | `_index.md` (indexer-generated) | 1 |
| `wiki/patterns/bebop-briefing-mcp-connecting-failure.md` | `patterns/bebop-briefing-mcp-connecting-failure.md` | 1 |
| `wiki/patterns/loom-route-stage.md` | `patterns/loom-route-stage.md` | 1 |
| `wiki/projects/loom.md` | `projects/loom.md` | 1 |
| `wiki/projects/splash-poller.md` | `projects/splash-poller.md` | 1 |
| `wiki/tools/loom.md` | `tools/loom.md` | 16 |
| `wiki/tools/tmux-prefix-key.md` | `tools/tmux-prefix-key.md` | 4 |

The live ledger (`loom/weave_ledger.json`, main checkout) carries 25 entries with a
`wiki/...` target, all `committed`. They are left alone on purpose: git is
authoritative, the commits are promoted, and nothing is re-woven here.

## The tool

`python -m loom.cli reconcile-phantom` (module `loom/phantom.py`, tests
`tests/loom/test_phantom.py`).

- **Default = dry run.** Prints JSON: every phantom file, its action
  (`fold` / `delete` / `move`), the fold file that will be applied (if any), the
  marker ids it carries, and `candidate_paragraphs` — phantom paragraphs whose
  text is not found verbatim in the real article. Candidates are a prompt for
  human judgement, never auto-folded. Reads only; no git mutation.
- **`--apply`** on the master checkout (`~/wiki`), in one commit:
  1. `fold`: append the curated fold file (from `--folds-dir`, default
     `docs/loom-phantom-wiki-folds/<real-rel-path>`) above the real article's
     `<!-- loom-woven -->` marker, merge the phantom's marker ids into that marker
     (so `weave` treats those learnings as present), then `git rm` the phantom.
  2. `delete`: `wiki/_index.md` is a separate mini-index; deleted, never folded.
  3. `move`: a phantom with no real counterpart is `git mv`-ed into place
     (none in the live wiki; covered for safety).
  4. If `~/wiki-loom-shadow` is exactly at master it is fast-forwarded so the
     phantom dir disappears there too. If shadow has unpromoted commits it is
     left alone; the next `promote` merges master's delete over it (tested).
- **Refuses** to run when: not on `master`; working tree dirty; a fold file
  matches no phantom target (typo guard); the folds dir is missing; a loom
  `absorb`/`backfill`/`promote` process is live; `loom/.run.lock` is held.
- **Never pushes.** The result JSON carries the exact push command. `promote`
  also pushes master nightly, so a forgotten push self-heals — but push now so
  the delete cannot resurface from `origin`.

## Fold decisions (content review done 2026-09-09)

Each phantom was read against its real counterpart. "Covered" means the real
article already states the fact (usually verbatim; the accretion cleanup of
2026-08-20 deliberately de-duplicated `tools/loom.md`).

| real article | decision | why |
|---|---|---|
| `_index.md` | delete, no fold | Mini-index. Its one fact (project **bank-sales** exists) is already `projects/bank-sales.md`. |
| `patterns/bebop-briefing-mcp-connecting-failure.md` | no fold, merge marker | Both failure modes (query miss → new terms; "connecting" → wait, same query) and the "don't conflate" note are in the real "Distinct failure modes" section. |
| `patterns/loom-route-stage.md` | no fold, merge marker | Update-over-create and "conflicts resolved inside the existing article, never a parallel page" are verbatim in "Resolution rules". `[[loom-weave-stage]]` is a dangling link, not a fact. |
| `projects/loom.md` | no fold, merge marker | "Recursive wrapping → nested identical YAML, no new signal, safe no-op" is verbatim in the real "Distill stage". |
| `projects/splash-poller.md` | no fold, merge marker | Its only claim is that `wiki/projects/splash-poller.md` is the canonical path — that claim *is* the bug. The real article is the canonical home. |
| `tools/loom.md` | no fold, merge marker | Prompt paths, data-not-instructions, two-stage flow, bare-JSON route contract, nested-YAML safety, empty/`-` transcript → empty list, meta-only transcripts, self-routing to `tools/loom.md` with `update`, "already absorbed" no-op: all present in the rewritten article. Re-adding restatements would redo the 2026-08 accretion. |
| `tools/tmux-prefix-key.md` | **fold** `docs/loom-phantom-wiki-folds/tools/tmux-prefix-key.md`, merge marker | Two small extras were absent: the `Ctrl+a` then `c` new-window example and the GNU Screen / reachability rationale. |

Marker merge touches six real articles (the 24 phantom ids join their markers);
`_index.md` is indexer-owned and gets nothing.

## Operator steps (after this branch merges to `main`)

1. Make sure no loom run is live: `pgrep -f "loom.cli (absorb|backfill|promote)"` prints nothing.
2. Dry run, read the JSON, confirm `unmatched_folds` is empty and the seven files
   show the actions above:

        cd ~/projects/build-ai-automation-workflow
        .venv/bin/python -m loom.cli reconcile-phantom

3. Apply (one commit on `~/wiki` master, shadow fast-forwarded):

        .venv/bin/python -m loom.cli reconcile-phantom --apply

4. Push so the delete propagates (the wiki has a GitHub remote; un-pushed deletes resurface):

        git -C ~/wiki push origin master

5. Verify the done-criteria:

        find ~/wiki ~/wiki-loom-shadow -type d -name wiki        # -> nothing
        git -C ~/wiki status -sb                                   # -> ## master...origin/master
        .venv/bin/python -m loom.cli pending | head -c 200         # -> parses

   The nightly indexer rebuild drops any phantom slugs from `_index.md` /
   `_backlinks.md`; no manual index edit is needed.

If `--apply` refuses (dirty tree, shadow out of sync, lock held), fix the stated
condition and re-run; it is idempotent and a second run after success reports
`no phantom tree`.

## Notes for the reviewer

- `tests/loom/test_promote.py` fails inside `backlog-run` worktrees on an untouched
  baseline: the runner points every push URL at `/nonexistent/backlog-run-no-push/`,
  which also breaks those tests' push to their own temp remote. Environmental, not
  a regression (already documented in the wiki's `tools/loom.md`).
- Nothing outside this repo was modified; the dry run above was executed against
  `~/wiki` read-only and its output informed the decision table.
