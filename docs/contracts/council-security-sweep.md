# Council security sweep

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed entrypoint:** `council/scripts/security-sweep.sh`

## Purpose and authority

**Default mode:** report-only scan plus notification. Each Monday, scan the automation-workflow repository with `council sweep`, capped at 40 chunks, and send the chair summary to Telegram. The scan may read its target and call the configured model service. It must not edit code, open a pull request, deploy, or remediate a finding.

The report in `council/logs/sweep.log` is the local execution record. There is no canonical findings store beyond the emitted report and log.

**Secrets, names only:** `VENICE_API_KEY`, `TELEGRAM_BOT_TOKEN`.

## Success and evidence

Success is `rc=0`, a logged target and finding count, and a report containing the coverage or dropped-chunk information. Inspect `council/logs/sweep.log` and `council/logs/sweep.log.err`. A scan result is not evidence that the Telegram summary arrived.

## Failure, escalation, and gaps

The script records a nonzero council exit, but intentionally ignores a Telegram send failure. This is a delivery mismatch: review the log after a missing Monday summary. The job has no independent escalation channel and no contract pointer in crontab.
