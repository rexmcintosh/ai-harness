# Memory and scheduler outcome audit

Date: 2026-09-05. Read-only investigation of shared-backlog items
`2026-08-25-wiki-life-first-effect-audit` and
`2026-08-25-diem-backfill-estimate-poisoning-check`.
No learning, ledger, estimate, queue, or scheduled job was changed.

## Question and decision

Does the automation remember more of the owner's life and complete useful work,
or mostly maintain and describe itself?

The life-first change has strongly shifted the destinations of dated learning
entries toward personal topics. Retain it. This does not establish that the
resulting articles are retrieved or improve a decision. Measure those outcomes
before replacing the personal-state layer or expanding its machinery.

The historical late-checkpoint skip was caused directly by an actual long job,
not by an estimate learned after that job. A late-window estimation weakness is
possible, but the current estimate is no longer stuck at that historical value.
Do not apply a historical-outlier fix merely to close the backlog item.

## Wiki target mix

Source: `~/projects/build-ai-automation-workflow/loom/weave_ledger.json`.
Resolve each entry's `commit_sha` against `git -C ~/wiki log --all`, covering
both master and loom-shadow. Partition by commit date before versus on/after
2026-08-23. Soft directories are exactly `people`, `relationships`, `places`,
`companies`, and `philosophies`, matching the existing backlog definition.

Independent parent verification at **2026-09-05 21:26:04 UTC**:

| Date bucket | Dated entries | Soft-target entries | Soft share |
|---|---:|---:|---:|
| Before 2026-08-23 | 4,432 | 317 | 7.1525% |
| On/after 2026-08-23 | 803 | 365 | 45.4545% |
| Undatable | 183 | Not assigned to a date bucket | Not estimated |
| Total | 5,418 | | |

The soft share increased **38.30 percentage points** within the dateable entries.
The backlog's 6.9% historical baseline is close but uses an earlier population;
use the matched 7.15% pre-period for this comparison. These are ledger entries,
not distinct articles or a randomized causal comparison. Commit time is a dating
proxy, not the time the underlying fact was learned. A SHA reachable on a shadow
branch does not alone prove promotion.

An earlier helper snapshot contained 5,413 entries: 4,432 pre-period, 799
post-period, and 182 undatable. Its post soft count was 363 (45.43%). Normal live
work continued during the audit; the later independent measurement agrees on the
substantive result. Do not force the counts to match by pausing or changing jobs.

Three post-policy creations were independently checked by the helper as reachable
from both master and loom-shadow:

| Article | Creation commit | Commit time |
|---|---|---|
| `people/paula-candeias.md` | `05c38b8` | 2026-08-23 14:45:19 UTC |
| `places/baltimore.md` | `f946a79` | 2026-08-23 14:45:50 UTC |
| `companies/allnodes.md` | `c29fe81` | 2026-08-23 14:47:03 UTC |

No article contents are reproduced here.

### Settled-topic exclusion

The helper inspected 4,473 physical learning Markdown files. Joining filename
session stems to ledger IDs and then to commit dates produced 189 post-period
files, 1,591 pre-period files, and 2,693 undatable files. Of the 189 dateable
post-period files, 2 (1.06%) had a `subject:` containing the word `loom`, and
3 (1.59%) mentioned that word anywhere.

The two subjects were `loom-promote-worktree-fix` and
`loom-cron-runtime-isolation`. These name operational topics; a subject alone
does not establish that the same settled fact was repeated.
This small dateable slice supports the intended exclusion but cannot establish
its success across all artifacts. It is not directly comparable to the original
62% baseline without recreating that baseline's sampling method.

### Reproduce the ledger calculation without changing it

```python
import collections
import json
import subprocess
from pathlib import Path

ledger = json.loads((Path.home() / "projects/build-ai-automation-workflow/loom/weave_ledger.json").read_text())
history = subprocess.run(
    ["git", "-C", str(Path.home() / "wiki"), "log", "--all", "--format=%H%x09%cI"],
    capture_output=True, text=True, check=True,
).stdout
dates = dict(line.split("\t") for line in history.splitlines())
soft = {"people", "relationships", "places", "companies", "philosophies"}
counts = collections.Counter()
for entry in ledger.values():
    date = dates.get(entry.get("commit_sha", ""))
    if not date:
        counts["undatable"] += 1
        continue
    bucket = "post" if date[:10] >= "2026-08-23" else "pre"
    counts[bucket] += 1
    if entry.get("target", "").split("/")[0] in soft:
        counts[bucket + "_soft"] += 1
print(dict(counts))
```

## DIEM duration investigation

`diem/state.py` uses an exponentially weighted moving average with alpha 0.3:

`new_estimate = 0.7 * old_estimate + 0.3 * observed_duration`

`diem/drain.py:run_checkpoint` advances elapsed time by the actual completed job's
duration before checking the next job against the deadline. It also records the
new duration estimate. Those are separate effects.

The helper found a **2,965.351-second** backfill in the 2026-08-23 23:00 record.
That consumed about 49.42 of the 50 minutes available before 23:50. Eleven
backfills and one review were then skipped. The learned backfill estimate moved
from about 0.275 seconds to 889.798 seconds (14.83 minutes).

