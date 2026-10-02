"""What the price table must be able to say, and what it must refuse to say.

Every figure asserted here was read from Venice's live catalogue on 2026-09-12
(`GET /api/v1/models`) and cross-checked against the billed SKUs for
2026-08-22..2026-09-13 (`GET /billing/usage-history`). None of it is from memory.
"""
import pytest

from venice_usage import price_table as pt
from venice_usage.pricing import (TEXT_PRICES, age_days, basis_line, estimate_usd,
                                  is_stale, price_row)


# ------------------------------------------------------------- the numbers --
def test_the_chair_is_priced_at_what_venice_bills_not_the_old_seed():
    # The seed said 15.00/75.00. Venice bills 6.00/30.00 — the seed was 2.5x high,
    # and that error was published in an audit before it was caught.
    assert price_row("claude-opus-4-8")["input"] == 6.0
    assert price_row("claude-opus-4-8")["output"] == 30.0
    assert estimate_usd("claude-opus-4-8", 1_000_000, 1_000_000) == 36.0


def test_deepseek_pro_is_priced_at_what_venice_bills_not_the_old_seed():
    # The seed said 0.50/2.00. Venice bills 1.65/3.301 — 3.3x LOW on input.
    assert price_row("deepseek-v4-pro")["input"] == 1.65
    assert price_row("deepseek-v4-pro")["output"] == 3.301


def test_the_other_two_seeded_models_were_also_wrong():
    assert (price_row("claude-sonnet-4-6")["input"],
            price_row("claude-sonnet-4-6")["output"]) == (3.6, 18.0)   # seed said 3.0/15.0


def test_rounds_to_six_places():
    assert estimate_usd("claude-opus-4-8", 1000, 0) == round(1000 / 1e6 * 6.0, 6)


def test_an_unknown_model_is_not_guessed_at():
    assert estimate_usd("flux-2-max", 0, 0) is None
    assert price_row("flux-2-max") is None


def test_text_prices_still_exposes_input_output_pairs():
    # Back-compat: the old module's only public data structure still resolves.
    assert TEXT_PRICES["claude-opus-4-8"] == (6.0, 30.0)


# -------------------------------------------------------------- the ledger --
def test_every_model_the_ledger_has_ever_seen_is_accounted_for():
    """The bug this table exists to fix: 17 of the ledger's 21 real models had no
    row at all, so `venice-usage report` valued them at zero."""
    buckets = (set(pt.PRICES), set(pt.UNPRICED), set(pt.UNKNOWN))
    for mid in pt.COVERS:
        hits = [b for b in buckets if mid in b]
        assert len(hits) == 1, f"{mid} is in {len(hits)} buckets, must be in exactly 1"


def test_every_text_model_in_the_ledger_is_priced():
    priced_or_declared = set(pt.PRICES) | set(pt.UNPRICED) | set(pt.UNKNOWN)
    for mid in ("claude-opus-4-8", "claude-opus-4-6", "claude-opus-4-7", "claude-fable-5",
                "claude-sonnet-4-6", "claude-sonnet-5", "deepseek-v4-pro",
                "deepseek-v4-flash", "deepseek-v4-1-flash", "grok-4-3", "kimi-k2-6",
                "openai-gpt-53-codex", "openai-gpt-54", "openai-gpt-56-sol",
                "openai-gpt-56-luna", "gemini-3-1-pro-preview", "zai-org-glm-5",
                "zai-org-glm-5-2"):
        assert mid in pt.PRICES, f"{mid} appears in the ledger and has no price"
        assert mid in priced_or_declared


def test_models_billed_per_asset_are_declared_unpriced_not_dropped():
    # Their real cost is passed with --usd; estimating them from tokens is wrong,
    # but silently omitting them is how a model becomes invisible.
    for mid in ("gpt-image-2", "kling-o3-pro-image-to-video",
                "gemini-omni-flash-1-1-image-to-video"):
        assert mid in pt.UNPRICED
        assert estimate_usd(mid, 1000, 1000) is None


def test_ledger_ids_the_catalogue_never_published_are_named_not_hidden():
    # Test doubles that reached the real ledger. Listed so coverage is provable.
    assert {"m", "test-model", "served-id"} <= set(pt.UNKNOWN)


# --------------------------------------------------------------- the cache --
def test_the_anthropic_family_bills_ttl_segmented_cache_writes():
    # 6.00 = 2.00x its 3.00 input, against 3.75 = 1.25x for the 5m.
    row = price_row("claude-sonnet-5")
    assert row["cache_write_5m"] == 3.75
    assert row["cache_write_1h"] == 6.0
    assert "cache_write" not in row


