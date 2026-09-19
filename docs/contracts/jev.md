# Contract: `jev`, the shared client for TypeSafe's Jev

Version 1, 2026-09-19. Code: `jev/` in this repo. Command: `jev` (pipx, from this repo).
Background and measurements: `docs/jev-assessment-2026-09-18.md`.

## What Jev is for

Jev is a typed judge. It answers three kinds of question about a `state` and nothing else:
Choice (one of a fixed list), Score (a rating on described levels), Noul (the chance of
yes). It cannot write, explain, count, do math, or compare dates. It costs about $0.042
per million input tokens and answers in about 0.6 seconds.

Use it as a **plug-in inside a process**, not as a replacement for a reasoning model:

- a **gate** before an expensive call (drop the obvious junk, skip the obviously clean),
- a **router** (which model, which depth, which panel),
- a **checker** after a model writes (does this claim name a file and a line?),
- a **sorter** for a pile a person works through.

## Rules every caller follows

1. **One door.** Every call goes through `jev.ask` / `jev.try_ask` / `jev.ask_many`
   (Python) or the `jev` command (anything else). No second HTTP client anywhere.
2. **Replay before wiring.** A plug-in goes live only after it was measured on history
   with known answers. Keep the replay script and its results next to the code.
3. **Fail open.** A live process uses `try_ask` (or treats any `error` from the command as
   "no answer") and then does what it did before Jev existed.
4. **Never the last safety check. Never an approval.** Jev may add a hold or a warning.
   It never clears one, and it never presses a key, merges, sends, or spends.
5. **The wording is the instrument.** Jev reads literally. A change to a question, its
   criteria, a threshold, or the model version means a new measurement.
6. **Pinned model.** `jev.MODEL` is an exact version. A reply from any other version is an
   error. A caller with a measured threshold passes its own `model=` (or `--model`) so a
   bump of the shared pin cannot move its line.
7. **Data scope is the owner's.** `jev/scope.py` records it and refuses out-of-scope file
   paths. Redaction (`jev/redact.py`) lowers risk; it never widens the scope. A caller that
   describes a repository's work to Jev (an item's title and prompt, a diff) first asks
   `jev.scope.repo_in_scope(repo, name)`: only the repositories in `IN_SCOPE_REPOS` pass
   (`ai-harness`, `swimtrack`, `swimtrack-website`, `ultimate-portugal`,
   `aris-management-website`; owner decision 2026-09-19). Any other or unknown repository is
   refused, and the caller then makes no call and does what it did before Jev existed.
   Adding a repository is an owner decision.
8. **Name yourself.** `project=` and `task=` are required. They land in the usage ledger.
9. **Tests never reach TypeSafe.** `tests/conftest.py` sets `JEV_DISABLED=1` and a temp
   `JEV_USAGE_LOG` for every test. A test of an enabled path deletes `JEV_DISABLED` and
   passes a fake `transport=`. Another repo that calls Jev needs the same guard in its
   own test setup (this was learned the hard way on 2026-09-18).

## Python

```python
import jev

got = jev.try_ask(
    {"title": title, "summary": summary},
    {"keep": jev.noul("Is this worth an editor's attention?", true="...", false="...")},
    project="ultimate-portugal", task="story-prefilter", model="jev-1.13.0")
if got is None:
    ...            # no answer: carry on as before
p = got["answers"]["keep"]["noul"]
```

`ask` returns `{answers, input_tokens, cost_usd, seconds, model}` and raises `JevError`.
`try_ask` returns `None` on any failure. `ask_many(items, questions, ...)` keeps order and
puts `{"id", "error"}` in place of a failed item. `questions` may be a function of the
item when the options differ per item.

## Any other language

```
echo '{"state": {...}, "questions": {"keep": {"type":"noul","instructions":"..."}}}' \
  | jev ask --project up --task story-prefilter --model jev-1.13.0

jev batch --project up --task story-prefilter --questions q.json < items.jsonl > answers.jsonl
```

One JSON line out per request, in order. A failure is `{"error": "..."}` (exit 1 for
`ask`; in place, exit 0, for `batch`). Never a traceback, never the key.

## Switches and files

| What | How |
|---|---|
| Turn every caller off | `JEV_DISABLED=1` in the environment |
| Key | `TYPESAFE_API_KEY` in the environment, else `~/.env`, read as text |
| Usage ledger | `~/.local/state/jev/usage.jsonl` (`JEV_USAGE_LOG` overrides). Counts and cost only, never content |
| What is it costing | `jev usage --days 7` |
| Is it healthy | `jev doctor` (one tiny live call) |

## Callers (keep this list current)

| Caller | Shape | Status |
|---|---|---|
| `watchdog/jev_shadow.py` | second opinion beside the cron-log regex, log only | live since 2026-09-19, shadow |
| `ultimate-portugal/scripts/prefilter-stories.mjs` | gate before the Opus judge, line 0.1 | live since 2026-09-19; still has its own fetch, moves to `jev batch` next |
| `backlogrun/gate.py` | hold-only gate before a night-runner session, line 0.7, own model pin; asks only about items whose `repo` passes `repo_in_scope` (rule 7), every other item skips the gate | live once merged and reinstalled (2026-09-19) |
| `tools/jev_council/` | offline council experiments | on main; imports the names `watchdog/jev_shadow.py` keeps for it; should move to `import jev` |
