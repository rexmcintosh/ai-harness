# Making the CI merge gate's Venice spend durable — 2026-09-11

> **Superseded in part, 2026-09-12.** Two corrections and a verified runbook are
> in [`ci-usage-rollout-2026-09-12.md`](ci-usage-rollout-2026-09-12.md):
>
> 1. §"The finding, confirmed" below says the runner logs its spend into a
>    ledger that dies with the job. At the **pinned** council (`4a01298`,
>    v0.4.0) it does not log at all: that commit packages `council` only — no
>    `venice_usage`, and `council/venice.py` has no `_log_usage`. The pin bump
>    is what turns CI logging on; `VENICE_USAGE_DB` + export + upload is what
>    keeps it. The conclusion is unchanged, the mechanism is not.
> 2. §"The workflow change" carries a `REPLACE_WITH_MERGE_SHA` placeholder. The
>    pin is `026900319c4deb479d6c9abe01fd537ac5ac09a4`, derived in the rollout
>    doc; it does not depend on any branch merging.
>
> `tools/ci-usage/repos.txt` named three repos by their `~/projects` directory
> name rather than their GitHub name, and `pull.sh` reported the resulting 404
> as "no runs in window". Both fixed 2026-09-12.

## The finding, confirmed

`council/venice.py:39-52` `_log_usage()` fires on every Venice call, including
every call the merge gate makes on a GitHub Actions runner. It writes through
`venice_usage.append()` to `venice_usage/ledger.py`'s `default_db()`, which is
`~/.local/state/venice-usage/ledger.db` — **the runner's** home directory, which
GitHub destroys when the job ends.

Evidence that the rows never arrive anywhere we can see them:

```
$ python3 -c "...SELECT project, COUNT(*) ... FROM usage GROUP BY project"
romance             8448
loom                6616
swimtrack-website   5850
council             1707        <- every one of these is a LOCAL council call
backlog-runner        25
ultimate-portugal      4
$ ... "SELECT task_type, COUNT(*) FROM usage WHERE project='council' GROUP BY task_type"
chat   1686      <- `council ask` / `council review` on the VPS
review   16      <- run_pr_review, all of them local test runs
ask       5
```

16 `review` rows in 54 days, against a CI key that Venice itself bills at
**34.11 DIEM over the trailing 7 days**. The gate's spend is logged successfully
into nothing.

`venice_usage/ledger.py:9-13` already honours a `VENICE_USAGE_DB` override, so
redirecting the write is one env var. Getting the file off the runner afterwards
is the actual work.

## Options weighed

| Option | New infra | New secret | Lossless per run | Verdict |
|---|---|---|---|---|
| **Workflow artifact** | none | none | yes — one artifact per run, per attempt | **chosen** |
| POST to a durable endpoint | a service to receive it, plus uptime | yes (endpoint + auth) | yes | rejected |
| Write into the PR as a comment | none | none | **no** | rejected |

**Why not the POST.** It needs somewhere to receive the rows and a credential in
18 repos' secret stores. That is a service to run, monitor and rotate keys for, to
solve a problem whose entire content is "move a 1 KB file". The brief asked to
prefer no new infrastructure and no new secret; this fails both.

**Why not the PR comment.** The shim deliberately keeps *one rolling comment* per
PR, updated in place (audit S2, `upsert_comment`). Usage written into that comment
is overwritten on the next push — and the `synchronize` re-runs are exactly the
spend we most need to see, because three pushes to one PR is three full panels. A
check-run summary avoids the overwrite but is an extra API surface, needs
`checks: write`, and is still harder to read back in bulk than `gh run download`.
Discarded on the losing-the-data point alone.

**Why the artifact wins.** `GITHUB_TOKEN` already exists in the job.
`actions/upload-artifact` needs no permission beyond the default. Every workflow
run — including every `synchronize` re-run and every re-run attempt — gets its own
artifact, so nothing is overwritten. 90-day retention is far longer than any
reconciliation window. And `gh run download` makes bulk retrieval a loop.

## What was built

