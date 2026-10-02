# Superpowers preamble reapply

**Contract:** v1.0 · **Date:** 2026-09-05 · **Observed entrypoint:** `setup/superpowers-slim-preamble/reapply.sh`

## Purpose and authority

**Default mode:** local change-producing maintenance. Each Monday, restore the local slim preamble and neutral session-start wrapper after a plugin update. Canonical desired content is `setup/superpowers-slim-preamble/SKILL.md`; upstream cache content is an external dependency, not canonical state.

It may copy the desired skill into the latest cached plugin version, save the incoming upstream files beside the desired copy, and apply one guarded hook-line patch. It must not change a hook whose expected line is absent or ambiguous, and must not alter other plugin files.

**Secrets:** none expected.

## Success and evidence

Success is a `SKILL.md: already applied` or `applied` result, a valid JSON hook output, and a zero exit recorded in `~/projects/.session-gc/preamble-reapply.log`. Inspect that log and the versioned `*.orig-*` backup files. The validation proves shell and JSON shape, not that a later session receives the intended preamble.

## Failure, escalation, and gaps

An upstream hook change produces a warning and leaves the hook untouched; cron captures it only in the log. There is no owner notification. This loop is a maintenance dependency on an unversioned cache path, which is a reliability mismatch.
