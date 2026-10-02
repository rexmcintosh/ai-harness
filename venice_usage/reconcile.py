"""Check a ledger rollup against what Venice actually billed.

    python -m venice_usage.reconcile --since 2026-09-04 --project council

This is the feedback loop whose absence let a 2.5x pricing error reach an audit.
The ledger's `usd` is an estimate; `GET /billing/usage-history` is the invoice.
Nothing else on this box compares the two per model, so nothing else could have
noticed that `claude-opus-4-8` was being valued at 15.00/75.00 against a 6.00 /
30.00 bill while grok, codex and gemini were being counted as free.

How the two sides are joined. The bills carry no project tag, so an account-wide
window cannot be compared to one project's rollup directly. Every billed line
item does carry `inferenceDetails.requestId`, `promptTokens` and
`completionTokens`, and the ledger records the same two token counts and a
second-precision timestamp for each call. So the billed rows are folded into
requests, and each ledger row is matched to the unclaimed request with the same
(model, prompt tokens, completion tokens) nearest it in time. A request is
claimed once. Rows that match nothing are reported, not quietly dropped —
usually the last call of the window, whose bill has not landed yet.

Billed model ids sometimes carry a serving suffix the ledger does not
(`deepseek-v4-pro-api`), which `refresh_prices.resolve_id` already handles.

Backfill: the other side of the join
------------------------------------

Drift is the small problem. The large one is a billed request with *no* ledger
row at all. Usage is recorded client-side, after a response lands, so a session
killed mid-call leaves the spend with no local trace whatsoever — and this host
OOM-kills sessions about seventeen times a month. Two confirmed instances: a
hand-written recovery note for a `claude-fable-5-1` call ("orchestration client
killed before saving response", 2.7549 DIEM), and `claude-fable-5-1` as a
whole — 38.52 DIEM of output over three weeks, the account's dearest model,
appearing in no ledger row at any point.

`--backfill` reconstructs those rows from the invoice. Every reconstructed row
is marked three ways so it can never be mistaken for one the client wrote:

* `ext_id = "venice-bill:<requestId>"` — the row's identity AND its provenance.
  The ledger already carries a UNIQUE partial index on `ext_id`, so this is
  also what makes the operation idempotent for free; `ext_id LIKE
  'venice-bill:%'` is exactly the reconstructed set, the same way `gh:%` is
  exactly the CI set (see `portable.py`). A row the client wrote has
  `ext_id IS NULL`, always.
* `source = "reconcile/billing"` — `source` is already a `report --group-by`
  column, so recovered spend is visible in the ordinary rollup with no schema
  change and no new query to remember.
* `project = "unattributed"` — `/billing/usage-history` carries no key or
  project tag (verified: the line items hold only sku, amount, timestamp and
  `inferenceDetails`). Guessing a project would be inventing data inside the
  audit trail. `--backfill-project` exists for when the operator actually
  knows.

The `usd` column gets the **billed** amount, not a token estimate: the invoice
is the fact, and it includes the cache-write premium Venice injects, which no
estimate from token counts can see. Read those rows with
`report --price-basis stored`.

Idempotency has two independent layers. The UNIQUE `ext_id` index makes a
second write a no-op, and on the next pass the row written by the first pass
matches its own bill, so the request is no longer an orphan at all. Re-running
over an overlapping window is therefore free — which is what lets the cron job
use a window several times wider than its period and still catch bills that
land late.

Reads only, unless `--backfill` is passed. The read path opens the ledger
read-only and Venice is only ever asked for usage history — no chat completion
is made on any path.
"""
from __future__ import annotations

import argparse
import collections
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from .refresh_prices import fetch_billing, open_ledger, parse_sku

#: How a reconstructed row is stamped. Three marks, deliberately: one for
#: identity/idempotency, one that shows up in the everyday rollup, and one that
#: refuses to invent an attribution the invoice does not carry.
BACKFILL_EXT_PREFIX = "venice-bill:"
BACKFILL_SOURCE = "reconcile/billing"
BACKFILL_PROJECT = "unattributed"
BACKFILL_TASK_TYPE = "recovered"

