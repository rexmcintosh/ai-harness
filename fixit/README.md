# fixit — feedback → fix → ship

An inbound issue becomes a **PR**, automatically. A constrained agent writes a
minimal fix on a fresh branch; the existing GitHub Actions **council review** gates
the PR; **you merge.** fixit never touches `main` and never merges — it stops exactly
at the project's human-go boundary. (Maps to the autonomous bug-fix loop from Naval's
*AI Industrial Revolution*, kept on the safe side of the merge gate.)

## Run

```bash
./fixit/run-fixit.sh --issue "Title :: longer body"   # dry-run: print the plan
./fixit/run-fixit.sh --file issue.md                  # issue from a file
./fixit/run-fixit.sh                                  # peek the next queued issue
./fixit/run-fixit.sh --issue "..." --run              # actually fix + open a PR
```

**Default is dry-run** — it resolves the issue and prints the plan without running the
agent or opening anything. Add `--run` to execute.

## How a real run works (`--run`)

1. Refuses unless the checkout is clean and on the base branch (`main`) — so it can't
   hijack a feature branch or a linked worktree.
2. Creates `fixit/<issue-id>` off `origin/main`.
3. Runs the constrained fix agent (`prompts/fix.md`) — it may only **Read/Edit/Write/Bash**
   to make a *minimal* fix, add/adjust a test, and **commit**. It is told not to push,
   switch branches, touch `main`, or open a PR.
4. **Gates deterministically (in the shell, not the agent):** there must be a commit on
   the branch, and `pytest` must pass. Either gate failing → no PR; the branch is left
   for inspection.
5. Pushes and `gh pr create`s into `main`. The council CI review runs on the PR. You merge.

## The queue

`fixit/queue.py` is a file-based queue (`queue/{pending,processing,done,failed}`).
Claiming is an atomic `os.rename` (pending → processing) so two runners never grab the
same issue. With no `--issue/--file`, the runner claims the next pending issue and marks
it done (with the PR URL) or failed (with the reason). Unit-tested in
`tests/test_fixit_queue.py`. `queue/` and `logs/` are gitignored.

## Triggering (today vs. later)

Today the trigger is **manual** (`--issue`, `--file`, or enqueue + run) — fixit writes
code, so auto-firing it unattended is deliberately not wired yet. The article's
auto-loop (ingest a bug report → fix → ship) is a later wiring: a Telegram listener or a
CI-failure webhook that calls `add_issue()` then `run-fixit.sh --run`. The substrate is
ready for it.

## First live run

Run the first `--run` from the **main checkout on `main`** (not a worktree), so you can
watch the agent + the council CI gate end to end on a small issue.
```
cd /home/dev/projects/ai-harness && ./fixit/run-fixit.sh --issue "..." --run
```
