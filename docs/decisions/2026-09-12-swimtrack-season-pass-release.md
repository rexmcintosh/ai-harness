# SwimTrack: Meet Navigator season-pass release worksheet

**Decision date:** 12 September 2026
**Status:** final preparation. No migration, secret, webhook, deployment, checkout change, account action, or charge is authorized by this worksheet.

## Proposed decision

Keep checkout off while the release evidence below is incomplete. The offer remains: Meet Navigator is free for following swimmers, events, heats, and results; the first calendar day of a covered multi-day meet and every single-day meet are free; a EUR5 once-per-September-to-August pass adds the combined personalised schedule after day one for every swimmer on one account; it has no automatic renewal.

This is a recommendation, not the collection-of-money decision. Rex chooses whether to make the EUR5 offer live and whether to commit scarce resources such as a Supabase environment, Stripe setup, paid services, or owner time.

## Exact current local release reference

| Item | Current evidence | Date | Status |
|---|---|---:|---|
| Local release HEAD | `da4da3c7f75ac405cf1b63b0ab07e4b78c9ff9bd` on `main` and `origin/main`; latest commit `Merge branch 'claude/image-engine-ledger'`. | 12 Sep 2026, 07:43:55 UTC | Exact local reference recorded. The working tree has untracked `.agents/` and `.playwright-mcp/`; this worksheet does not treat them as release content. |
| PR #31 | `b844c8b Merge pull request #31 from rexmcintosh/messaging-audit` is in the ancestry of the current HEAD. | 12 Sep Git-history check | Historical uncertainty about PR #31 being unshipped is corrected. Its presence does not prove a deployed service or payment journey. |
| Earlier zero-purchase/no-credential snapshot | Retained canonical snapshot only. | Earlier audit evidence | Historical, not current verification. It cannot establish today's purchases, credentials, or deployment. |

## Complete known prerequisite worksheet

| Prerequisite | Evidence and date | Current status | What it proves, and what it does not |
|---|---|---|---|
| Release-source identity | Exact HEAD above. | Recorded | Identifies source to validate, not deployed Worker revision. |
| Local season unit and route coverage | Recorded `npm test -- src/lib/season`: 55 tests in 10 files passed on 12 Sep. | Passed on the exact HEAD above, rerun at 19:26–19:29 UTC | Covers validation, auth helpers, payment parameters, and route behaviour. Fixtures do not prove remote services. |
| Local migration, RLS and payment sequences | Recorded `npm run test:season-db` passed on 12 Sep in fresh PGlite. | Passed on the exact HEAD above, rerun at 19:26–19:29 UTC | Executes `migrations/20260905_parent_seasons.sql` and checks owner isolation, grants, plan/follow updates, payment/refund sequences. PGlite is not deployed Supabase. |
| Required local release checks | `docs/season-services.md` names unit tests, season-db, `npm run lint:fix`, `npm run lint`, and `npm run build`. | Unit, database, lint and build passed at the exact HEAD | `npm run lint` reported zero errors and zero warnings, with five informational hints. `npm run build` completed; no release or migration ran. The mutation command `lint:fix` was unnecessary because lint passed without edits. |
| Public anonymous capability | Recorded `GET https://swimtrack.ai/api/season/account?seasonId=2026-2027` returned HTTP 200 with `account: true`, `checkout: false` on 12 Sep. | Observed, narrow | Shows sufficient account configuration to advertise `account: true`; checkout was not configured/enabled. Signed-out response returns before plan/pass queries, so it does not prove migration, sign-in, SMTP delivery, RLS, persistence, or Stripe. |
| Installed staging or production migration | SQL exists locally at `migrations/20260905_parent_seasons.sql`; no inspected remote migration history, schema, or table result exists. | Unverified | Read target migration history and schema first. If installed, verify checksum/schema and do not reapply. If absent, apply once through the approved normal path. |
| Supabase account configuration | Public flag above; local code requires `SEASON_SUPABASE_URL` and `SEASON_SUPABASE_ANON_KEY` for `account: true`. | Partly observed, values uninspected | Does not prove service-key custody, Auth callback, database access, or a successful session. |
| Sign-in and email delivery | Local login calls Supabase `signInWithOtp`; code has no separate SMTP service by design. No recorded live email-link delivery exists. | Unverified | Current flags cannot prove delivery, callback exchange, or cookie persistence. |
| Auth callback and cookies | Local contract fixes callback at `/api/season/callback`; route exchanges a code then redirects to `/pt/my-season/`. | Local implementation only | Exact production callback and real email-link/browser session unverified. |
| Account persistence, RLS, two-account isolation | Local PGlite pass above. | Unverified on service | Needs isolated staging service and two test accounts. |
| Stripe credentials and checkout flag | Local code requires account settings, service key, Stripe secret, webhook secret, and `SEASON_CHECKOUT_ENABLED=true`; public flag was `checkout: false`. | Checkout off observed; credentials unverified | Flag does not reveal why checkout is off or prove secret custody. |
| Stripe webhook and real test delivery | Local code verifies raw signatures and replay-safe fulfilment/revocation. No recorded deployed endpoint or Stripe test delivery. | Unverified | Needs test-mode events and retained results. |
| Browser customer journey | Local implementation and tests exist. | Unverified | Needs signed-out/signed-in mobile and desktop checks, save/follow merge, coverage/free states, unavailable, purchase, error, cancellation, refund, and retry paths. |
| Coverage and public-copy honesty | Current source contains covered-meet, free-first, EUR5, non-renewal, and 31 August expiry wording. | Source present; deployed copy unverified | Compare deployed page and actual coverage at release. |
| Production deployment and disabled health check | No inspected deployment revision, migration record, webhook result, or authenticated health evidence. | Unverified | Checkout-off deployment and end-to-end data path remain evidence needs. |

