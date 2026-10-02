"""Regenerate `venice_usage/price_table.py` from Venice's live catalogue.

One command, so the next refresh is not a research session:

    python -m venice_usage.refresh_prices            # show the diff, write nothing
    python -m venice_usage.refresh_prices --write    # rewrite price_table.py
    python -m venice_usage.refresh_prices --check    # exit 2 if the table is stale

**Never type a price from memory, and never trust an old bill.** The seed table
this replaced had `claude-opus-4-8` at 15.00/75.00 when Venice bills 6.00/30.00,
and `deepseek-v4-pro` at 0.50/2.00 when Venice bills 1.65/3.301 — wrong by 2.5x
in one direction and 3.3x in the other, on the same four-row table. Seventeen
further models in the ledger had no row at all and were valued at zero.

Where each figure comes from
----------------------------

* **input, output, cache_read** — `GET /api/v1/models`, `model_spec.pricing`
  (`input`, `output`, `cache_input`). The catalogue is the *current* price list;
  a bill is a historical fact that may already have been superseded. Venice
  reprices without notice: `openai-gpt-56-sol` billed 6.25/37.50 on 2026-09-03
  and 2.50/12.50 on 2026-09-10. So the catalogue wins for these three fields and
  a disagreeing bill is reported, not applied.

* **which cache-write SKUs a model actually has** — `GET /billing/usage-history`
  (admin key), never the model's name or vendor. Venice segments cache writes by
  TTL for some models and not others, and only the billed SKU string says which:

      claude-sonnet-5-api-llm-cache-write-5m-mtoken   3.7500  = 1.25x input
      claude-sonnet-5-api-llm-cache-write-1h-mtoken   6.0000  = 2.00x input
      openai-gpt-56-sol-llm-cache-write-mtoken        3.1250  = 1.25x input

  A model billed a TTL-suffixed SKU gets `cache_write_5m` / `cache_write_1h` and
  **only for the TTLs that were actually billed** — no 1-hour rate is derived
  from a multiplier for a model that has never been billed one. A model billed
  one un-suffixed SKU gets a single `cache_write`. A model never billed a cache
  write at all falls back to the catalogue's `cache_write` under the un-suffixed
  key, which claims no TTL segmentation. Where the catalogue publishes no cache
  rate, none is invented and no key is emitted.

* **the rate itself** — `amount / units` per SKU, cross-checked against the
  `pricePerUnitUsd` the same rows carry. They agree to within float noise
  (`0.612379` units for `0.168410` DIEM derives 0.2750034, and the published
  number is 0.275), so the published number is used whenever the two agree to
  0.1% and the derived one otherwise. Where a SKU was repriced inside the
  window, the **latest** rate is the live one and the move is recorded.

One figure that looks wrong and is not: `claude-fable-5-1` reads cache at 0.30
against a 12.00 input (0.025x) while `claude-fable-5`, on the same 12.00 input,
reads at 1.20 (0.1x). The catalogue says so and the bills agree. Do not
"correct" it.

Nothing here writes to the usage ledger. The ledger is opened read-only, purely
to ask which model ids have to be covered.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

MODELS_URL = "https://api.venice.ai/api/v1/models"
BILLING_URL = "https://api.venice.ai/api/v1/billing/usage-history"
TABLE_PATH = Path(__file__).with_name("price_table.py")
PANELS_PATH = Path(__file__).resolve().parent.parent / "council" / "panels.toml"

#: A table older than this is called out in every `venice-usage report` header.
STALE_AFTER_DAYS = 30

#: How far back `--check`/`--write` reads the bills for cache-SKU evidence.
DEFAULT_LOOKBACK_DAYS = 21

#: `<model>-llm-<kind>-mtoken`. `search-augmentation` and other flat-fee SKUs
#: do not match, and are skipped rather than guessed at.
_SKU_RE = re.compile(
    r"^(?P<model>.+?)-llm-(?P<kind>input|output|cache-input|cache-write(?:-5m|-1h)?)-mtoken$")

_FIELD = {
    "input": "input",
    "output": "output",
    "cache-input": "cache_read",
    "cache-write": "cache_write",
    "cache-write-5m": "cache_write_5m",
    "cache-write-1h": "cache_write_1h",
}

_WRITE_FIELDS = ("cache_write", "cache_write_5m", "cache_write_1h")

#: Catalogue fields the bills are allowed to *flag* but never overwrite.
_CATALOGUE_OWNED = ("input", "output", "cache_read")


# --------------------------------------------------------------- catalogue --
def rate(pricing, field):
    """One `model_spec.pricing` field as a number, or None.

    Every cell reports the same figure for `usd` and `diem`; a bare number is
    also accepted because some fields are not wrapped."""
    cellv = (pricing or {}).get(field)
    if isinstance(cellv, dict):
        cellv = cellv.get("diem", cellv.get("usd"))
    try:
        return float(cellv) if cellv is not None and not isinstance(cellv, bool) else None
    except (TypeError, ValueError):
        return None


def merge_row(entry, billed):
    """One catalogue entry plus that model's billed rates -> one table row.

    Returns None when the model has no token pricing at all (image and video
    models are billed per asset, and their real cost is logged with `--usd`)."""
    pricing = (entry or {}).get("model_spec", {}).get("pricing") or {}
    row = {}
    for field in ("input", "output"):
        value = rate(pricing, field)
        if value is None:
            return None
        row[field] = value
    cache_read = rate(pricing, "cache_input")
    if cache_read is not None:
        row["cache_read"] = cache_read
    billed_writes = {f: billed[f] for f in _WRITE_FIELDS if f in (billed or {})}
    if billed_writes:
        # The bills name the SKUs that exist. Record those and nothing else: a
        # TTL a model has never been billed for is not a TTL it is known to have.
        row.update(billed_writes)
    else:
        catalogue_write = rate(pricing, "cache_write")
        if catalogue_write is not None:
            # Published but never billed on this account -> one un-suffixed rate,
            # asserting no TTL segmentation we have not seen.
            row["cache_write"] = catalogue_write
    return row


def extended_tiers(catalogue, wanted):
    """Above-threshold context tiers, copied verbatim from the catalogue.

    Recorded as evidence, not applied: `estimate_usd` prices on the base rates
    because no ledger row has ever crossed one of these thresholds, so there is
    no billed line item to confirm whether the threshold counts prompt tokens or
    the whole conversation. When one does cross, the rate is already here."""
    by_id = {m["id"]: m for m in (catalogue or {}).get("data", [])}
    out = {}
    for mid in wanted:
        ext = ((by_id.get(mid) or {}).get("model_spec", {}).get("pricing") or {}).get("extended")
        if not isinstance(ext, dict):
            continue
        tier = {"context_token_threshold": ext.get("context_token_threshold")}
        for field in ("input", "output", "cache_input", "cache_write"):
            value = rate(ext, field)
            if value is not None:
                tier[_FIELD.get(field.replace("_", "-"), field)] = value
        out[mid] = tier
    return out


# ----------------------------------------------------------------- billing --
def parse_sku(sku):
    """`<model>-llm-<kind>-mtoken` -> (billed model id, price field), or None."""
    m = _SKU_RE.match(sku or "")
    if not m:
        return None
    return m.group("model"), _FIELD[m.group("kind")]


def resolve_id(billed_model, catalogue_ids):
    """A billed model id mapped onto its catalogue id, or None.

    Venice suffixes some billed ids with the serving route (`-api`), and dated
    snapshots (`deepseek-v4-pro-0813`) are billed under ids the catalogue does
    not publish at all. Only the exact and the `-api` forms are accepted; a
    still-unresolved id is reported to the operator, never guessed at."""
    if billed_model in catalogue_ids:
        return billed_model
    if billed_model.endswith("-api") and billed_model[:-4] in catalogue_ids:
        return billed_model[:-4]
    return None


def billed_rates(rows, catalogue_ids):
    """Observed rate per (catalogue model, price field), newest wins.

    Returns `(rates, repriced, unresolved)`: the rates, the (model, field, moves)
    triples for SKUs whose rate changed inside the window, and the billed model
    ids that map onto nothing in the catalogue."""
    seen: dict[tuple[str, str], list] = {}
    unresolved: dict[str, int] = {}
    for r in rows or []:
        parsed = parse_sku(r.get("sku"))
        if not parsed:
            continue
        billed_model, field = parsed
        mid = resolve_id(billed_model, catalogue_ids)
        if mid is None:
            unresolved[billed_model] = unresolved.get(billed_model, 0) + 1
            continue
        seen.setdefault((mid, field), []).append(r)

    rates: dict[str, dict[str, float]] = {}
    repriced = []
    for (mid, field), group in sorted(seen.items()):
        group.sort(key=lambda r: r.get("timestamp") or "")
        stated = [r.get("pricePerUnitUsd") for r in group if r.get("pricePerUnitUsd")]
        distinct = sorted({round(float(s), 6) for s in stated})
        latest = group[-1]
        # `amount / units` is the rate that was really charged; the published
        # `pricePerUnitUsd` on the same row is the same number without the float
        # noise that dividing two rounded quantities leaves behind.
        units = float(latest.get("units") or 0.0)
        derived = round(abs(float(latest.get("amount") or 0.0)) / units, 6) if units else None
        published = float(latest["pricePerUnitUsd"]) if latest.get("pricePerUnitUsd") else None
        if published is not None and derived is not None and published:
            value = published if abs(derived - published) / published < 1e-3 else derived
        else:
            value = published if published is not None else derived
        if value is None:
            continue
        rates.setdefault(mid, {})[field] = value
        if len(distinct) > 1:
            repriced.append((mid, field, distinct))
    return rates, repriced, unresolved


def disagreements(catalogue, rates, tol=0.01):
    """Where a bill and the catalogue name different rates for the same field.

    The catalogue wins — it is the live price list and a bill is history — so
    these are reported, never applied. They matter anyway: `openai-gpt-56-luna`
    was billed 0.26666667 input on 2026-09-03 against a catalogue that now says
    0.25, which is a repricing visible from one call. `tol` is fractional, and
    is set above the float noise that dividing two rounded quantities leaves."""
    entries = {m["id"]: m for m in (catalogue or {}).get("data", [])}
    found = []
    for mid, fields in sorted((rates or {}).items()):
        pricing = (entries.get(mid) or {}).get("model_spec", {}).get("pricing") or {}
        for field in _CATALOGUE_OWNED:
            billed = fields.get(field)
            listed = rate(pricing, "cache_input" if field == "cache_read" else field)
            if billed is None or not listed:
                continue
            if abs(billed - listed) / listed > tol:
                found.append((mid, field, listed, billed))
    return found


# ------------------------------------------------------------------ ledger --
def open_ledger(db_path):
    """The usage ledger, opened READ-ONLY.

    A generator that can write to the ledger is a generator that will one day
    contaminate it — that has already happened once on this box."""
    return sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)


def ledger_models(db_path):
    """Every distinct model id the ledger has ever recorded, sorted.

    A ledger that does not exist yet is not an error: this tool has to run on a
    fresh checkout and in CI, where there is nothing to cover."""
    path = Path(db_path)
    if not path.exists():
        return []
    try:
        with open_ledger(path) as conn:
            return sorted(r[0] for r in conn.execute("SELECT DISTINCT model FROM usage")
                          if r[0])
    except sqlite3.Error:
        return []


def panel_models(panels_path):
    """Model ids configured in council's panels.toml, so a newly seated model is
    priced on its first call rather than after someone notices a zero."""
    path = Path(panels_path)
    if not path.exists():
        return []
    try:
        import tomllib
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a malformed panels file must not block a refresh
        return []
    found = set()
    settings = data.get("settings") or {}
    for key, value in settings.items():
        if key.endswith("_model") and isinstance(value, str):
            found.add(value)
    for panel in (data.get("panels") or {}).values():
        chair = (panel or {}).get("chair_model")       # a panel may name its own chair
        if isinstance(chair, str) and chair.strip():
            found.add(chair.strip())
        for member in (panel or {}).get("members") or []:
            if isinstance(member.get("model"), str):
                found.add(member["model"])
    return sorted(found)


def billed_models(rows, catalogue_ids):
    """Catalogue ids this account has actually been billed for.

    A model we paid for and cannot price is the same bug as a ledger model we
    cannot price, and it is the earlier warning: `claude-fable-5-1` was billed
    38.52 DIEM of output in three weeks while appearing in no ledger row at all.
    Billed ids the catalogue cannot name are left out — `unresolved` reports
    those, and guessing at them would only add noise to UNKNOWN."""
    found = set()
    for r in rows or []:
        parsed = parse_sku(r.get("sku"))
        if not parsed:
            continue
        mid = resolve_id(parsed[0], catalogue_ids)
        if mid:
            found.add(mid)
    return sorted(found)


def wanted_models(*, existing=(), db_path=None, panels_path=None, extra=(),
                  billed=None, catalogue_ids=()):
    """The ids the table must cover: whatever it already covers, plus everything
    the ledger has seen, plus everything the bills show this account paying for,
    plus every seat council is configured to use, plus any `--model` the
    operator named. Never shrinks on its own."""
    ids = set(existing) | set(extra)
    ids |= set(ledger_models(db_path)) if db_path else set()
    ids |= set(panel_models(panels_path)) if panels_path else set()
    ids |= set(billed_models(billed, set(catalogue_ids))) if billed else set()
    return sorted(i for i in ids if i)


# ------------------------------------------------------------------- build --
def build_table(catalogue, billing, *, wanted, refreshed_at=None, window=None):
    """Everything the generated module needs, as plain data."""
    entries = {m["id"]: m for m in (catalogue or {}).get("data", [])}
    rates, repriced, unresolved = billed_rates(billing, set(entries))
    prices, unpriced, unknown = {}, {}, []
    for mid in wanted:
        entry = entries.get(mid)
        if entry is None:
            unknown.append(mid)
            continue
        row = merge_row(entry, rates.get(mid, {}))
        if row is None:
            unpriced[mid] = f"{entry.get('type', 'unknown')} model — no token pricing published"
        else:
            prices[mid] = row
    return {
        "refreshed_at": refreshed_at or date.today().isoformat(),
        "window": window or "",
        "covers": sorted(wanted),
        "prices": dict(sorted(prices.items())),
        "unpriced": dict(sorted(unpriced.items())),
        "unknown": sorted(unknown),
        "extended": extended_tiers(catalogue, [m for m in wanted if m in prices]),
        "billed": {k: dict(sorted(v.items())) for k, v in sorted(rates.items()) if k in prices},
        "repriced": sorted(repriced),
        "disagreements": disagreements(catalogue, rates),
        "unresolved": dict(sorted(unresolved.items())),
    }


# ------------------------------------------------------------------ render --
_HEADER = '''"""GENERATED FILE — do not edit by hand.

