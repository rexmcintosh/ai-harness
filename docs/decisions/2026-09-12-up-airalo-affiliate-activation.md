# Ultimate Portugal: Airalo affiliate application decision bundle

**Decision date:** 12 September 2026
**Status:** final preparation for Rex's decision. Nothing here applies, creates an account, submits a form, deploys a link, or changes a registry.

## Proposed decision

Rex may choose to apply to the Airalo Affiliate Program through its current Impact application destination. If Rex approves that commercial relationship, an approved application and an approved content release can use the prepared registry and placement below. Until then, Airalo remains an unpaid destination and there is no affiliate URL.

## Evidence, reader fit, and unknowns

The existing Portugal eSIM comparison says an eSIM is sometimes unnecessary, retains Portuguese carriers and rival marketplaces, and identifies Airalo as a convenient option. That makes it a credible application case, not a reason to make Airalo the preferred answer.

`src/data/partners.json` currently has 15 entries. Each has `program.status: "none"`; none has `affiliateUrl`. Airalo is not in that registry. The existing redirect code would use a normal destination until an approved program-issued URL is entered. It counts a real GET best-effort by slug and referring path when its `AFFILIATE_CLICKS` binding is present. That does not prove the binding is present or that traffic was measured.

Traffic is **no measured traffic available in inspected evidence**. The retained Cloudflare query was denied for missing Zone Analytics Read, and the fixed AI-answer benchmark has no answer observations. Cash and Rex time are unknown. These are unknowns, not zeroes.

## Fresh first-party Airalo check, 12 September 2026

The public Portugal shop page checked today lists Portugal coverage on NOS plus two other networks, data-only and Unlimited categories, and these visible Unlimited offers in its displayed USD storefront: 3 days $11.50, 5 days $19.00, 7 days $27.00, 10 days $34.50, 15 days $48.00, and 30 days $69.00. It also offers regional EU-and-UK, Europe, and global products that include Portugal. The page says a plan can be installed before travel and connected on arrival. [Airalo Portugal shop](https://www.airalo.com/pt-BR/portugal-esim)

The product catalogue is storefront-dependent. A separately indexed official Portugal storefront showed Fofo Mobil fixed-data offers, including 1 GB for 7 days at $4.00, while the page above showed Unlimited offers. The bundle therefore does not claim one universal Airalo Portugal price, network, supplier, allowance, or throttle. The proposed reader link must send readers to the live Portugal shop and the comparison must be fact-checked again on its release day.

The prior article's Airalo EUR prices, Portugal-only-Unlimited claim, 3 GB/day high-speed cap and roughly 1 Mbps throttle are **stale comparison facts** until rechecked. The Vodafone €10 promotion explicitly ended on 31 August 2026 and is stale. The August prices and product claims for Holafly, Nomad, Saily, NOS, MEO, Verizon, AT&T, T-Mobile, and UK carriers are also historical observations, not current comparisons. They must not support a new affiliate placement without their own current source check.

## Exact application destination and terms checked today

Airalo's own affiliate page says the program runs through Impact, is free to join, supplies tracking links after approval, and pays its standard 10% commission on final sale value after discounts. The rate can vary by promotional method. Its partner page says travel bloggers and comparison sites are suitable, applications are reviewed, commissions pay on the 28th of the following month after the $15 threshold, and bank or PayPal payment is available. PayPal carries a stated 2% fee. Those pages also say partner discount codes may offer 10% to 15% off. [Airalo affiliate page](https://www.airalo.com/m/resources/airalo-affiliate-program), [Airalo Partners terms and FAQ](https://partners.airalo.com/solutions/affiliates)

**Application destination:** the public `Get started` destination from the Airalo Partners page is `https://app.impact.com/campaign-promo-signup/Airalo-The-Worlds-First-eSIM-store.brand?execution=e1s1`. This is an application page, not an affiliate link and must never be placed in reader content. No form was opened or submitted in this preparation.

Acceptance, final program terms, cookie duration, publisher eligibility, tax treatment, exact payout eligibility, applicable rate, discount-code terms, and any program-issued affiliate URL remain unknown until Rex reviews the terms in the application flow. The quoted terms are dated decision input, not a promise about future payouts.

## Non-deployed activation appendix

### Proposed registry record, not inserted

```json
{
  "slug": "airalo",
  "name": "Airalo",
  "url": "https://www.airalo.com/portugal-esim",
  "domains": ["airalo.com"],
  "program": {
    "network": "Impact",
    "status": "none",
    "notes": "Proposed 2026-09-12. No application submitted and no affiliate URL issued. Portugal shop facts must be rechecked on any release day."
  }
}
```

There is deliberately no `affiliateUrl` field. A public application URL, ordinary Portugal shop URL, coupon, referral code, or guessed parameter is not an affiliate URL. If approved, only the program-issued tracking URL belongs in that field and the status may then become `active` through the normal reviewed release.

### Proposed reader-facing placement and disclosure, not deployed

In `src/content/posts/esim-for-portugal.md`, retain the existing four-provider table and Portuguese SIM section. In the Airalo row only, add this descriptive reader link after factual recheck:

> **Check Airalo's current Portugal plans** → `/go/airalo`

Do not change the comparison verdict, remove the Nomad, Holafly, Saily, Vodafone, NOS, or MEO alternatives, or imply Airalo is cheapest. The existing mechanism would show this disclosure at the end of the post, after the author box and before related content, only when a `/go/` link points to a record with an actual `affiliateUrl`:

> Some links on this page are affiliate links, at no cost to you. This never influences our editorial picks — see our disclosure policy.

That is the proposed disclosure. It must not appear before a paid relationship exists. This bundle deploys neither version.

## Proposed allocation and test criteria

Propose one application and one existing-page placement, conditional on acceptable partner terms and an issued tracking URL. Allow up to 90 minutes of implementation/fact-checking work and 20 minutes of Rex's time; no paid ads, new subscription, or application fee. Record token use within the existing per-run budget. Its actual cash cost remains unknown. The owner's EUR25 nominal time value is not a cash outlay.

For the initial three-month live test, propose this modest success criterion: at least one attributable approved sale, commission received, no material reader correction left unresolved, and a verified route from the page through the issued tracking link. A payout below the partner's threshold remains pending, not received cash. Zero receipts after the agreed observation period is a failed cash test; insufficient eligible traffic or unavailable attribution is inconclusive. Neither proves whether scaling would work.

Return pass, fail, or inconclusive and the next recommendation to Rex. Do not add spend, extend a resource limit, or change editorial preference automatically. Stop using a specific broken or misleading placement while it is repaired under ordinary authority; commercial expansion and any new test budget remain owner decisions. The case does not freeze other work.

## Case-specific observation plan

If Rex approves an application and later a live placement, the test begins on the actual live-link date, not on 12 September. Record source-page fact review, eligible `/go/airalo` GETs, Impact-approved sales, reversals, paid commission, reader corrections, and Rex time if Rex chooses to log it. Pending commission is not cash. `hours × EUR75` is nominal only.

At about three, six, and twelve months after the actual start, return the observed result, including pass, fail, or inconclusive, to Rex with the independent comparison intact. These are proposed review points for this Airalo experiment. They do not automatically add a partner, retire a link, restart a test, or restrict other Ultimate Portugal work.

## Rex's decision

Rex decides whether to apply after reviewing the live Impact terms and this bundle. Acceptance, tracking-link use, and later commercial expansion remain Rex decisions.