## Scoped release bundle, if Rex approves it

One approved, scoped release bundle can authorize its necessary technical steps. It need not be split into six fresh approvals merely because it includes validation, staging, migration verification, configuration, webhook registration, disabled deployment, and documented checks. The bundle should: tie checks to the exact HEAD; read migration history/schema first; prove the account, two-account/RLS, email-link, browser, and Stripe test paths in isolated staging; return dated results to Rex; then, if Rex approves production technical release, verify production migration state first and perform required one-time steps with checkout still off.

New environments, services, credentials, webhook, and associated time/spend are scarce-resource commitments for Rex. The separate choice to set `SEASON_CHECKOUT_ENABLED=true` and collect money is also Rex's. A technical release bundle never silently makes that money-collection choice.

## Proposed bounded next step and result criteria

Approve one staging-readiness proof, with checkout kept off: at most four hours of implementation/validation work, up to 30 minutes of Rex's time, and no new paid service or live charge. Token use must be recorded within the existing per-run budget; its cash equivalent is not established. This is a proposed allocation, not an allocation already granted. EUR37.50 for the owner half-hour is nominal, not cash spent or saved.

Pass means two isolated test accounts can sign in, keep separate data, save a plan, complete a Stripe test purchase, receive the expected pass, and survive duplicate/refund events with the documented access result. Failure is a reproducible broken step; inconclusive means a required environment or input was unavailable within this scope. In each case, return the evidence and next recommendation to Rex. Do not consume a larger budget automatically. There is no revenue claim from a Stripe test payment.

For a later paid experiment, propose a first tangible cash result within three months: at least one unrelated customer's EUR5 payment received, correct delivered access, and recorded refund/support costs. This tests the ability to earn and fulfil, not a sustainable business. Set any scale threshold with Rex after actual demand and service costs are known. A confirmed charging-without-access defect uses the approved emergency response; routine revenue disappointment returns to Rex and does not silently change direction.

## Case-specific review horizon

If Rex launches the pass, record payments, refunds, access/support failures, covered-meet availability, and Rex time from the actual launch date. Return three-, six-, and twelve-month observations, whether pass, fail, or inconclusive, to Rex. These are proposed review points, not automatic rules to renew, expand, pause, or restart SwimTrack work.

## Rex's decision

The recommendation is to keep checkout off now. Rex may approve a bounded release bundle after choosing scope and resources, then separately decide whether to collect money.
