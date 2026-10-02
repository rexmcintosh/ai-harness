# venice-review fleet audit — pins + shim drift — 2026-07-23

**Why.** The council regression check (`council-regression-2026-07-23.md`) found the
S1 grounding fix half-propagated: the engine pin was upgraded fleet-wide, but the
CI *shim* (`scripts/venice_review.py`) that decides whether `file_context` reaches
the engine was upgraded in exactly one repo. This audit maps every repo running the
venice-review gate. Backlog item: `2026-07-23-venice-review-shim-audit`.

## Method

`grep`/`md5sum` sweep over `~/projects/*/.github/workflows/venice-review.yml` and
`~/projects/*/scripts/venice_review.py`, 2026-07-23. Facts, not run behavior — no
API calls.

## Headline

- **17 repos** run the gate. **All 17 pin `council-v0.4.0`** (monthly-bidding was
  the last stale pin — bumped today via its PR #1).
- **Shims: 16 of 17 are byte-identical copies of the legacy v0.2.0-era shim**
  (md5 `2968cbde…`, 49 lines) — including `build-ai-automation-workflow` itself,
  council's home repo. Only **swimtrack-website** carries the v0.4.0-era reference
  shim (md5 `9dd1d7cc…`, 128 lines).
- Net: the fleet runs a v0.4.0 engine fed by a v0.2.0 harness. The regression
  check showed the engine's chair arbitration alone still clears the golden set
  (the `noctx` arm) — so the gate is not broken today, but grounding, determinism,
  and the advisory escape hatch simply don't exist in 16 repos.

## What the legacy shim is missing vs the reference

| Capability | Legacy (16 repos) | Reference (swimtrack-website) | Why it matters |
|---|---|---|---|
| `file_context` to `run_pr_review` | ✗ | ✓ gather changed files + anchors (`package.json`/`.gitignore`/`.nvmrc`), 40 KB/file, 160 KB cap | Audit S1. Kills false findings at the panel instead of out-voting them at the chair |
| `temperature=0` on the gate client | ✗ | ✓ | Audit E3/B — run-to-run determinism of the blocking verdict |
| Rolling comment (upsert one) | ✗ posts a new comment per run | ✓ finds & updates its own comment | PR hygiene on multi-round reviews |
| `COUNCIL_ENFORCE=0` advisory mode | ✗ | ✓ | The documented escape hatch when the gate misfires |
| Error wording | "blocking finding(s)" | "chair-confirmed blocking finding(s)" | Reflects who actually decides in v0.4.0 |

## The matrix

Pin is `council-v0.4.0` for every row (verified today), so only shim state varies.

| Repo | Shim | file_context | Recommendation |
|---|---|---|---|
| swimtrack-website | reference (128-line) — **shares the traversal defect below** | ✓ | port PR #5's containment guard + cap-parse guard |
| aris-management-website | legacy + **PR #5 in flight** ports `gather_file_context` (hardened — see below) | ✓ pending merge | merge PR #5; pick up remaining reference deltas (temp=0, upsert, advisory) with the fleet |
| build-ai-automation-workflow | legacy | ✗ | adopt reference — council's own repo should not run the legacy harness |
| MCP-Configuration-Assistant | legacy | ✗ | adopt reference |
| Santa_Amaro_Home_Renovation | legacy | ✗ | adopt reference |
| blkout-dice-roller | legacy | ✗ | adopt reference |
| bubblepop-art | legacy | ✗ | adopt reference |
| combat-arms-transition | legacy | ✗ | adopt reference |
| crypto-portfolio | legacy | ✗ | adopt reference |
| dump | legacy | ✗ | adopt reference |
| finance-tracker | legacy | ✗ | adopt reference |
| liam_mobility | legacy | ✗ | adopt reference |
| macmcintosh | legacy | ✗ | adopt reference |
| monthly-bidding | legacy | ✗ | adopt reference |
| romance-empire | legacy | ✗ | adopt reference |
| splash_poller | legacy | ✗ | adopt reference |
| swimtrack | legacy | ✗ | adopt reference |

Because the 16 legacy copies are byte-identical, propagation is mechanical: one
canonical file, 15 branch-per-repo passes (aris covered by PR #5).

## Live true positive: the gate blocked its own hardening PR — and was right

aris PR #5's first council run (grounded, via the very shim it was adding)
returned **1 chair-confirmed blocking finding — and it was genuine**:
`gather_file_context` built `root / rel` from diff-derived paths with no
containment check. Verified against `council/routing.py:64`: `changed_paths()`
extracts paths verbatim from diff headers, no sanitization — so a crafted diff
could name `../../etc/passwd` or an absolute path and the shim would read it and
ship the contents to the review API (and potentially into a posted comment).
Second consensus finding (unguarded `int(COUNCIL_FILE_CAP)` crash) also real.

Both fixed in PR #5 (`0f6089c`): resolve-and-`is_relative_to` containment +
`ValueError` fallback, verified by a hostile-diff test (traversal paths yield
anchors only, no escape; normal gathering unchanged).

Two consequences for this audit:

1. **The reference shim in swimtrack-website has the identical unguarded
   pattern** — the defect originates there. Its row above now carries the fix
   recommendation, and the `council-ci-review` port below MUST include the guard.
2. This is AC2 ("true positives still block") evidenced in the wild, on the same
   day the golden set showed false positives no longer block. The gate earned
   both halves of its keep — worth stating because the regression check
   explicitly could not test AC2.

## The structural recommendation — stop having 17 copies

This audit exists because a fix was made in one copy of a file that exists in 17
places. That will recur on every future shim change. The durable fix is to **move
the shim into the council package as a console entry point** (e.g.
`council-ci-review`), so each repo's workflow step becomes
`run: council-ci-review` and the *existing version pin governs the shim and the
engine together* — shim drift becomes impossible by construction, and 17 files
are deleted. ("Does this door need to exist at all?" — the remove-surface rule.)

Suggested sequencing:
1. Merge aris PR #5 (in flight — restores grounding where the false positives
   actually happened, with the traversal guard).
2. Port the two PR #5 guards to swimtrack-website's shim (6 lines — it is the
   origin of the unguarded pattern).
3. Add `council-ci-review` to the council package (port of the reference shim
   **including the guards**), release as `council-v0.5.0`.
4. Fleet pass: bump pin + replace the `Run Venice review council` step + delete
   `scripts/venice_review.py`, one branch-per-repo.

Step 3 subsumes the per-repo "adopt reference" recommendations above; do them as
one item, not two waves.

## Limits

- Static audit: file identity and grep facts only. No review runs were made; CI
  behavior is inferred from the shim source plus today's regression-check runs.
- Repos outside `~/projects` (if any run the gate) are not covered; nothing in
  memory or CLAUDE.md names one.

---

## Disposition of the structural recommendation — 2026-09-12

**Reversed.** Step 3 (`council-ci-review`, v0.5.0) shipped 2026-08-02; step 4
(the fleet pass) never ran. On 2026-08-20 a fleet pass rolled hardening round 3
out to all 18 copies of `scripts/venice_review.py` instead, and the packaged
shim went six weeks without rounds 2 and 3 while remaining installed and
callable on every runner.

`council/ci_review.py` and the `council-ci-review` entry point are deleted.
Per-repo `scripts/venice_review.py` is the canonical shim. Evidence, the file
diff, and what reviving the migration would involve:
[`venice-review-shim-disposition-2026-09-12.md`](venice-review-shim-disposition-2026-09-12.md).

Backlog item `2026-07-23-venice-review-fleet-pass` cites this document as its
spec and is invalid as written.
