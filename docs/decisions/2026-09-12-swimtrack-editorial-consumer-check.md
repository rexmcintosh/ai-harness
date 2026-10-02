# SwimTrack editorial scan: consumer check and proposed use case

**Decision date:** 12 September 2026
**Status:** read-only decision preparation. No workflow, issue, source, schedule, queue, or publication change is made here.

## Recommendation

Rex should consider pausing the daily editorial scan while retaining manual dispatch. The scan creates review material, but the inspected sample has no committed downstream consumer. This is a pause recommendation, not an automatic schedule change and not a gate on other SwimTrack work.

The site writing engine already has its own collection, judging, draft, preview, owner-decision, and publishing route. Do not build an adapter merely to manufacture a consumer.

## Evidence and limit

The workflow is configured for a daily 06:00 UTC trigger; the sampled actual runs occurred later, has a 20-minute limit, reads six RSS feeds, can select up to five candidates, uses Venice inference, uploads a 14-day queue artifact, and may open an issue. Its material is mostly US/global/EU; Portugal-specific FPN and SwimRankings collection is deferred. Actual Venice cost, owner-review minutes, reader reach, and customer value are unknown.

In the inspected trace, candidate IDs `d201cd4b6fdc` and `79ec6d97aab4` were in open Issue #36 from 7 September; `d77ae7ea7bdd` was in open Issue #35 from 5 September; and `ced81900b4b7` was in open Issue #34 from 3 September. Exact-ID searches found no matching archive record or website reference in the inspected SwimTrack and SwimTrack Website repositories. This shows no observed consumer in that route. It does not show that nobody read or used the material elsewhere.

## One-page proposed demand-driven consumer contract

**Name:** Portugal swim-parent decision-support article.
**Status:** **proposed; not scheduled.** No consumer has committed to request or receive this output.

**Known parent need:** SwimTrack's Portugal-facing promise is clarity, context, and confidence around a swimmer's season, not merely raw results. Existing parent material addresses decisions such as responding to a disappointing meet, avoiding result-first pressure, supporting a sibling at long meet days, and understanding a swimmer's coach-led work. Meet Navigator separately supports following events, heats, and results at covered Portuguese meets. These are decision domains, not evidence that the daily scan has supplied them. Source basis: `/home/dev/projects/swimtrack/SPEC.md:10`; `/home/dev/projects/swimtrack-website/src/i18n/ui.json:1075-1076,2059-2060,2187-2188`; and the pending sibling-support article at `/home/dev/projects/swimtrack-website/drafts/pending/what-about-my-other-kids.md`. These are product and editorial sources, not a committed consumer request.

**Demand trigger:** A named website-editorial decision asks for help with one recurring Portugal-parent decision that the existing library does not answer well. The request states the decision, Portuguese relevance, intended reader, and why an existing article or Navigator page is insufficient. A global race recap, ranking update, or vague inspiration item is not this use case.

**Permitted input:** One manually requested scan may surface a source-backed candidate for that decision. An international source may inform a Portugal parent only when the article does not pretend foreign conditions are Portuguese facts.

**Consumer and destination:** The named website editor decides whether the candidate becomes a source note in that specific website editorial decision. If accepted, it follows the existing website route to a reviewable draft and owner publication decision. If declined, record the reason against the candidate. The scan does not publish, send a newsletter, or change Navigator.

**Evidence of use:** Keep the request, candidate ID/source, editorial decision, destination path, date, and result. A queue item, issue, run, token count, or draft alone is not use.

**Result owner:** Rex receives useful, not-useful, or inconclusive results. Neither result automatically restarts a cadence, retires the scan, or changes unrelated work.

## Case-specific review points

If Rex elects to retain a manual test, use its actual first requested scan as the start. Return the consumer check after that one requested scan. If Rex retains a longer trial, describe its tangible-result horizon consistently as within three, six, or twelve months. These proposed experiment limits do not change the current schedule by themselves or impose a blanket free/manual-work rule.

## Rex's decision

Rex chooses whether to approve the daily-trigger pause, retain manual dispatch, or name a committed consumer. The recommended pause preserves the manual option and evidence; it does not delete the workflow.
