# Rolling out the CI usage artifact — verified runbook, 2026-09-12

The receiving half shipped on 2026-09-11 (`venice_usage/portable.py`,
`venice-usage export|ingest`, `tools/ci-usage/pull.sh`). This is the sending
half: three edits and a pin bump in each of the 18 repos that run the merge
gate. **Nothing here has been applied to any of the 18 repos.** Applying it is
the operator's call; everything below has been dry-run.

Supersedes the "The workflow change (proposal — NOT applied)" section of
[`ci-usage-durability-2026-09-11.md`](ci-usage-durability-2026-09-11.md), which
was written before two merges landed and carried a `REPLACE_WITH_MERGE_SHA`
placeholder.

---

## 1. The pin, and how it was derived

```
026900319c4deb479d6c9abe01fd537ac5ac09a4
```

Not a number copied from the proposal. Derived from three constraints:

| Constraint | Earliest commit that satisfies it |
|---|---|
| `venice-usage` must be a console script (the export step's command) | `61df8c4` — added `venice_usage` to `[tool.setuptools] packages` and `[project.scripts]` |
| `council.venice` must log usage at all | `d7e93db` — `_log_usage()`; the client had no instrumentation before it |
| `scripts/venice_review.py` as this repo carries it must import cleanly | `ec3df23` — added `Settings.max_completion_tokens` |

`0269003` is `origin/main` today and is an ancestor-superset of all three. It is
reachable on GitHub (verified via the commits API), so `pip install ... @<sha>`
resolves.

**The old pin was worse than "missing a feature".** Every repo currently installs
`4a01298` (council-v0.4.0), whose `pyproject.toml` is:

```toml
[project.scripts]
council = "council.cli:main"
[tool.setuptools]
packages = ["council"]
```

No `venice_usage` package, no `venice-usage` command, and
`git show 4a01298:council/venice.py | grep _log_usage` is empty. So the premise
in the durability doc — "the gate's spend is logged successfully into nothing" —
is half right: on the runner it is not logged at all. **The pin bump is what
turns CI usage logging on; `VENICE_USAGE_DB` + export + upload is what keeps it.**

**The bump is also already overdue for this repo, independently of usage.**
`ec3df23` (2026-09-11) changed `scripts/venice_review.py` here to read
`settings.max_completion_tokens`, which `Settings` at the pinned v0.4.0 does not
have:

```
$ git archive 4a01298 council | tar -x -C /tmp/v040 && cd /tmp/v040
$ python3 -c "from council.config import load_panels; s,_=load_panels(); s.max_completion_tokens"
AttributeError: 'Settings' object has no attribute 'max_completion_tokens'
```

The next PR opened against `build-ai-automation-workflow` fails the gate on that
traceback until the pin moves. The other 17 repos still carry the pre-`ec3df23`
script (`9807ad6`), which works against both pins — `run_pr_review(settings=…)`
and `VeniceClient(max_completion_tokens=…)` are optional at `0269003`, so the
bump is backward compatible for them.

The pin does **not** depend on this branch merging. Nothing the runner needs is
added here.

## 2. Which repos take the shared patch

Re-verified 2026-09-12 against both the local checkouts (`git hash-object`) and
GitHub's contents API (blob `sha`) — the two agree everywhere:

**17 byte-identical** at blob `34b4d7f643871d51ef0ab9f474565226891dda08`, and
`git apply --check` passes on all 17:

```
mcp-configuration-assistant   dump
santa-amaro-home-renovation   finance-tracker
aris-management-website       liam-mobility
blkout-dice-roller            macmcintosh
bubblepop-art                 monthly-bidding
build-ai-automation-workflow  romance-empire
combat-arms-transition        swimtrack-splash-poller
crypto-portfolio              swimtrack
                              ultimate-portugal
```

**1 exception — `swimtrack-website`** (blob `3ce3032…`, 2410 bytes vs 1405). It
adds Node setup, `npm ci`, `npm test`, a season-DB check, a translations check
and a readability check, and its review step already sets `GITHUB_WORKSPACE` and
`COUNCIL_ENFORCE`. The shared patch is correctly *rejected* there
(`git apply --check` fails), which is what makes "apply to 17, hand-edit 1"
safe. Its own patch is
[`proposals/venice-review-usage-artifact-swimtrack-website.patch`](proposals/venice-review-usage-artifact-swimtrack-website.patch)
— same three edits, `VENICE_USAGE_DB` appended to the existing `env:` block.

### Do `swimtrack-website` first

Gate runs in the trailing 7 days, counted with `gh run list --workflow
venice-review.yml` across all 18 repos on 2026-09-12:

```
swimtrack-website             31
aris-management-website        1
build-ai-automation-workflow   1
(the other 15)                 0
TOTAL                         33
```

So ~94% of the spend the artifact is meant to capture comes from the one repo
the shared patch cannot touch. Roll out the hand edit there first and the rest
of the fleet becomes a tidy-up rather than the point.

## 3. Apply

These are **local directory names** under `~/projects` (they differ from the
GitHub names for three repos — see §6):

```bash
PATCH=~/projects/build-ai-automation-workflow/docs/proposals/venice-review-usage-artifact.patch

for dir in MCP-Configuration-Assistant Santa_Amaro_Home_Renovation \
           aris-management-website blkout-dice-roller bubblepop-art \
           build-ai-automation-workflow combat-arms-transition crypto-portfolio \
           dump finance-tracker liam_mobility macmcintosh monthly-bidding \
           romance-empire splash_poller swimtrack ultimate-portugal; do
  ( set -e
    cd ~/projects/"$dir"
    git switch -c claude/ci-usage-artifact main
    git apply --check "$PATCH"            # refuses rather than mangles
    git apply "$PATCH"
    git commit -qam "ci: keep the Venice review council's usage off the runner"
    echo "$dir OK  $(git hash-object .github/workflows/venice-review.yml)"
  ) || echo "$dir FAILED"
done
```

`swimtrack-website` takes
`venice-review-usage-artifact-swimtrack-website.patch` by the same steps.

Branch only. Per the merge protocol, each repo gets its own merge recommendation
and nothing is pushed to a `main` without an explicit go-ahead.

## 4. Verify

**Per repo, before the PR** — the post-patch file is deterministic, so one hash
is the whole check:

```bash
git hash-object .github/workflows/venice-review.yml
# 17 canonical repos  -> 5ebfbefbb10225b6f09a8faac50ff9e55baa8612
# swimtrack-website   -> d1ec9ff6297f10e808b3af805c30c97f17b1022b
```

Anything else means the patch landed on a file that had drifted. Also worth one
line, because a YAML typo here fails the gate for everyone:

```bash
python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/venice-review.yml'))"
```

**Per repo, on the PR.** The gate reviews the PR that changes it, so a green
check is the end-to-end proof that the bumped pin still runs the repo's shim.
Then:

```bash
gh run download <run-id> --repo rexmcintosh/<repo> --pattern 'venice-usage*' --dir /tmp/a
head -1 /tmp/a/venice-usage-1/venice-usage.jsonl   # header line, rows > 0
```

**Fleet, afterwards.** Point the ledger at a throwaway the first time so a
surprise cannot reach production:

```bash
VENICE_USAGE_DB=/tmp/probe.db tools/ci-usage/pull.sh --days 2
# then, for real
tools/ci-usage/pull.sh --days 8
venice-usage report --since <date> --group-by project,model
```

Confirm the CI rows arrived and are attributed, by `ext_id` prefix rather than
by file hash (other sessions write to the ledger continuously):

```sql
SELECT substr(ext_id, 4, instr(substr(ext_id,4), ':')-1) AS repo,
       COUNT(*) FROM usage WHERE ext_id LIKE 'gh:%' GROUP BY repo;
```

Baseline for that query on 2026-09-12: **0 rows**, total ledger 22,859 rows
(romance 8466, loom 6711, swimtrack-website 5850, council 1799,
backlog-runner 25, ultimate-portugal 8).

## 5. Rollback

Revert the repo's commit. The export and upload steps are `if: always()` +
`continue-on-error: true`, so neither can fail a check; the only edit that can
change gate behaviour is the pin, and reverting the commit restores it.

## 6. Fixed here, not in the 18 repos

`tools/ci-usage/repos.txt` named three repos by their `~/projects` directory
name rather than their GitHub name. `gh run list` 404s on those, and `pull.sh`
sent that 404 to `/dev/null` and printed `no runs in window` — the same line a
quiet week produces. Three of eighteen repos' CI spend would have been dropped
with no signal at all.

- `repos.txt`: `Santa_Amaro_Home_Renovation` → `santa-amaro-home-renovation`,
  `liam_mobility` → `liam-mobility`, `splash_poller` → `swimtrack-splash-poller`
  (also `MCP-Configuration-Assistant` → `mcp-configuration-assistant`, which
  worked only because GitHub redirects case).
- `pull.sh`: a repo that cannot be listed is now reported on stderr and makes
  the script exit 1 **after** ingesting everything it could reach.

Live run with the corrected list: all 18 reachable, `exit=0`,
`0 run(s) with usage artifacts` everywhere — correct, because the workflow
change above is not rolled out yet.
