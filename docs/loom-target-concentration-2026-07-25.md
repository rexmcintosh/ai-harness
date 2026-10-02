# Loom target concentration — investigation & recommendation (2026-07-25)

Backlog item: `2026-07-23-loom-target-concentration` (status: held — "investigate and
recommend; this is a design decision, not a mechanical fix").

**Recommendation in one line:** add a **content-coverage check** in front of the opus
weave call so a learning whose fact the target article *already states* settles as
`committed (covered)` for near-zero cost — and run that check on the *whole* target
bucket, outside the per-target weave cap. This drains the standing backlog on its first
pass and stops it re-accumulating. Reject raising the cap; treat splitting the article as
an optional later readability change, not the fix.

No cap is changed by this document. Per the item's done-criteria, `max_per_target` stays
at 4 until this recommendation is accepted.

---

## 1. The problem got sharper since it was filed

The item was filed 2026-07-23. Re-measuring the live ledger on 2026-07-25 (two more
nightly runs later):

| Metric | 2026-07-23 (filed) | 2026-07-25 (now) |
|---|---|---|
| Pending (non-terminal) learnings | 628 | **270** |
| Distinct targets with pending | 115 | **38** |
| `tools/loom.md` pending | 295 (55%) | **227 (84%)** |
| Next-largest target | ~46 | 3 |
| Targets with exactly 1 pending | 75 | 32 |

The rest of the wiki drained cleanly (628 → 270 total; 115 → 38 targets; the long tail
is nearly gone). `tools/loom.md` barely moved (295 → 227). **So its share of the backlog
rose from 55% to 84% — it is now essentially the *entire* backlog.** Everything the
pipeline can drain, it drained; this one article is the residue.

Reproduce: read `loom/weave_ledger.json`, take entries whose `status` is not in
{`committed`, `rejected`, `quarantined`}, group by `target`.

## 2. The article is saturated, not slow

Full weave history of `tools/loom.md` on `master` (post loom-shadow promotion the whole
history is on master; `master...loom-shadow` is 0/0):

- **86 weave commits**, consuming **341 learnings**, moved the article a net **+65 lines**
  (churn +332 / −267). The file is 65 lines long today.
- Lifetime yield: **0.19 net lines per learning**, 0.76 per weave commit.
- Split chronologically into thirds by learnings consumed:

  | Phase | commits | learnings | net lines | net/learning |
  |---|---|---|---|---|
  | oldest third | 28 | 109 | **+53** | +0.486 |
  | middle third | 28 | 112 | +6 | +0.054 |
  | newest third | 30 | 120 | +6 | **+0.050** |

- Running length: `15 → 35 → 49` over the first 16 commits, then
  `53 → 55 → 59 → 59 → 59 → 59 → 63 → 63 → 65`. The article was *built* in its first ~16
  weaves; the last **~62 commits and ~230 learnings added ~12 lines.**

This is textbook saturation. Since roughly the article reached ~53 lines, loom has spent
~62 **opus-tier** weave calls to add ~12 net lines — the model is being handed batches of
facts it has already recorded and paying a premium call to restate them. Empty-diff
weaves are worse still: `gitio.py:41` skips the *commit* when the diff is empty, but the
opus call was already spent — a paid no-op that leaves no trace in the commit count above.

Reproduce: `git -C ~/wiki log master --numstat --format=... -- tools/loom.md`, sum
`Loom-Woven:` ids per commit against add/del.

## 3. Why it never drains (the mechanism)

Three code facts together make this article structurally undrainable:

1. **Weave is the expensive tier.** `backends.py:12` — `route: haiku`, `weave: opus`.
   Routing is already cheap; the cost is entirely in the weave.
2. **Dedup is id-based, not content-based.** `weave.py:76` —
   `fresh = [b for b in bundle if b["id"] not in present]`. `present` is the set of
   fingerprint markers already in the article plus committed ids. A *new* learning
   (new `session#idx` id) that merely *restates* an already-recorded fact is not in
   `present`, so it counts as `fresh` and drives a full opus weave.
3. **The per-target cap gates the expensive call, so cheap-to-settle work waits behind
   it.** `run.py:203` — `weave_now = buckets[target][:max_per_target]` (default 4); the
   overflow is `ledger.defer(..., "per-target cap")`. Only 4 of `loom.md`'s 227 pending
   are even eligible per run. The cap is deliberate and correct — it keeps each weave a
   small reviewable diff and prevents a bisect/cost storm on a popular article. But it
   throttles *all* work on the target, including the ~majority that need no weave at all.

Net effect: `tools/loom.md` takes on ~30+ new learnings on every loom-development night
(loom is self-documenting and under heavy active development — see the other
`2026-07-23-loom-*` backlog items), and drains at most 4/run, most of which are opus
no-ops. Inflow ≈ outflow, so the backlog sits at ~227 indefinitely. At 4/run with zero
inflow it would still take ~56 nights to clear.

## 4. The four options, judged against the evidence

**(a) Coverage-dedup — settle already-covered learnings without a weave. ✅ RECOMMENDED.**
The measurement (0.05 net lines/learning) says most pending learnings are already stated
in the article. A cheap check that recognises this and settles them as
`committed (covered)` — *before* the opus call and *outside* the cap — drains the standing
227 in a handful of cheap calls and permanently stops re-accumulation. Detailed below.

**(b) Split `tools/loom.md` into sub-articles (router / weave / promote / ledger / ops).
⚠️ Optional later; NOT the fix.** The article is only **65 lines** — it is not too *big*,
it is too *re-written*. Splitting gives more surface area but the same already-covered
restatements still each cost an opus call, now spread across five files with 5× the
routing surface (and more chances for the router to misfile, cf. the phantom `wiki/wiki/`
tree). Worth doing for readability once the article genuinely outgrows one page; it does
nothing for the cost problem today.

**(c) Per-target catch-up mode (raise `max_per_target` above a backlog threshold).
❌ REJECT.** This is a throughput lever, and the problem is not throughput. At 0.05 net
lines/learning, raising the cap buys more opus no-ops and larger, less-reviewable diffs on
the most popular article — exactly what the cap exists to prevent. It would drain the
*count* while spending the most DIEM to do it.

**(d) Bulk-prune the pending `loom.md` learnings as covered. ➜ Adopt as a consequence of
(a), not a separate hack.** A manual prune clears the 227 now but loses the audit trail
and re-accumulates on the next loom-dev night. Instead, let option (a)'s first pass do the
prune *by classifying* — the covered majority settle as `committed (covered)`
automatically, traceably, and the mechanism that cleaned them keeps them clean.

## 5. Recommended design — coverage-dedup

**Insertion point.** In `_weave_all` (`run.py`), after bucketing a target and *before*
applying `max_per_target`, run a coverage pass over the **entire** bucket using the
article text (`repo.read(target)`). Split the bucket into `covered` and `fresh`:

```
covered, fresh = partition_covered(cheap_backend, article, bucket)
for b in covered:
    ledger.mark(b["id"], "committed", reason="covered")   # settled, no opus call, no cap slot
    summary["committed"] += 1
# only `fresh` is then subject to the max_per_target weave cap, unchanged
```

**The check itself.** One batched call on the **haiku** tier (add a `"covered"` role to
`CLAUDE_MODELS` / `VENICE_MODELS`, mirroring the existing cheap `route` role): "Here is an
article and N candidate facts; return the indices of facts the article does **not**
already state with equal-or-greater specificity." One cheap call classifies the whole
bucket, replacing up to N opus weaves.

**Safety (this is the crux of the design review):**
- **Conservative default.** Any parse failure, timeout, or uncertainty → treat as `fresh`
  → weave. The change can only ever *remove* opus calls, never *drop* a genuine new fact
  by fallthrough.
- **Specificity guard in the prompt.** "Already stated with equal-or-greater specificity"
  keeps *refinements* (a more precise version of a recorded fact) on the weave path.
- **Nothing is lost even on a false "covered".** The learning stays in its
  `learnings/<sid>.md` artifact and in the ledger with `reason=covered`, and its id is
  recorded — fully auditable and reversible, exactly like a woven learning's fingerprint.
- **No interaction with the guard/bisect machinery.** Coverage runs before
  `_weave_recursive`, so the sentinel / shape-lint / bisect-on-fail path is untouched.

**Why this is the durable fix.** It attacks the actual cause (paying opus to restate
recorded facts), drains the standing 84% backlog on the first nightly run at haiku cost,
and prevents the residue from ever rebuilding — without touching the per-target cap that
legitimately protects genuine weaves.

## 6. If accepted — implementation sketch (TDD, separate change)

1. `tests/loom/test_weave.py` (or a new `test_coverage.py`): a bucket of learnings whose
   facts are verbatim in the article → all classified `covered`, `ledger` marks them
   `committed (covered)`, **zero** `weave` backend calls (assert via the fake backend's
   call log). A genuinely-new learning → stays `fresh`, one weave call. A backend
   error/garbage response → all treated `fresh` (fail-safe).
2. Add the `"covered"` role to both backends (haiku / gemini-flash tier).
3. Wire `partition_covered` into `_weave_all` ahead of the cap; add `reason="covered"` to
   the ledger mark so `committed` entries carry provenance.
4. Nightly runtime is `~/loom-runtime` (syncs from `main`), so merging to `main` deploys
   it. First live run should be watched: confirm `loom.md` pending collapses and the
   `committed (covered)` count matches expectation; spot-check a sample of "covered"
   learnings against the article to validate the classifier's precision before trusting it
   unattended.
5. Only after (1)–(4) land and the first drain is verified is the standing backlog
   considered cleared. The per-target cap is still not touched — the whole point is that
   coverage work no longer needs it.

## 7. Downstream

`2026-07-23-wiki-retirement-pass` (the 172 never-read articles) is gated on this
investigation. With the diagnosis settled — the wiki fragmentation around loom is a
*weave-cost* artifact of an id-based dedup, not a routing explosion — the retirement pass
can proceed once coverage-dedup is accepted, because master will stop churning on
`loom.md` and the retirement diff will be stable.