**`venice_usage/portable.py`** — export the ledger to JSONL, ingest JSONL back.

```
{"kind":"venice-usage-export","version":1,"origin":{...},"exported_at":...,"rows":4}
{"ts":...,"project":"council",...,"ext_id":"gh:owner/repo:9001:1#1"}
```

`ext_id` is `<origin id>#<row id in the exporting ledger>`, where the origin id is
`gh:<repo>:<run_id>:<run_attempt>`. It does two jobs:

- **Idempotency.** `usage.ext_id` gained a *partial* unique index
  (`WHERE ext_id IS NOT NULL`) and `append()` became `INSERT OR IGNORE`.
  Re-downloading an artifact cannot double-count spend. Locally-appended rows keep
  `ext_id = NULL`, so two identical local calls still count as two calls.
- **Provenance.** `ext_id LIKE 'gh:%'` is exactly the CI rows, and it names the
  repo, the run and the attempt. The attempt is in the key on purpose: re-running a
  failed workflow really does spend a second panel's worth of calls.

**Ledger migration.** `connect()` adds the column to a pre-existing ledger on first
open. Verified against a copy of the real 22,650-row ledger — schema gains `ext_id`,
row count and every per-project rollup unchanged.

**`venice-usage export` / `venice-usage ingest`** — the two CLI verbs. `export`
follows the same best-effort contract as `log` (exit 0 with a warning on failure;
`--strict` when you want the failure) because accounting must never be the reason a
merge gate fails. `ingest` is an operator command and exits 2 on a real failure.

**`tools/ci-usage/pull.sh`** — `gh run list` + `gh run download` across
`tools/ci-usage/repos.txt`, then `venice-usage ingest`. Safe to cron: idempotent.

## The workflow change (proposal — NOT applied)

