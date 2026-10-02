"""Pricing for ledger rows, from the generated `price_table` module.

The table is DATA and is generated — never hand-typed:

    python -m venice_usage.refresh_prices --write     # refresh from the catalogue
    python -m venice_usage.refresh_prices --check     # exit 2 if it has gone stale

Why that matters. The four-row seed this replaced priced `claude-opus-4-8` at
15.00/75.00 when Venice bills 6.00/30.00 and `deepseek-v4-pro` at 0.50/2.00 when
Venice bills 1.65/3.301 — 2.5x high on one model and 3.3x low on another — and
had no row at all for seventeen further models the ledger had recorded, so those
summed to zero. A `venice-usage report` built on it valued one week of council
at 83.6 when the bills said 61.1. The numbers here are not editable by hand for
that reason: the only supported way to change them is to re-read the catalogue.

`estimate_usd` prices prompt and completion tokens at the base input/output
rates, which is all the ledger records. It deliberately does NOT model caching:
Venice injects Anthropic prompt caching itself, so part of any prompt may in
fact have been billed as a cache read (0.1x input, or 0.025x for
claude-fable-5-1) or a cache write (1.25x for a 5-minute TTL, 2.00x for an
hour), and the ledger has no column saying which. Measured against
`/billing/usage-history` for council over 2026-09-04..2026-09-12, pricing every
prompt token at the plain input rate lands within about 1.3% of the real bill.
The cache rates are still carried in the table, because a caller that DOES know
its cache split needs them and must not have to look them up by hand.
"""
from __future__ import annotations

from datetime import date

from .price_table import (BILLED, CATALOGUE_URL, COVERS, EXTENDED, PRICES,  # noqa: F401
                          REFRESHED_AT, REPRICED, STALE_AFTER_DAYS, UNKNOWN, UNPRICED)

#: The two bases a usd figure can be on. A report that does not say which is how
#: the 2026-09-11 audit shipped a 2.5x error.
BASES = ("current", "stored")

#: Back-compat: the old module's only public data structure, as a view over the
#: generated table. `(input, output)` in DIEM (== USD here) per 1M tokens.
TEXT_PRICES: dict[str, tuple[float, float]] = {
    model: (row["input"], row["output"]) for model, row in PRICES.items()
}


def price_row(model):
    """Every published rate for one model, or None if it has none.

    The keys are deliberately uneven — see `price_table`'s docstring. Read them
    with `.get()`; do not assume a model has a cache write rate, still less that
    it has TTL-segmented ones."""
    row = PRICES.get(model)
    return dict(row) if row is not None else None


def estimate_usd(model, tokens_in, tokens_out):
    """Value one call from its token counts, or None when the model has no
    token pricing (image and video models, which log a real `--usd` instead)."""
    row = PRICES.get(model)
    if row is None:
        return None
    return round(tokens_in / 1e6 * row["input"] + tokens_out / 1e6 * row["output"], 6)


def age_days(today=None):
    """How many days old the committed table is."""
    today = today or date.today()
    return (today - date.fromisoformat(REFRESHED_AT)).days


def is_stale(today=None):
    """True once the table is older than `STALE_AFTER_DAYS`.

    Age is a hint, not proof: Venice reprices without notice, so a two-day-old
    table can already be wrong. `refresh_prices --check` is the real test — it
    compares against the live catalogue and exits 2 on any disagreement."""
    return age_days(today) > STALE_AFTER_DAYS


def basis_line(basis, today=None):
    """One line naming which prices a report used, for the report's own header.

    Every report prints this. A report that mixes vintages without saying so is
    exactly what produced the bad audit."""
    if basis not in BASES:
        raise ValueError(f"unknown price basis {basis!r}; expected one of {BASES}")
    if basis == "stored":
        return ("prices: as stored in the ledger when each row was logged — mixed "
                "vintages, and rows for unpriced models read as 0")
    age = age_days(today)
    warn = "  *** STALE: run `python -m venice_usage.refresh_prices --write` ***" \
        if is_stale(today) else ""
    return (f"prices: current table, refreshed {REFRESHED_AT} ({age}d ago) from "
            f"{CATALOGUE_URL}{warn}")