Venice model prices, read from the live catalogue at `GET /api/v1/models` and
cross-checked against the billed SKUs at `GET /billing/usage-history`.

Regenerate with:

    python -m venice_usage.refresh_prices --write

Check whether it has gone stale (exit 2 = the live catalogue disagrees):

    python -m venice_usage.refresh_prices --check

`PRICES` is DIEM (== USD on this account) per 1,000,000 tokens. Cache keys are
deliberately uneven and that is the point:

* `cache_write_5m` / `cache_write_1h` — the model is billed TTL-segmented
  cache-write SKUs, and only the TTLs actually seen on a bill are listed. No
  1-hour rate is derived from a multiplier.
* `cache_write` — one un-suffixed rate: either the bills show a single SKU with
  no TTL in its name, or the catalogue publishes a write rate this account has
  never been billed, in which case no TTL segmentation is claimed.
* no `cache_write*` key at all — the catalogue publishes no write rate and none
  was invented.
* `cache_read` likewise varies inside a family: claude-fable-5 reads at 1.20 and
  claude-fable-5-1 at 0.30 on the same 12.00 input. Billed fact, not a typo.

`EXTENDED` records above-threshold context tiers verbatim; `estimate_usd` does
not apply them (no ledger row has ever crossed a threshold, so there is no
billed line item confirming what the threshold counts). `BILLED` is the receipts
the cache shapes above were taken from.
"""
'''


def _fmt(value):
    return json.dumps(value, sort_keys=False, ensure_ascii=False)


def _tuple(values):
    """A real tuple literal, so the annotation and the value agree."""
    items = ", ".join(_fmt(v) if not isinstance(v, (list, tuple)) else _tuple(v)
                      for v in values)
    return f"({items},)" if len(values) == 1 else f"({items})"


def _dict_block(name, annotation, mapping, *, comment=None):
    out = [f"{name}: {annotation} = {{"] if not comment else [comment, f"{name}: {annotation} = {{"]
    for key, value in mapping.items():
        out.append(f"    {_fmt(key)}: {_fmt(value)},")
    out.append("}")
    return "\n".join(out) + "\n"


def render(table):
    """The generated module, as text. Deterministic for a given table."""
    parts = [_HEADER, "from __future__ import annotations\n"]
    parts.append(f"REFRESHED_AT = {_fmt(table['refreshed_at'])}\n"
                 f"CATALOGUE_URL = {_fmt(MODELS_URL)}\n"
                 f"BILLING_URL = {_fmt(BILLING_URL)}\n"
                 f"BILLING_WINDOW = {_fmt(table.get('window', ''))}\n"
                 f"STALE_AFTER_DAYS = {STALE_AFTER_DAYS}\n")
    parts.append(_dict_block(
        "PRICES", "dict[str, dict[str, float]]", table["prices"],
        comment="# DIEM per 1,000,000 tokens."))
    parts.append(_dict_block(
        "UNPRICED", "dict[str, str]", table["unpriced"],
        comment="# Seen in the ledger, not token-priced: the caller logs a real --usd."))
    parts.append(f"# Ledger ids the catalogue has never published — test doubles and\n"
                 f"# retired models. They stay listed so coverage is provably complete.\n"
                 f"UNKNOWN: tuple[str, ...] = {_tuple(table['unknown'])}\n")
    parts.append(f"# Every model id this table was generated to cover.\n"
                 f"COVERS: tuple[str, ...] = {_tuple(table['covers'])}\n")
    parts.append(_dict_block("EXTENDED", "dict[str, dict[str, float]]", table["extended"],
                             comment="# Above-threshold context tiers, recorded not applied."))
    parts.append(_dict_block("BILLED", "dict[str, dict[str, float]]", table["billed"],
                             comment="# Observed rates from /billing/usage-history."))
    parts.append(f"# (model, field, catalogue rate, billed rate) where the bills and the\n"
                 f"# catalogue disagree. The catalogue is what PRICES uses.\n"
                 f"DISAGREEMENTS: tuple[tuple, ...] = {_tuple(table['disagreements'])}\n")
    parts.append(f"# SKUs Venice repriced inside the billing window read above.\n"
                 f"REPRICED: tuple[tuple, ...] = {_tuple(table['repriced'])}\n")
    parts.append(f"# Billed model ids with no catalogue entry to map onto.\n"
                 f"UNRESOLVED: dict[str, int] = {_fmt(table['unresolved'])}\n")
    return "\n".join(parts).rstrip() + "\n"


# -------------------------------------------------------------------- diff --
def _shape(row):
    if row is None:
        return "(new)"
    order = ("input", "cache_read", "cache_write", "cache_write_5m", "cache_write_1h", "output")
    return " ".join(f"{k}={row[k]:g}" for k in order if k in row)


def describe(before, after):
    """What changed, one line per model, for a human to read before `--write`."""
    lines = []
    for mid in sorted(after):
        was, now = before.get(mid), after[mid]
        if was is None:
            lines.append(f"+ {mid:34} {_shape(now)}")
        elif was != now:
            moves = []
            for field in ("input", "output"):
                old, new = was.get(field), now.get(field)
                if old and new and old != new:
                    moves.append(f"{field} {old:g}->{new:g} ({old / new:.2f}x)")
            suffix = ("   [" + ", ".join(moves) + "]") if moves else ""
            lines.append(f"~ {mid:34} {_shape(was)}\n  {'':34} {_shape(now)}{suffix}")
        else:
            lines.append(f"  {mid:34} unchanged")
    for mid in sorted(before):
        if mid not in after:
            lines.append(f"- {mid:34} dropped")
    return "\n".join(lines)


# ------------------------------------------------------------------- check --
#: What `--check` compares. Everything here changes only when the money changes
#: or a model stops being covered. REFRESHED_AT, BILLING_WINDOW, BILLED,
#: REPRICED and DISAGREEMENTS are deliberately excluded: they move with today's
#: date and with whichever bills happen to fall inside the lookback window, and
#: a check that fires on those is a check people learn to ignore.
CHECKED = ("PRICES", "UNPRICED", "UNKNOWN", "COVERS")


def _checked_values(namespace):
    return {name: namespace.get(name) for name in CHECKED}


def check(table, path=TABLE_PATH):
    """0 when the committed table still matches the live catalogue, 2 when it
    does not. Exit code 2 is the thing to wire into cron or CI: a table nobody
    has refreshed is the failure mode that put a 2.5x error into an audit."""
    path = Path(path)
    if not path.exists():
        print(f"venice prices: {path} is missing — the table is stale.", file=sys.stderr)
        return 2
    committed: dict = {}
    try:
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), committed)  # noqa: S102
    except Exception as e:  # noqa: BLE001
        print(f"venice prices: {path} could not be read ({e}) — treating as stale.",
              file=sys.stderr)
        return 2
    fresh: dict = {}
    exec(compile(render(table), "<fresh>", "exec"), fresh)  # noqa: S102
    drifted = [name for name in CHECKED if committed.get(name) != fresh.get(name)]
    if not drifted:
        return 0
    print(f"venice prices: the committed table is stale — {', '.join(drifted)} "
          f"disagree(s) with the live catalogue. Run "
          f"`python -m venice_usage.refresh_prices --write`.", file=sys.stderr)
    return 2


# ------------------------------------------------------------------ fetch ---
def _get(url, key, params=None, timeout=60):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed https host
        return json.loads(resp.read())


def fetch_catalogue(key, get=_get):
    return get(MODELS_URL, key, {"type": "all"})


def fetch_billing(key, *, start, end, get=_get, max_pages=500):
    """Every billed line item in the window.

    CURSOR TRAP: once `cursor` is passed, every other parameter is rejected with
    a 400 — so the filters ride on the first request only."""
    rows, params, pages = [], {"currency": "DIEM", "startTimestamp": start,
                               "endTimestamp": end, "pageSize": 200}, 0
    while pages < max_pages:
        payload = get(BILLING_URL, key, params)
        batch = payload.get("data") or []
        rows.extend(batch)
        pages += 1
        cursor = payload.get("nextCursor")
        if not cursor or not batch:
            break
        params = {"cursor": cursor}          # cursor alone. Nothing else.
    return rows


# -------------------------------------------------------------------- main --
def _load_committed(path):
    """The committed table's PRICES, for the diff. Missing file -> empty."""
    path = Path(path)
    if not path.exists():
        return {}
    ns: dict = {}
    try:
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)  # noqa: S102
    except Exception:  # noqa: BLE001
        return {}
    return ns.get("PRICES", {})


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="python -m venice_usage.refresh_prices",
        description="Regenerate venice_usage/price_table.py from Venice's live catalogue.")
    p.add_argument("--write", action="store_true", help="rewrite the table")
    p.add_argument("--check", action="store_true",
                   help="exit 2 if the committed table disagrees with the catalogue")
    p.add_argument("--model", action="append", default=[], dest="models",
                   help="also cover this model id (repeatable)")
    p.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                   help="how far back to read the bills for cache-SKU evidence")
    p.add_argument("--table", default=str(TABLE_PATH))
    p.add_argument("--db", default=None, help="usage ledger to read model ids from "
                                              "(read-only; default $VENICE_USAGE_DB)")
    a = p.parse_args(sys.argv[1:] if argv is None else list(argv))

    api_key = os.environ.get("VENICE_API_KEY")
    if not api_key:
        print("VENICE_API_KEY is not set (run: set -a; . ~/.env; set +a)", file=sys.stderr)
        return 1
    try:
        catalogue = fetch_catalogue(api_key)
    except Exception as e:  # noqa: BLE001
        print(f"the model catalogue could not be read: {e}", file=sys.stderr)
        return 1

    from datetime import timedelta
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=a.lookback_days + 1)
    window = f"{start.isoformat()}..{end.isoformat()}"
    billing, admin_key = [], os.environ.get("VENICE_ADMIN_KEY")
    if admin_key:
        try:
            billing = fetch_billing(admin_key, start=f"{start.isoformat()}T00:00:00Z",
                                    end=f"{end.isoformat()}T00:00:00Z")
        except Exception as e:  # noqa: BLE001
            print(f"warning: the bills could not be read ({e}); cache-write shapes "
                  f"will fall back to the catalogue", file=sys.stderr)
            window = ""
    else:
        print("warning: VENICE_ADMIN_KEY is not set; cache-write TTL shapes cannot be "
              "confirmed against the bills and fall back to the catalogue", file=sys.stderr)
        window = ""

    from .ledger import default_db
    db_path = a.db or str(default_db())
    table_path = Path(a.table)
    existing = _load_committed(table_path)
    wanted = wanted_models(existing=existing, db_path=db_path,
                           panels_path=PANELS_PATH, extra=a.models, billed=billing,
                           catalogue_ids={m["id"] for m in catalogue.get("data", [])})
    table = build_table(catalogue, billing, wanted=wanted, window=window)

    print(describe(existing, table["prices"]))
    if table["unpriced"]:
        print(f"\nunpriced (real cost logged with --usd): {', '.join(table['unpriced'])}")
    if table["unknown"]:
        print(f"not in the catalogue: {', '.join(table['unknown'])}")
    if table["unresolved"]:
        print(f"billed ids with no catalogue entry: "
              f"{', '.join(f'{k} (n={v})' for k, v in table['unresolved'].items())}")
    for mid, field, listed, billed in table["disagreements"]:
        print(f"catalogue/bill disagreement (catalogue wins): {mid} {field} "
              f"catalogue {listed:g} vs billed {billed:g}")
    for mid, field, values in table["repriced"]:
        print(f"repriced inside {window}: {mid} {field} {values}")

    if a.check:
        return check(table, table_path)
    if not a.write:
        print(f"\nnothing written; pass --write to update {table_path}")
        return 0
    table_path.write_text(render(table), encoding="utf-8")
    print(f"\nwrote {len(table['prices'])} priced model(s) to {table_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
