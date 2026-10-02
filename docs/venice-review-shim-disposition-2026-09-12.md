# The packaged CI shim: deleted — 2026-09-12

`council/ci_review.py`, behind the `council-ci-review` console entry point, is
deleted. Each repo keeps its own `scripts/venice_review.py`. This reverses the
structural recommendation of
[`venice-review-shim-audit-2026-07-23.md`](venice-review-shim-audit-2026-07-23.md)
§"stop having 17 copies", and it invalidates the held backlog item
`2026-07-23-venice-review-fleet-pass` as written.

## The evidence, from the two files

`diff -u council/ci_review.py scripts/venice_review.py` at `0269003`: 167 lines
vs 207, 132 changed lines. The gaps are not stylistic.

**What the packaged shim never received** (all of it live in all 18 repos):

| Missing | Landed in | Consequence of running the packaged shim |
|---|---|---|
| `_gh()` retries on connection errors and 5xx | `4af0abc` (#11), 2026-08-16 | one network blip drops the review comment |
| `_gh()` retries on 429/408 honouring a clamped `Retry-After` | `5a12869` (#12), 2026-08-20 | a rate-limit burst drops the review comment |
| `COMMENT_AUTHOR` check — PATCH only a marker-matched comment **authored by `github-actions[bot]`** | `5a12869` | anyone can paste `<!-- council-review -->` into a PR comment; the shim then PATCHes someone else's comment, gets 403, and the gate fails closed. A one-comment merge DoS. |
| `find_council_comment()` pagination past the first 100 comments | `5a12869` | a long PR thread silently breaks the one-rolling-comment invariant |
| `REQUIRED_ENV` validated upfront | `4af0abc` | a missing secret is a `KeyError` traceback instead of `::error::missing required env` |
| total file-context cap counted in UTF-8 **bytes** | `4af0abc` | multi-byte diffs overshoot the engine byte budget |

**What only the packaged shim has** — so this is a fork, not an old copy:

- `_read_capped()`: reads head-half + tail-half of an oversized changed file
  without loading the whole thing, where the live script does
  `candidate.read_text()` then truncates.
- `if file_cap <= 0: file_cap = 40_000` — the live script guards `ValueError`
  but not a negative or zero `COUNCIL_FILE_CAP`.

Both are worth having. They are recorded below as a follow-up rather than
ported here; `git show 40fd8d4:council/ci_review.py` is the source.

## Why deletion, and not parity

**1. It has never run. Not once.** No workflow in any of the 18 repos references
`council-ci-review`; every one runs `python scripts/venice_review.py`. Six weeks
of existence, zero executions, 22 unit tests.

**2. The fleet pass it was built for was spent on the other option.** The audit's
sequencing was: add the entry point (step 3), then point the fleet at it and
delete the copies (step 4). Step 3 shipped as v0.5.0 on **2026-08-02**. On
**2026-08-20**, eighteen days later, a fleet pass did run — and it rolled
hardening round 3 out to all 18 copies of `scripts/venice_review.py`
("Roll out venice_review shim round 3", ~13:18 UTC across the fleet), minutes
after round 3 merged here at 13:17 UTC. The migration's own budget was spent
reinforcing the thing the migration was meant to remove.

**3. "Drift impossible by construction" did not survive contact.** The shim was
created specifically because a file in 17 places drifts. It then drifted itself:
two hardening rounds (2026-08-16, 2026-08-20) went into the live script and not
into it. It was updated exactly once, for `ec3df23` — a mirror rate of 1 in 3.

**4. Unmaintained *and* installed is the bad combination.** `council` is
pip-installed in all 18 CI jobs, so `council-ci-review` is on the runner's PATH
right now. Switching a workflow to it is a one-line change, and the held backlog
item `2026-07-23-venice-review-fleet-pass` instructs a future runner to make
exactly that change in all 18 repos. Had it run, the fleet would have silently
lost the comment-ownership check, the retries and the pagination. Deleting the
module turns that from a silent security downgrade into an `ImportError` at the
first repo. Loud beats silent.

**5. It does not help the work actually in front of us.** The usage-artifact
rollout ([`ci-usage-rollout-2026-09-12.md`](ci-usage-rollout-2026-09-12.md)) is
18 hand edits to `.github/workflows/venice-review.yml`. A live shim would not
have saved one of them: `VENICE_USAGE_DB` is workflow env, and
`actions/upload-artifact` is a workflow step that no Python entry point can
replace. The "one change instead of eighteen" argument is worth zero on this
change, and the pin would still need bumping in 18 places on every release.

**6. Parity is cheap; keeping parity is not.** The port is ~60 lines with the
tests already written next door. But it would then have to be *held* at parity
until the fleet pass unblocks — an item held since 2026-07-23, last attempted
2026-09-04, and stuck on a backlog-runner limitation (`repo: none`), not on a
decision. The observed mirror rate over that window is 1 in 3. And when the
migration does happen it will port the *then-current* live script anyway, so a
half-ported copy in between buys nothing.

## What is kept instead

`scripts/venice_review.py` in this repo is the canonical shim. Each of the 18
repos carries a copy; the rollout mechanism is a fleet pass over that file, as
on 2026-08-20. `tests/test_no_packaged_ci_shim.py` fails if a packaged shim
reappears without a parity test, so the decision is not silently reversible.

## If the migration is revived

It is still the better architecture — this deletion is about a copy that nobody
runs, not about the idea. Reviving it means, in one change:

1. Port `scripts/venice_review.py` **as it then stands** into the package, plus
   `_read_capped()` and the `file_cap <= 0` clamp.
2. Add a parity test: the packaged module and `scripts/venice_review.py` must
   agree on `COMMENT_AUTHOR`, `REQUIRED_ENV`, the retry policy and the
   pagination, or the build fails. Without this, item 3 recurs.
3. Decide the key contract. The packaged shim used
   `council.config.get_api_key()` (`VENICE_COUNCIL_KEY` then `VENICE_API_KEY`);
   the repos' workflows pass `VENICE_API_KEY` from repo secrets.
4. Fleet pass: replace the `run:` line, delete `scripts/venice_review.py`, bump
   the pin — 18 repos, one branch each, `swimtrack-website` by hand.
5. Do it in the same pass as the usage-artifact rollout, or the 18-repo cost is
   paid twice.

## Follow-ups this leaves open

- **Salvage the two orphaned improvements.** Port `_read_capped()` and the
  `file_cap <= 0` clamp from `git show 40fd8d4:council/ci_review.py` into
  `scripts/venice_review.py`, then roll that file out with the next fleet pass.
  Low severity: today a large changed file is read fully into memory before
  being truncated.
- **Rewrite or drop backlog item `2026-07-23-venice-review-fleet-pass`.** Its
  prompt names `council-ci-review` and would now fail on import.