#: How far apart a ledger row's timestamp and its bill may be and still be the
#: same call. Observed maximum on real data is under a second; a minute is slack
#: for clock skew, not a licence to match different calls.
MATCH_WINDOW_SECONDS = 600


def strip_route(model):
    """`deepseek-v4-pro-api` -> `deepseek-v4-pro`. The ledger logs the id it
    asked for; the bill logs the route it was served on."""
    return model[:-4] if model.endswith("-api") else model


#: Kept for anything that imported the private name.
_strip_route = strip_route


def fold_requests(rows):
    """Billed line items -> one record per request: model, tokens, DIEM, when.

    Every SKU charged to a request is summed, flat-fee ones (`search-augmentation`,
    which is billed per web search alongside the tokens) included — the invoice
    for a call is everything it was charged, not just its tokens."""
    by_id: dict[str, dict] = {}
    for r in rows or []:
        details = r.get("inferenceDetails") or {}
        rid = details.get("requestId")
        if not rid:
            continue
        rec = by_id.setdefault(rid, {"request_id": rid, "model": None, "amount": 0.0,
                                     "prompt_tokens": None, "completion_tokens": None,
                                     "ts": None})
        rec["amount"] += abs(float(r.get("amount") or 0.0))
        rec["prompt_tokens"] = details.get("promptTokens")
        rec["completion_tokens"] = details.get("completionTokens")
        parsed = parse_sku(r.get("sku"))
        if parsed and rec["model"] is None:
            rec["model"] = strip_route(parsed[0])
        ts = r.get("timestamp")
        if ts and (rec["ts"] is None or ts < rec["ts"]):
            rec["ts"] = ts
    return list(by_id.values())


def _parse_ts(text):
    return datetime.fromisoformat((text or "").replace("Z", "+00:00")).replace(tzinfo=None)


def match(ledger_rows, requests, window=MATCH_WINDOW_SECONDS):
    """Pair ledger rows with billed requests. Returns (matched, unmatched).

    `matched` is a list of `(ledger_row, request)`; a request is used at most
    once, so two identical calls in the same second cost two bills, not one
    counted twice."""
    buckets = collections.defaultdict(list)
    for req in requests:
        if req["model"] is None or req["ts"] is None:
            continue
        buckets[(req["model"], req["prompt_tokens"], req["completion_tokens"])].append(
            (_parse_ts(req["ts"]), req))
    for pool in buckets.values():
        pool.sort(key=lambda pair: pair[0])

    claimed, matched, unmatched = set(), [], []
    for row in ledger_rows:
        when = _parse_ts(row["ts"])
        best = None
        for req_ts, req in buckets.get((row["model"], row["tokens_in"], row["tokens_out"]), []):
            if id(req) in claimed:
                continue
            gap = abs((req_ts - when).total_seconds())
            if best is None or gap < best[0]:
                best = (gap, req)
        if best and best[0] <= window:
            claimed.add(id(best[1]))
            matched.append((row, best[1]))
        else:
            unmatched.append(row)
    return matched, unmatched


def requests_in_window(requests, start, end):
    """The requests billed inside the operator's window, inclusive.

    The billing fetch is deliberately a day wider on each side, so a call at the
    edge of the window still finds its bill and is not mistaken for an orphan.
    That widening must never reach the rows this writes. It did once: a run for
    `--since 2026-09-11 --until 2026-09-12` wrote 2026-09-10's calls as well and
    reported 39.3229 DIEM of `claude-fable-5-1` where the day's bill was
    22.1627. Each row was right; the scope was two days wide under a one-day
    heading, and a number that cannot be reproduced from the invoice is worth
    nothing in an audit trail.

    A request with no timestamp is kept, not dropped: it cannot be placed in the
    window, so it cannot be excluded by date either, and `backfill` reports it
    as unusable rather than losing it silently."""
    out = []
    for r in requests:
        if not r.get("ts"):
            out.append(r)
            continue
        if start <= _parse_ts(r["ts"]) <= end:
            out.append(r)
    return out


