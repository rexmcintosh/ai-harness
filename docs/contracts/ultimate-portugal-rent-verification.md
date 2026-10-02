# Ultimate Portugal rent verification

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed entrypoint:** `ultimate-portugal/scripts/verify-rents.mjs`

## Purpose and authority

**Default mode:** report-only verification. Every Tuesday, compare published rent claims with the current Statistics Portugal (INE) release and write a proposal when an editorial decision is needed. Canonical verification cursor is `ultimate-portugal/.rent-verification/state.json`; published source remains human-owned.

It may read site content, INE data, and its own verification state. It may write state and reports under `.rent-verification/`. It must not edit `src/`, commit, publish, or move `lastVerified` without a human review.

**Secrets:** none expected.

## Success and evidence

Success is an exit code 0 only when every applicable cell passes; a changed or unverified source is a surfaced proposal, not a silent success. Inspect `~/projects/.session-gc/rent-verification.log`, `.rent-verification/state.json`, generated reports, and `docs/rent-verification.md`. The script’s `--self-test` fixtures are validation evidence for parser changes, not proof of a live INE check.

## Failure, escalation, and gaps

Exit 1 means unreadable content or source; exit 2 means drift or failed verification. Cron stores the report in a log and has no direct owner notification, so a proposal can be missed. The measurable outcome is strong, but the review handoff is weak.
