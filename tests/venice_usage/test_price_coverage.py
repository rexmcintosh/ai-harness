"""The pin: every model this machine's real ledger has ever recorded must be
priced, or explicitly declared unpriceable.

This is the regression guard for the bug the table was rebuilt to fix — 17 of
the ledger's 21 real models had no row, so `venice-usage report` silently valued
them at zero and an audit shipped with grok, codex and gemini counted as free.

The real ledger is opened READ-ONLY and only ever asked `SELECT DISTINCT model`.
On a machine with no ledger (a fresh checkout, CI) there is nothing to cover and
these skip; the offline half of the same guarantee is in `test_pricing.py`,
which checks `COVERS` against `PRICES`/`UNPRICED`/`UNKNOWN` with no I/O at all.
"""
from pathlib import Path

import pytest

from venice_usage import price_table as pt
from venice_usage.pricing import estimate_usd
from venice_usage.refresh_prices import ledger_models

#: The conftest fixture points $VENICE_USAGE_DB at a tmp file, so name the real
#: path directly rather than going through default_db().
REAL_LEDGER = Path.home() / ".local/state/venice-usage/ledger.db"


def _models():
    if not REAL_LEDGER.exists():
        pytest.skip(f"no ledger at {REAL_LEDGER}")
    found = ledger_models(REAL_LEDGER)
    if not found:
        pytest.skip("ledger has no rows")
    return found


def test_every_model_in_the_real_ledger_is_covered_by_the_table():
    missing = [m for m in _models() if m not in pt.COVERS]
    assert not missing, (
        f"{missing} appear in the ledger and not in the price table. "
        f"Run `python -m venice_usage.refresh_prices --write`.")


def test_no_model_in_the_real_ledger_is_left_silently_unvalued():
    """Every id must be priceable, or named as unpriceable on purpose."""
    declared = set(pt.PRICES) | set(pt.UNPRICED) | set(pt.UNKNOWN)
    unaccounted = [m for m in _models() if m not in declared]
    assert not unaccounted, f"{unaccounted} would sum to zero in a report"


def test_every_priceable_ledger_model_actually_returns_a_number():
    for model in _models():
        if model in pt.PRICES:
            assert estimate_usd(model, 1000, 1000) > 0, model
        else:
            # Declared unpriceable: None, so the caller's own --usd is used and
            # the row is never quietly valued at zero.
            assert estimate_usd(model, 1000, 1000) is None, model


def test_the_ledger_is_never_opened_for_writing_by_this_suite(tmp_path):
    import sqlite3
    from venice_usage.refresh_prices import open_ledger
    if not REAL_LEDGER.exists():
        pytest.skip("no ledger")
    conn = open_ledger(REAL_LEDGER)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO usage(ts,project,task_type,model) "
                     "VALUES ('x','x','x','x')")