def orphan_requests(requests, matched):
    """Billed requests that no ledger row claims — spend with no local trace.

    Pass the `matched` list from a `match()` over the WHOLE ledger, never over
    one project's slice. A call `loom` logged is not a lost call just because
    the question was about `council`, and backfilling it on top of the row that
    already records it would double-count real money."""
    claimed = {req["request_id"] for _, req in matched}
    return [r for r in requests if r["request_id"] not in claimed]


def backfill_row(req, *, project=BACKFILL_PROJECT):
    """One billed request as ledger-append kwargs, or None if it cannot be
    reconstructed honestly.

    None means the invoice does not say enough: no request id, no timestamp, or
    a SKU that names no model (a request charged only a flat fee such as
    `search-augmentation`). Those are reported, never guessed at — a fabricated
    row in the audit trail is worse than a known gap in it."""
    if not req.get("request_id") or not req.get("model") or not req.get("ts"):
        return None
    return {
        "project": project,
        "task_type": BACKFILL_TASK_TYPE,
        "model": req["model"],
        "tokens_in": int(req.get("prompt_tokens") or 0),
        "tokens_out": int(req.get("completion_tokens") or 0),
        # The invoice, not an estimate: this figure includes the cache-write
        # premium Venice injects, which token counts cannot reveal.
        "usd": round(abs(float(req.get("amount") or 0.0)), 6),
        "source": BACKFILL_SOURCE,
        "ts": _parse_ts(req["ts"]).replace(microsecond=0).isoformat(),
        "ext_id": BACKFILL_EXT_PREFIX + req["request_id"],
    }


def backfill(requests, *, db_path=None, project=BACKFILL_PROJECT, dry_run=False):
    """Append one ledger row per billed request in `requests`.

    Idempotent: `ext_id` is UNIQUE, so `append()` returns 0 for a request that
    is already recorded and it is counted as skipped, not written. Returns the
    counts plus the DIEM recovered, so a cron log line says something useful."""
    from .ledger import append
    out = {"candidates": 0, "written": 0, "skipped": 0, "unusable": 0,
           "diem": 0.0, "by_model": {}, "project": project}
    for req in requests:
        row = backfill_row(req, project=project)
        if row is None:
            out["unusable"] += 1
            continue
        out["candidates"] += 1
        out["diem"] += row["usd"]
        out["by_model"][row["model"]] = round(
            out["by_model"].get(row["model"], 0.0) + row["usd"], 6)
        if dry_run:
            continue
        if append(db_path=db_path, **row):
            out["written"] += 1
        else:
            out["skipped"] += 1
    out["diem"] = round(out["diem"], 6)
    return out