Holding all other constraints constant, that one observation can exclude a later
23:40 job with only ten minutes remaining. Repeated observations of that same
duration cannot by themselves exclude a fresh 23:00 job with fifty minutes
remaining: the moving average approaches 2,965 seconds, below 3,000 seconds.
This statement concerns the duration check only; balance and queue rules can
still prevent work.

| Snapshot | Review estimated cost / seconds | Backfill estimated cost / seconds |
|---|---|---|
| Helper's earlier September 5 read, exact time not recorded | 0.506597 / 54.624 | 0.038648 / 109.253 |
| Parent read of state updated at 21:22 UTC | 0.506597 / 54.624 | 0.163319 / 484.911 |

These are the scheduler's stored cost estimates, not a new billing measurement.
The live values changed during the audit. Both observed backfill duration
estimates fit a fresh ten-minute window by duration alone. No state was edited.

The helper's historical sample after the long job contained 680 later backfills,
of which 616 (90.6%) finished in under one second. Treat these as **fast runs**
until their output confirms no useful work occurred; duration alone is not proof
of a no-op. Their shared estimate with long backfills is a stronger present
investigation target than the August outlier alone.

The parent independently parsed the log and confirmed the 2,965.351-second event
with eleven deadline-skipped backfills and one review. By that later read, the
post-event population was 687 executions with 617 under one second; the log grew
during the audit. The following read-only method reproduces that window:

```python
import json
import re
from pathlib import Path

text = (Path.home() / ".local/state/diem/drain.log").read_text()
decoder = json.JSONDecoder()
records = []
for match in re.finditer(r'^\{\n "aborted"', text, re.M):
    try:
        record, _ = decoder.raw_decode(text, match.start())
        records.append(record)
    except json.JSONDecodeError:
        continue  # incomplete live tail; report that sampling limit
rows = [(record, run) for record in records for run in record.get("ran", [])
        if run.get("type") == "backfill"]
for index, (record, run) in enumerate(rows):
    if abs(run.get("duration_s", 0) - 2965.351) < 0.01:
        later = rows[index + 1:]
        print(record.get("deadline"), len(later),
              sum(run.get("duration_s", 1) < 1 for _, run in later))
```

## Concrete next measurement, before a scheduler change

Target: `~/projects/build-ai-automation-workflow`; inspect `diem/drain.py`,
`diem/state.py`, the runner result schema, and existing DIEM tests. Read the
current archived checkpoint records and job outputs without executing any jobs.

Task: take every archived `type=backfill` run from checkpoints whose recorded
UTC deadline falls in `[2026-08-29T00:00:00Z, 2026-09-05T00:00:00Z)`. Include
failures; exclude non-backfill work, incomplete live-tail records, and records
without a parseable deadline, reporting each exclusion count. Deduplicate only
by an explicit run identifier; otherwise report possible duplicates as a limit.
Do not silently widen the window to obtain a desired result.

Classify each included run from its recorded output: explicit failure or nonzero
exit is `failed`; explicit successful committed/distilled work greater than zero
is `productive`; success with explicit zero work across all reported work stages
is `empty probe`; missing, conflicting, or insufficient output is `unknown`.
Map those concepts to the actual runner result fields and publish that mapping.
Duration alone never establishes the class. Require at least 20 productive runs
with both prediction and observed duration before recommending an estimate
change; below that, report insufficient evidence and preserve current behavior.
This is a minimum diagnostic sample, not a statistical confidence claim. Compare
predicted duration against actual duration for the productive group. Determine
whether empty probes drive a materially misleading productive-work estimate.
Here "material" means a changed deadline decision at a recorded checkpoint:
the estimate admits a job whose observed duration exceeds the available window,
or rejects a job whose observed duration fits. Report both counts and the
available window. If no such cases occur, do not recommend a timing change based
on percentage error alone.

Constraints: no live queue/estimate changes or paid model calls. Recommend a
separate productive-work estimate or conservative recent-duration rule only if
the observed prediction errors justify it. Preserve the real deadline backstop
and the existing no-repeat-filler guard.

Done: publish the sample window, classification counts, prediction errors,
unclassifiable count, and a go/no-go recommendation. Any code change then uses
deterministic fake-runner tests for empty, productive, failed, and deadline cases.

For personal memory usefulness, the next prerequisite is an owner-relevant task
sample: a missed commitment, a repeated explanation, a relationship fact, and a
project decision that should benefit from context. Compare the same tasks with
and without the retrieved context. The existing personal-layer bake-off stays
held until that sample and its owner scoring are agreed; target mix is not a
substitute for the experiment.

Proposed scoring for the owner to approve before that experiment: for each
matched task, record pass/fail on source-grounded factual correctness, task
completion, and staying within authority; count owner corrections and repeated
explanations; measure personal-context tokens loaded. A replacement must have
no new correctness or authority failures, fewer owner corrections overall, and
lower context load on the same task sample. If results tie or conflict, retain
the current layer. The sample size and minimum useful reduction remain an owner
decision before running the bake-off, not an invented result of this audit.