> Superseded by [`ci-usage-rollout-2026-09-12.md`](ci-usage-rollout-2026-09-12.md),
> which carries the real pin, both patches (the shared one and
> swimtrack-website's), the per-repo apply command and the verification
> hashes. The diff below is kept for the reasoning, not for use.

Full proposed file: [`proposals/venice-review.yml.proposed`](proposals/venice-review.yml.proposed).
Patch: [`proposals/venice-review-usage-artifact.patch`](proposals/venice-review-usage-artifact.patch).

```diff
       - name: Install council
-        run: pip install --quiet "council @ git+https://github.com/rexmcintosh/build-ai-automation-workflow@4a01298dcce2734115ac57f02592146969d76f48"
+        run: pip install --quiet "council @ git+https://github.com/rexmcintosh/build-ai-automation-workflow@REPLACE_WITH_MERGE_SHA"
@@
           DIFF_PATH: /tmp/pr.diff
+          # Put the usage ledger somewhere we can find it again. Without this it
+          # lands in the runner's $HOME and dies with the job.
+          VENICE_USAGE_DB: ${{ runner.temp }}/venice-usage.db
         run: python scripts/venice_review.py
+
+      # Accounting must never be the reason a merge gate fails: always(), and
+      # continue-on-error on both steps. A lost usage row is a lost row; a
+      # blocked merge is a blocked merge.
+      - name: Export Venice usage
+        if: always()
+        continue-on-error: true
+        env:
+          VENICE_USAGE_DB: ${{ runner.temp }}/venice-usage.db
+          PR_NUMBER: ${{ github.event.pull_request.number }}
+        run: venice-usage export --output "${{ runner.temp }}/venice-usage.jsonl"
+
+      - name: Upload Venice usage
+        if: always()
+        continue-on-error: true
+        uses: actions/upload-artifact@v4
+        with:
+          # run_attempt keeps a re-run from colliding with the immutable v4
+          # artifact of attempt 1 — and a re-run really is a second panel.
+          name: venice-usage-${{ github.run_attempt }}
+          path: ${{ runner.temp }}/venice-usage.jsonl
+          retention-days: 90
+          if-no-files-found: warn
```

Three things to know before rolling it out:

1. **The pin bump is mandatory, not optional.** All 18 repos currently install
   `@4a01298dcce2734115ac57f02592146969d76f48` (council-v0.4.0), which has no
   `venice-usage export`. The export step would fail (harmlessly —
   `continue-on-error`) and produce nothing. Replace `REPLACE_WITH_MERGE_SHA` with
   the commit this branch merges as.
2. **`${{ runner.temp }}` must stay at step level.** The `runner` context is not
   available in `jobs.<id>.env`, only in step `env`. That is why the path is
   written twice instead of hoisted.
3. **No new secret and no new permission.** `upload-artifact` works on the default
   `GITHUB_TOKEN`; the workflow's existing `permissions:` block is unchanged.

### Repos that need it

All 18 that carry `.github/workflows/venice-review.yml`. Verified 2026-09-11 that
**17 of the 18 files are byte-identical**, so the patch applies unchanged to:

```
rexmcintosh/MCP-Configuration-Assistant   rexmcintosh/finance-tracker
rexmcintosh/Santa_Amaro_Home_Renovation   rexmcintosh/liam_mobility
rexmcintosh/aris-management-website       rexmcintosh/macmcintosh
rexmcintosh/blkout-dice-roller            rexmcintosh/monthly-bidding
rexmcintosh/bubblepop-art                 rexmcintosh/romance-empire
rexmcintosh/build-ai-automation-workflow  rexmcintosh/splash_poller
rexmcintosh/combat-arms-transition        rexmcintosh/swimtrack
rexmcintosh/crypto-portfolio              rexmcintosh/ultimate-portugal
rexmcintosh/dump
```

**`rexmcintosh/swimtrack-website` is the exception.** Its workflow adds Node setup,
`npm test`, season-DB and readability steps, and sets `GITHUB_WORKSPACE` and
`COUNCIL_ENFORCE` on the review step. The same three edits still apply — bump the
pin, add `VENICE_USAGE_DB` to the review step's existing `env:` block, append the
two new steps at the end of `steps:` — but apply them by hand, not with the patch
file.

## Reconciling afterwards

```bash
# nightly, or on demand
tools/ci-usage/pull.sh --days 8

# what the gate actually costs, by repo
venice-usage report --since 2026-09-04 --group-by project,model
python3 - <<'PY'
import sqlite3, os
db = os.path.expanduser("~/.local/state/venice-usage/ledger.db")
q = """SELECT substr(ext_id, 4, instr(substr(ext_id,4), ':')-1) AS repo,
              COUNT(*) calls, SUM(tokens_in) tin, SUM(tokens_out) tout
       FROM usage WHERE ext_id LIKE 'gh:%' AND ts >= ? GROUP BY repo ORDER BY tout DESC"""
for r in sqlite3.connect(db).execute(q, ("2026-09-04",)):
    print(r)
PY
```

Cross-check the total against Venice's own record for the CI key with
`GET /billing/usage-history` (`$VENICE_ADMIN_KEY`; see the
`venice-model-pricing` memory note for the cursor-pagination trap). The ledger
counts tokens, Venice bills DIEM — the ledger's `usd` column is a stale local
estimate (`venice_usage/pricing.py` still prices `claude-opus-4-8` at 15/75 when
the live catalogue says 6/30) and must not be used as a bill.

## Verified

End-to-end, with the runner's home directory deleted between export and ingest:

```
STEP 1  4 seats logged on the runner    -> runner/ledger.db  25k
STEP 2  venice-usage export             -> exported 4 row(s)
STEP 3  rm -rf runner/                  -> only venice-usage.jsonl survives
STEP 4  venice-usage ingest (1st)       -> inserted 4, skipped 0
        venice-usage ingest (2nd)       -> inserted 0, skipped 4
        report --group-by model         -> all four seats present
        ext_id                          -> gh:rexmcintosh/swimtrack-website:9001:1#1..#4
```

`tools/ci-usage/pull.sh --days 400` was run live against
`rexmcintosh/swimtrack-website` and `rexmcintosh/build-ai-automation-workflow`: it
listed real workflow runs and correctly reported `0 run(s) with usage artifacts`,
because the workflow change above has not been rolled out yet.
