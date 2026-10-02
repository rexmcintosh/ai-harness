# Engage scanner (TikTok comment pilot)

**Contract:** v1.0 · **Date:** 2026-09-09 · **Observed entrypoint:** `romance-empire/scripts/engage-scan.sh heron-creek`

## Purpose and authority

**Default mode:** change-producing digest. Daily at 05:30 UTC, find the TikTok posts worth a comment for the `heron-creek` series, draft each comment in the series voice through the configured Venice models, and write the day's targets as rows in the Notion database "Engage — TikTok pilot" under Launch HQ. The owner posts the comments by hand and flips a Status in Notion. Canonical target and scorecard state is `series/heron-creek/engage/engage-log.yaml` and `engage.yaml` in the Romance Empire repository; Notion is the owner-facing projection.

It may read TikTok data through the scanner's configured fetchers, call Venice for labeling and drafting, create rows in the configured Notion database, and write its own log and YAML state. On the first run with a token present it may create that Notion database once and record its ids in `engage.yaml`. It must not post comments, send messages, or spend when `NOTION_TOKEN` is absent: the wrapper prints a notice and exits 0 without running the scan in that case.

**Secrets, names only:** `NOTION_TOKEN` (the Romance Empire scanner connection, not a general-workspace token), `VENICE_KEY` or `VENICE_API_KEY`, optional `VENICE_ENGAGE_LABEL_MODEL` and `VENICE_ENGAGE_DRAFT_MODEL`. The wrapper sources all of `~/.env`, so observed custody is broader than this list.

## Success and evidence

Success is one dated `=== <timestamp> engage scan heron-creek ===` header in `~/.local/state/engage/scan.log` followed by a `notion: N row(s) created` line and a matching `wrote N target(s) to engage-log.yaml; scorecard refreshed` line, with the same rows visible in Notion. The 2026-09-08 run shows this shape with five rows. A run that prints the missing-token notice and exits 0 is a deliberate no-spend skip, not a success. Proposed outcome measure: the owner posts from the Notion table on the same day and flips a Status, and the repository scorecard shows the result.

## Failure, escalation, and gaps

The wrapper's `flock -n` skips an overlapping run silently. A failed `notion-init` exits 1 and a failed scan exits with the Python status; both land only in the log, which cron appends to. There is no owner notification and no independent escalation channel; a missing Notion digest is the only visible signal. The contract lives outside the owning repository (`romance-empire`), the same ownership gap recorded for the content engines. The setup ritual is `romance-empire/docs/engage-scanner-setup.md`; the design is that repository's 2026-09-06 engage-scanner spec.