def test_no_one_hour_rate_is_invented_for_a_model_never_billed_one():
    """A 5m-only biller must never grow a guessed 1h rate.

    Pinning one model went stale: claude-opus-4-8 and claude-fable-5 held this
    role until the 2026-09-17 refresh, when real 1h SKUs showed up on the bills
    for both. So pick whoever currently fits instead of naming a model.
    """
    five_m_only = [m for m, billed in pt.BILLED.items()
                   if "cache_write_5m" in billed and "cache_write_1h" not in billed]
    assert five_m_only, "no 5m-only biller left to check this rule against"
    for model in five_m_only:
        assert "cache_write_1h" not in price_row(model), model


def test_the_one_hour_rates_are_exactly_the_billed_ones():
    """Both directions, and the number itself.

    PRICES -> BILLED catches an invented rate. BILLED -> PRICES catches the
    quieter bug: a real 1h write that never made it into the table, which
    under-values every report that uses it.
    """
    priced = {m for m, row in pt.PRICES.items() if "cache_write_1h" in row}
    billed = {m for m, row in pt.BILLED.items() if "cache_write_1h" in row}
    assert priced == billed
    for model in priced:
        assert pt.PRICES[model]["cache_write_1h"] == pt.BILLED[model]["cache_write_1h"], model


def test_a_model_outside_the_anthropic_family_bills_one_un_suffixed_write():
    row = price_row("openai-gpt-56-sol")
    assert row["cache_write"] == 3.125
    assert "cache_write_5m" not in row and "cache_write_1h" not in row


def test_cache_reads_are_not_uniform_inside_a_family_and_are_not_corrected():
    # Same 12.00 input. fable-5 reads at 0.1x, fable-5-1 at 0.025x. Billed fact.
    five, five_one = price_row("claude-fable-5"), price_row("claude-fable-5-1")
    assert five["input"] == five_one["input"] == 12.0
    assert five["cache_read"] == 1.2
    assert five_one["cache_read"] == 0.3


def test_where_the_catalogue_publishes_no_cache_write_none_is_invented():
    for mid in ("deepseek-v4-pro", "grok-4-3", "openai-gpt-53-codex"):
        row = price_row(mid)
        assert not any(k.startswith("cache_write") for k in row), mid


def test_the_receipts_behind_the_cache_shapes_are_kept():
    assert pt.BILLED["claude-sonnet-5"]["cache_write_1h"] == 6.0
    assert pt.BILLED["openai-gpt-56-sol"]["cache_write"] == 3.125


# ------------------------------------------------------------- staleness ----
def test_the_table_says_when_it_was_refreshed():
    import re
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", pt.REFRESHED_AT)
    assert pt.CATALOGUE_URL.endswith("/models")


def test_age_and_staleness_are_measurable():
    from datetime import date, timedelta
    fresh = date.fromisoformat(pt.REFRESHED_AT)
    assert age_days(today=fresh) == 0
    assert not is_stale(today=fresh)
    assert age_days(today=fresh + timedelta(days=pt.STALE_AFTER_DAYS + 1)) == \
        pt.STALE_AFTER_DAYS + 1
    assert is_stale(today=fresh + timedelta(days=pt.STALE_AFTER_DAYS + 1))


def test_a_report_header_names_the_basis_and_the_vintage():
    from datetime import date, timedelta
    fresh = date.fromisoformat(pt.REFRESHED_AT)
    line = basis_line("current", today=fresh)
    assert "current" in line and pt.REFRESHED_AT in line
    stored = basis_line("stored", today=fresh)
    assert "stored" in stored and "mixed" in stored.lower()
    stale = basis_line("current", today=fresh + timedelta(days=999))
    assert "STALE" in stale


def test_an_unrecognised_basis_is_refused():
    with pytest.raises(ValueError):
        basis_line("vibes")


# ------------------------------------------------------ prices that moved ---
def test_a_repricing_inside_the_billing_window_is_recorded():
    # openai-gpt-56-sol billed 6.25/37.50 on 2026-09-03 and 2.50/12.50 on 09-10.
    moved = {(m, f) for m, f, _ in pt.REPRICED}
    assert ("openai-gpt-56-sol", "input") in moved or \
           ("openai-gpt-56-sol", "output") in moved
