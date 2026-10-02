# Council review coverage and adjudication

Required `council review --diff` ran for the harness, Romance, VPS tools and Attain feedback. The first harness input exceeded the 200,000-byte cap (225,574 bytes). Its middle was truncated. A separate complete bridge/package/document diff review covered every file intersecting the omitted span. No claim of complete coverage rests on the truncated pass alone. Subsequent corrections receive a focused complete diff review.

The shared branch also contains the existing Notion queue commit `37eb484`, merged into this execution branch at `8886f54`. That source already backs a separately installed runtime. Its original [verification and review record](superpowers/plans/2026-09-09-notion-work-queue.md#verification-and-review) records Council findings, fixes, crash recovery review and the then-authorized live smoke. The current direction handoff changes received this package's review and regression tests. This execution did not rerun that historical live smoke or silently install the revised queue.

## Fixed from evidence

- Failed tmux-generation lookup now remains invalid; it cannot fall back to a reused session name.
- Relay state is written before terminal injected state. An interruption does not authorize repeating an injected action.
- Confirmed bridge action history is bounded to 256 records, with offset-based old-update rejection. Uncertain evidence is retained.
- Repeated Telegram rate limits return the documented rejection code. Regression testing also caught Bash collapsing an empty tab field; response classification now uses a delimiter that preserves it. A valid retry delay is observed; the sender's elapsed-time budget is bounded across chunks. Partial accepted messages return uncertain, not a whole-message rejection.
- Watchdog dry run no longer creates delivery/cooldown/metric state. The wrapper serializes passes and saves a send intent before the outbound call. Accepted receipt persistence precedes suppression; interruption resumes local persistence without replaying delivery.
- The VPS poller saves intent before sending, only records suppression after persisted provider acceptance, and serializes the poll. See its scoped review record/tests.
- Romance verifies installed runner capabilities before creating an intent or consuming a session slot. Its exact displayed SHA reaches the immutable-commit approval path.
- Attain feedback does remote reads outside the backlog lock, then checks the source revision and holds the lock only for each conditional update with a five-second request timeout. Changed source evidence cancels remaining stale updates. A dry run cannot push earlier backlog commits.

## Suggestions not adopted blindly

The Council adversary claimed owner-supplied SHA could merge later branch changes. The current-head comparison and immutable-commit merge, verified with actual temporary Git repositories, refute that claim.

Refunding all failed rework invocations would permit uncertain sessions to exceed the resource cap. Refund only a definite no-start signal; preserve uncertain intent and resource use. Cap refusal retains Notion Status/Comment and retries later, as a full-tick test verifies. It does not discard owner feedback.

Feedback's existing lock acquisition fails promptly on contention and has stale-lock handling; the review's claim of an indefinite acquisition wait was incorrect. Missing archive data remains an explicit failure for reconciliation because absence is insufficient to prove the exact linked identity has no archived conflict. The application itself continues; this is not a new feature-work approval gate. The reconciliation function now indexes linked records once, stores active/archive origin explicitly, and tests duplicate IDs and changed source evidence. Per-row API failures allow later rows to proceed within the 30-second write budget and retain a failing exit with exact report/work IDs.

The final harness review asserted an internal retry exit code 4. The actual sender uses code 2 internally, normalizes it to code 1 after a second 429, and a direct fake-provider regression confirms code 1 and exactly two requests. No code-4 path exists in this sender. That finding was rejected on inspected source and observed behavior. Several Security Officer seats returned no substantive analysis, so those seats are not counted as clean security verdicts. Council supplied useful defects and false positives; this package does not represent its output as an automatic approval.

## Execution thrift finding

This package required more integration work than its initial split suggested. The broad first review exceeded its cap, and focused tests alone missed browser Origin behavior and several persistence boundaries. The lesson is to size future repair batches around one connected handoff and run its end-to-end failure test earlier. Do not infer cash savings or time savings from this execution. The global learning/skill integration remains a separately scoped follow-up rather than more machinery added here.