def render_backfill(out, *, dry_run=False, since=None, until=None):
    head = "would recover" if dry_run else "recovered"
    # Name the window on the same line as the total. The over-report this fixes
    # was only invisible because the heading said "2026-09-11" and the figure
    # was two days wide; a total whose window is unstated cannot be checked
    # against the invoice, and an unverifiable number in an audit trail is worse
    # than a gap.
    span = f" in {since or '(start)'}..{until or 'now'}"
    lines = [f"\n{head}{span}: {out['candidates']} billed request(s) with no "
             f"ledger row, {out['diem']:.4f} DIEM"]
    for model, diem in sorted(out["by_model"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {model:38} {diem:10.4f}")
    lines.append(f"  written={out['written']} already-recorded={out['skipped']} "
                 f"unusable={out['unusable']}")
    if not dry_run and out["written"]:
        lines.append(f"  marked: source={BACKFILL_SOURCE} "
                     f"project={out.get('project', BACKFILL_PROJECT)} "
                     f"ext_id={BACKFILL_EXT_PREFIX}<requestId>")
    if out["unusable"]:
        lines.append(f"  {out['unusable']} billed request(s) name no model on any "
                     f"SKU (flat-fee only) and were left alone")
    return "\n".join(lines)


def read_ledger(db_path, *, since=None, until=None, project=None):
    # A ledger that does not exist yet has no rows, which is not an error: it is
    # the exact starting state of the first `--backfill` on a fresh box, and the
    # read-only connect below raises rather than returning empty.
    from pathlib import Path
    if not Path(db_path).exists():
        return []
    sql = "SELECT ts, project, model, tokens_in, tokens_out, usd FROM usage"
    where, params = [], []
    if since:
        where.append("ts >= ?"); params.append(since)
    if until:
        where.append("ts <= ?"); params.append(until)
    if project:
        where.append("project = ?"); params.append(project)
    if where:
        sql += " WHERE " + " AND ".join(where)
    with open_ledger(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql + " ORDER BY ts", params)]


def summarise(matched, unmatched, *, price_basis="current"):
    """Per-model ledger-vs-billed totals for the matched calls."""
    from .pricing import PRICES
    per = collections.defaultdict(lambda: {"calls": 0, "ledger": 0.0, "billed": 0.0})
    for row, req in matched:
        cell = per[row["model"]]
        cell["calls"] += 1
        cell["billed"] += req["amount"]
        rate = PRICES.get(row["model"])
        if price_basis == "current" and rate is not None:
            cell["ledger"] += (row["tokens_in"] / 1e6 * rate["input"]
                               + row["tokens_out"] / 1e6 * rate["output"])
        else:
            cell["ledger"] += row["usd"] or 0.0
    return {"per_model": dict(per),
            "ledger": sum(c["ledger"] for c in per.values()),
            "billed": sum(c["billed"] for c in per.values()),
            "matched": len(matched), "unmatched": len(unmatched)}


def render(summary, *, since, until, project, price_basis):
    from .pricing import basis_line
    out = [f"reconciliation: project={project or '(all)'} since={since} until={until or 'now'}",
           basis_line(price_basis),
           f"matched {summary['matched']} ledger row(s) to a billed request; "
           f"{summary['unmatched']} unmatched",
           "",
           f"{'model':30} {'calls':>6} {'ledger':>10} {'billed':>10} {'delta':>9}"]
    for model in sorted(summary["per_model"],
                        key=lambda m: -summary["per_model"][m]["billed"]):
        cell = summary["per_model"][model]
        delta = (cell["ledger"] / cell["billed"] - 1) * 100 if cell["billed"] else float("nan")
        out.append(f"{model:30} {cell['calls']:6} {cell['ledger']:10.4f} "
                   f"{cell['billed']:10.4f} {delta:+8.2f}%")
    total = (summary["ledger"] / summary["billed"] - 1) * 100 if summary["billed"] else float("nan")
    out.append(f"{'TOTAL':30} {summary['matched']:6} {summary['ledger']:10.4f} "
               f"{summary['billed']:10.4f} {total:+8.2f}%")
    return "\n".join(out)


DESCRIPTION = ("Compare the ledger with what Venice actually billed, and "
               "optionally recover billed calls the ledger never recorded.")


def add_arguments(p):
    """Every flag this command takes, so `venice-usage reconcile` and
    `python -m venice_usage.reconcile` are the same command and cannot drift
    apart. Only reachable as a `python -m` incantation, it was never run."""
    p.add_argument("--since", required=True,
                   help="start of the window (YYYY-MM-DD or an ISO timestamp)")
    p.add_argument("--until", default=None, help="end of the window; default now")
    p.add_argument("--project", default=None,
                   help="restrict the DRIFT comparison to one project. It never "
                        "restricts backfill: orphans are always judged against "
                        "the whole ledger.")
    p.add_argument("--price-basis", default="current", choices=("current", "stored"),
                   dest="price_basis")
    p.add_argument("--db", default=None, help="ledger to use (read-only unless "
                                              "--backfill)")
    p.add_argument("--tolerance", type=float, default=5.0,
                   help="percent the two sides may differ before this exits 2")
    p.add_argument("--backfill", action="store_true",
                   help="append a ledger row for every billed request no ledger "
                        "row claims. Idempotent by request id; rows are marked "
                        f"source={BACKFILL_SOURCE}, ext_id={BACKFILL_EXT_PREFIX}"
                        "<requestId>.")
    p.add_argument("--backfill-project", default=BACKFILL_PROJECT,
                   dest="backfill_project",
                   help="project to file recovered rows under. The invoice "
                        f"carries no project tag, so the default is "
                        f"{BACKFILL_PROJECT!r}; override it only when you know.")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="with --backfill: report what would be recovered, write "
                        "nothing")
    return p


