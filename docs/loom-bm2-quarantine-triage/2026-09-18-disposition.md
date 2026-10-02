# Triage: two malformed bm2-* quarantine artifacts (2026-09-18)

Backlog item: 2026-08-25-loom-triage-two-malformed-bm2-artifacts.

## Structural note: this can't be finished as a branch commit

`loom`'s runtime state (`loom/state.json`, `loom/learnings/`, `loom/quarantine/`)
is gitignored (`loom/.gitignore`) and `loom/cli.py` hardcodes its repo root to
`~/projects/ai-harness` regardless of cwd (the repository's name since 2026-09-18). So the actual
quarantine files, state.json and the learnings directory referenced by this
backlog item live only in the **main checkout**, never in a worktree, and are
never tracked by git. A worktree-scoped session cannot touch them (rule 1) and
has nothing to commit for them (they're gitignored). Everything below is
analysis plus a ready-to-run operator step; see `apply.py` in this folder.

## Root cause of the malformed distills

Both quarantined bm2-* artifacts are genuine YAML parse failures (verified
against `loom.run._parse_learnings`, not just eyeballed): an unquoted colon
inside a `learning:` value breaks the YAML mapping.

- `bm2-363556c5-bf8f-4ada-95ee-79e435d75d34.md` fails on `... with three books:
  False Start, ...` (colon after "books").
- `bm2-c2b2fe92-aca1-4140-9f0f-105efcbec759.md` fails on `Pricing for Heron
  Creek: Still Waters $2.99, ...` (colon after "Creek").

This is consistent with the known back-mining defect: the distill prompt for
these older transcripts didn't reliably quote `learning:` strings containing a
colon. Both files are otherwise well-formed, readable lists of learnings once
you allow for that.

## Disposition 1 — bm2-363556c5-bf8f-4ada-95ee-79e435d75d34

Original session `363556c5-bf8f-4ada-95ee-79e435d75d34` **is committed**
(`loom/state.json` state: `committed`; `loom/learnings/363556c5-....md` exists,
27 entries, already woven).

Comparing the 9 facts in the quarantined bm2 draft against those 27 committed
entries: 7 are covered (homepage direction pick, "link to the artifact"
preference, campaign image library, world-band night-natatorium image, shared
Bento account, the Archivo `wdth.css` procedure, and the Astro scoped-style
procedure). Two are **not** covered anywhere in the committed learnings:

- The Tessa Cross pen name, its three book titles (False Start, Out of Reach,
  Open Water), and the author site domain (authortessacross.com).
- The planned Freestyle reader-magnet (8,000-12,000 word training-camp short
  story, delivered via BookFunnel + a Bento Flow on `freestyle_optin`).

**Verdict: partially covered.** Salvaged the two uncovered facts into
`salvage-bm2-363556c5-bf8f-4ada-95ee-79e435d75d34.md` (parses cleanly under
`_parse_learnings`, verified). The malformed original should be deleted from
quarantine once the salvage artifact is in place.

## Disposition 2 — bm2-c2b2fe92-aca1-4140-9f0f-105efcbec759

Original session `c2b2fe92-aca1-4140-9f0f-105efcbec759` is **not** committed —
contrary to the backlog item's assumption that "the original transcripts...
were already processed by the normal pipeline." Its own distill is itself
sitting in `loom/quarantine/c2b2fe92-....md` with state `quarantined`. That
file is syntactically valid YAML (19 entries) but was apparently quarantined
for a different reason (e.g. a secret-detector hit) — its content wasn't
compared for parse validity, only for topic coverage, since it's not the
subject of this backlog item. It predates this item and belongs to the
existing quarantine-review backlog described in
`docs/loom-quarantine-disposition.md` (the 13-session 2026-07-23 review
queue); resolving it is out of scope here and was left untouched.

Comparing the 12 facts in the malformed bm2 draft against the 19 facts in the
original's own (unprocessed) quarantined draft: most overlap (launch strategy,
pricing, KDP Select/Saltbox boundary, Ellie Calloway brand, blurb model
choice/routing, KDP pre-order and keyword rules). Three are **not** present in
the original's draft at all:

- Heron Creek books will be published without DRM (permanent per-book KDP
  choice) — no DRM mention anywhere in the original's draft.
- The specific release schedule: Saltbox Season (BookFunnel magnet) July 27,
  2026; Still Waters pre-order immediately / releases Aug 24, 2026; High Tide
  Sep 14, 2026; Undertow Oct 5, 2026 (the original has the generic 72-hour
  pre-order rule, but none of these dates).
- The imprint name "Flight 7 Publishing" (used for all Ellie Calloway books)
  — absent from the original entirely.

**Verdict: partially covered** (and the "original" baseline is itself an
unresolved quarantine entry, not committed learnings). Salvaged the three
uncovered facts into `salvage-bm2-c2b2fe92-aca1-4140-9f0f-105efcbec759.md`
(parses cleanly, verified). The malformed bm2 file should be deleted from
quarantine once the salvage artifact is in place; the original session's own
quarantine entry is left for its existing review queue.

## Operator step (remaining, outward of this branch)

Run it when no loom run is live (the DIEM drain slots end at 23:50 UTC; absorb starts at
02:00 UTC). From any checkout of this branch:

    python3 docs/loom-bm2-quarantine-triage/apply.py

It targets the live data under `~/projects/ai-harness/loom/` by default (`--repo` points it
elsewhere; `--backup-dir` moves the backups). The script reads the two `salvage-*.md`
files beside it.

Before it writes anything it checks that `loom/state.json` exists there and already knows
both bm2-* ids, that both salvage files parse with loom's own `_parse_learnings`, that no
different artifact already sits under a salvage id, that no loom process is running, and
that `loom/.run.lock` is free. It holds that lock while it works. Any failed check prints
one line and exits 1 with nothing written. The first check matters: the repository was
renamed to `ai-harness` on 2026-09-18, and the first draft of this script still pointed at
the old name, where it would have created an empty `loom/` tree and reported success.

Then, per id, and safe to re-run:

1. Write `loom/learnings/<bm2-id>-salvage.md` from the matching `salvage-*.md`, if absent.
2. Advance `<bm2-id>-salvage` to `distilled` through loom's own `LoomState` writer, unless
   it is already `distilled` or later. A salvage that a later backfill has woven is never
   pulled back, so a re-run cannot cause a duplicate weave.
3. Advance `<bm2-id>` to `committed` (quarantine settled).
4. Copy `loom/quarantine/<bm2-id>.md` to `~/.local/state/loom/quarantine-resolved/`, verify
   the copy byte for byte, then delete the original.

After it runs, `loom/quarantine` contains no `bm2-*` files, and the two salvage learnings
ride the normal weave like any other distilled session. `tests/loom/test_bm2_triage_apply.py`
covers each refusal, the happy path, the re-run, and the backup.
