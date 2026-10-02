# Quarantined session disposition

Quarantined sessions are not bulk-requeued. Requeueing an unchanged transcript
would run the same secret gate again and normally return it to quarantine.

For each existing quarantined session:

1. Run `loom resolve <session-id>`.
2. Review only the detector name and match location. Do not copy matched material
   into issues, logs, commits, or review notes.
3. If the finding is a false positive, resolve it through the CLI and requeue the
   session.
4. If the finding is real, scrub the transcript before requeueing it.
5. Leave the quarantine file's `0600` permissions unchanged.

The 13 sessions identified by the 2026-07-23 investigation remain in this review
queue rather than being automatically retried. The 223 MB transcript
`ebb64c54-f0cc-41ac-a5f2-31d4f56f8086` should be reviewed separately to distinguish
a detector finding from any size-related failure.

`loom pending` now makes this backlog persistent and actionable. It reports the
total count, a detector histogram when detector metadata is available, and the ten
most recently quarantined sessions with quarantine date and file size. Older state
files do not contain detector metadata, so those entries are reported as `unknown`
until resolved.