def _window(a):
    """(start, end) as naive UTC datetimes, from the operator's flags.

    Deliberately NOT derived from the ledger rows the way the drift-only path
    used to derive it. The whole point of a backfill is a call the ledger has
    no row for, and a window bounded by the rows that survived cannot see past
    the last one that did."""
    start = _parse_ts(a.since)
    end = _parse_ts(a.until) if a.until else \
        datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    return start, end


def run(a):
    key = os.environ.get("VENICE_ADMIN_KEY")
    if not key:
        print("VENICE_ADMIN_KEY is not set (run: set -a; . ~/.env; set +a)", file=sys.stderr)
        return 1
    from .ledger import default_db
    db_path = a.db or str(default_db())
    rows = read_ledger(db_path, since=a.since, until=a.until, project=a.project)
    backfilling = getattr(a, "backfill", False)
    if not rows and not backfilling:
        print("no ledger rows in that window", file=sys.stderr)
        return 1

    # Widen by a day on each side: a bill can land a moment after the call, and
    # the join is on request identity, not on the window.
    start, end = _window(a)
    wide_start, wide_end = start - timedelta(days=1), end + timedelta(days=1)
    billing = fetch_billing(key, start=wide_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            end=wide_end.strftime("%Y-%m-%dT%H:%M:%SZ"))
    requests = fold_requests(billing)
    matched, unmatched = match(rows, requests)
    summary = summarise(matched, unmatched, price_basis=a.price_basis)
    print(render(summary, since=a.since, until=a.until, project=a.project,
                 price_basis=a.price_basis))
    for row in unmatched:
        print(f"unmatched: {row['ts']} {row['model']} "
              f"{row['tokens_in']}/{row['tokens_out']} (no bill found)", file=sys.stderr)

    if backfilling:
        # Judge orphans against the WHOLE ledger over the widened window, never
        # against `rows` — that list is filtered by --project, and a call
        # another project logged is not a lost call.
        everything = read_ledger(db_path,
                                 since=wide_start.isoformat(timespec="seconds"),
                                 until=wide_end.isoformat(timespec="seconds"))
        claimed, _ = match(everything, requests)
        # Orphans are found over the WIDENED set, so a call just outside the
        # window is claimed by the ledger row just outside it and is not a false
        # orphan. Only then is the set clipped back to the window the operator
        # actually asked for, so the widening can never inflate what is written.
        orphans = requests_in_window(orphan_requests(requests, claimed), start, end)
        out = backfill(orphans, db_path=db_path,
                       project=a.backfill_project, dry_run=a.dry_run)
        print(render_backfill(out, dry_run=a.dry_run, since=a.since, until=a.until))

    if not summary["billed"]:
        print("no billed request matched a ledger row in that window", file=sys.stderr)
        # Recovery is the job when --backfill is on; nothing to compare is not
        # a failure, and a cron line that exits 1 on a quiet night gets muted.
        return 0 if backfilling else 1
    drift = abs(summary["ledger"] / summary["billed"] - 1) * 100
    if drift > a.tolerance:
        print(f"\nledger is {drift:.2f}% away from the bills, over the {a.tolerance:g}% "
              f"tolerance — refresh the price table and look again.", file=sys.stderr)
        return 2
    return 0


def main(argv=None):
    p = add_arguments(argparse.ArgumentParser(
        prog="python -m venice_usage.reconcile", description=DESCRIPTION))
    return run(p.parse_args(sys.argv[1:] if argv is None else list(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
