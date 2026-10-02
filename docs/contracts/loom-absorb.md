# Loom session learning

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed entrypoint:** `loom/run-absorb.sh`

## Purpose and authority

**Default mode:** change-producing learning pipeline. At 02:00 UTC, sync the Loom runtime, distill and weave bounded session learnings to the shadow branch, run automated promotion only when its gate permits it, write a briefing payload, and notify Telegram only on failure. Local cron documentation and host configuration confirm UTC scheduling. Canonical learning state is in the Loom data repository and its `loom-shadow` branch; the briefing payload is `loom/pending.json`.

It may modify the shadow branch and automatically promote only gate-approved work. It must not promote a staged `~/.claude` swap, silently drop rejected or deferred learnings, or turn a failure into a success notification.

**Secrets, names only:** `VENICE_API_KEY`, `TELEGRAM_BOT_TOKEN`; the headless Claude session uses its configured account credentials.

Transcript selection is owned by `loom/discovery.py:find_pending` and
`loom/run.py:absorb`: immediate project-directory `*.jsonl` files under
`~/.claude/projects`, not already committed or quarantined; the distill stage
also skips states at or beyond distilled and self-generated Loom calls. The
secret scan quarantines unsafe inputs. Deadlines and configured caps can defer
otherwise pending work; "eligible" is not a promise to drain the entire corpus
in one run.

## Success and evidence

Success is an `absorb rc=0` record, a gate-allowed promotion result where applicable, and an atomically written `loom/pending.json`. Inspect `loom/logs/runs.log`, `loom/logs/runs.log.err`, `loom/logs/cron.log`, `loom/pending.json`, the Loom ledger, and the shadow-branch diff. A completed absorb does not prove a promoted change was useful. Proposed outcome measure: every eligible transcript is distilled or explicitly deferred/quarantined, and every promoted learning is reviewable in the recorded diff.

## Failure, escalation, and gaps

Nonzero absorb or promote results, and an all-failed silent run, attempt a Telegram alert. The runner exits with absorb status even when promotion fails, so inspect both recorded return codes. Runtime promotion authority belongs in Loom’s gate, not in this documentary contract.
