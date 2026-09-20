"""The price-table generator: parsing, merging, rendering, and the checks that
stop a stale table from being silently wrong.

No test here touches the network or the real ledger — the catalogue and billing
payloads are injected, and every ledger read goes through a tmp_path database.
"""
import sqlite3

import pytest

from venice_usage import refresh_prices as rp


# ---------------------------------------------------------------- fixtures --
def cell(n):
    """`model_spec.pricing` reports {usd, diem} per field; both carry the same number."""
    return {"usd": n, "diem": n}


def model(mid, pricing, mtype="text"):
    return {"id": mid, "type": mtype, "model_spec": {"pricing": pricing}}


CATALOGUE = {"data": [
    model("claude-opus-4-8", {"input": cell(6), "cache_input": cell(0.6),
                              "cache_write": cell(7.5), "output": cell(30)}),
    model("claude-sonnet-5", {"input": cell(3), "cache_input": cell(0.3),
                              "cache_write": cell(3.75), "output": cell(15)}),
    model("claude-fable-5", {"input": cell(12), "cache_input": cell(1.2),
                             "cache_write": cell(15), "output": cell(60)}),
    model("claude-fable-5-1", {"input": cell(12), "cache_input": cell(0.3),
                               "cache_write": cell(15), "output": cell(60)}),
    model("deepseek-v4-pro", {"input": cell(1.65), "cache_input": cell(0.33),
                              "output": cell(3.301)}),
    model("openai-gpt-56-sol", {"input": cell(2.5), "cache_input": cell(0.25),
                                "cache_write": cell(3.125), "output": cell(12.5),
                                "extended": {"context_token_threshold": 272000,
                                             "input": cell(5), "output": cell(18.75)}}),
    model("gpt-image-2", {"quality": {}, "resolutions": []}, mtype="image"),
]}


def billed(sku, units, amount, rate, ts):
    return {"sku": sku, "units": units, "amount": -amount, "currency": "DIEM",
            "timestamp": ts, "pricePerUnitUsd": rate,
            "inferenceDetails": {"requestId": "r", "promptTokens": 1, "completionTokens": 1}}


BILLING = [
    # claude-* cache writes always carry their TTL in the SKU name.
    billed("claude-opus-4-8-llm-cache-write-5m-mtoken", 0.1, 0.75, 7.5, "2026-09-05T00:00:00Z"),
    billed("claude-sonnet-5-api-llm-cache-write-5m-mtoken", 0.1, 0.375, 3.75, "2026-09-05T00:00:00Z"),
    billed("claude-sonnet-5-api-llm-cache-write-1h-mtoken", 0.1, 0.60, 6.0, "2026-09-06T00:00:00Z"),
    # no other family does; one un-suffixed SKU means one rate, whatever TTL is asked for.
    billed("openai-gpt-56-sol-llm-cache-write-mtoken", 0.1, 0.3125, 3.125, "2026-09-10T00:00:00Z"),
    # a mid-window repricing: the LATEST rate is the live one.
    billed("openai-gpt-56-sol-llm-input-mtoken", 0.1, 0.625, 6.25, "2026-09-03T00:00:00Z"),
    billed("openai-gpt-56-sol-llm-input-mtoken", 0.1, 0.25, 2.5, "2026-09-10T00:00:00Z"),
    # float noise in amount/units must not leak into the table.
    billed("deepseek-v4-flash-api-llm-output-mtoken", 0.612379, 0.168410, 0.275,
           "2026-09-07T00:00:00Z"),
]


# ------------------------------------------------------------------- rates --
def test_a_usd_diem_cell_and_a_bare_number_both_read_as_the_number():
    p = {"input": cell(12), "output": 60}
    assert rp.rate(p, "input") == 12
    assert rp.rate(p, "output") == 60
    assert rp.rate(p, "missing") is None


def test_sku_parses_into_a_model_id_and_a_price_field():
    assert rp.parse_sku("claude-opus-4-8-llm-input-mtoken") == ("claude-opus-4-8", "input")
    assert rp.parse_sku("grok-4-3-llm-cache-input-mtoken") == ("grok-4-3", "cache_read")
    assert rp.parse_sku("claude-sonnet-5-api-llm-cache-write-5m-mtoken") == (
        "claude-sonnet-5-api", "cache_write_5m")
    assert rp.parse_sku("claude-sonnet-5-api-llm-cache-write-1h-mtoken") == (
        "claude-sonnet-5-api", "cache_write_1h")
    assert rp.parse_sku("openai-gpt-56-sol-llm-cache-write-mtoken") == (
        "openai-gpt-56-sol", "cache_write")
    # not an inference SKU at all
    assert rp.parse_sku("search-augmentation") is None


def test_a_billed_alias_resolves_to_its_catalogue_id():
    ids = {m["id"] for m in CATALOGUE["data"]}
    assert rp.resolve_id("claude-sonnet-5-api", ids) == "claude-sonnet-5"
    assert rp.resolve_id("claude-opus-4-8", ids) == "claude-opus-4-8"
    # a billed id with no catalogue entry is reported, never guessed at
    assert rp.resolve_id("deepseek-v4-pro-0813", ids) is None


def test_the_latest_billed_rate_wins_and_the_repricing_is_recorded():
    ids = {m["id"] for m in CATALOGUE["data"]}
    rates, repriced, _ = rp.billed_rates(BILLING, ids)
    assert rates["openai-gpt-56-sol"]["input"] == 2.5      # 2026-09-10, not the 09-03 6.25
    assert ("openai-gpt-56-sol", "input") in {(m, f) for m, f, _ in repriced}


def test_a_billed_rate_is_the_published_number_not_its_float_noise():
    # amount/units here is 0.2750034..., and the table must still say 0.275.
    ids = {"deepseek-v4-flash"}
    rates, _, _ = rp.billed_rates(BILLING, ids)
    assert rates["deepseek-v4-flash"]["output"] == 0.275


# ------------------------------------------------------------------ merging --
def test_catalogue_sets_input_output_and_cache_read():
    row = rp.merge_row(CATALOGUE["data"][0], {})
    assert row["input"] == 6 and row["output"] == 30 and row["cache_read"] == 0.6


def test_cache_rates_are_not_uniform_and_are_copied_not_corrected():
    by = {m["id"]: m for m in CATALOGUE["data"]}
    five = rp.merge_row(by["claude-fable-5"], {})
    five_one = rp.merge_row(by["claude-fable-5-1"], {})
    # Same 12.00 input, cache reads 1.20 vs 0.30. Billed fact, not a typo.
    assert five["input"] == five_one["input"] == 12
    assert five["cache_read"] == 1.2 and five_one["cache_read"] == 0.3


def test_an_observed_ttl_sku_gives_a_ttl_segmented_write_and_no_un_suffixed_one():
    by = {m["id"]: m for m in CATALOGUE["data"]}
    row = rp.merge_row(by["claude-opus-4-8"], {"cache_write_5m": 7.5})
    assert row["cache_write_5m"] == 7.5
    assert "cache_write" not in row


def test_no_one_hour_rate_is_invented_for_a_model_never_billed_one():
    by = {m["id"]: m for m in CATALOGUE["data"]}
    opus = rp.merge_row(by["claude-opus-4-8"], {"cache_write_5m": 7.5})
    sonnet = rp.merge_row(by["claude-sonnet-5"], {"cache_write_5m": 3.75, "cache_write_1h": 6.0})
    assert "cache_write_1h" not in opus          # never derived as 2x input
    assert sonnet["cache_write_1h"] == 6.0       # billed, so recorded


def test_one_un_suffixed_billed_sku_means_one_rate_and_no_ttl_variants():
    by = {m["id"]: m for m in CATALOGUE["data"]}
    row = rp.merge_row(by["openai-gpt-56-sol"], {"cache_write": 3.125})
    assert row["cache_write"] == 3.125
    assert "cache_write_5m" not in row and "cache_write_1h" not in row


def test_a_model_with_no_published_cache_write_gets_none_invented():
    by = {m["id"]: m for m in CATALOGUE["data"]}
    row = rp.merge_row(by["deepseek-v4-pro"], {})
    assert row["input"] == 1.65 and row["output"] == 3.301 and row["cache_read"] == 0.33
    assert not any(k.startswith("cache_write") for k in row)


def test_an_unbilled_model_falls_back_to_the_catalogue_write_with_no_ttl_claim():
    by = {m["id"]: m for m in CATALOGUE["data"]}
    row = rp.merge_row(by["claude-fable-5"], {})
    assert row["cache_write"] == 15          # un-suffixed: TTL segmentation unobserved
    assert "cache_write_5m" not in row


def test_a_model_with_no_token_pricing_yields_no_row():
    by = {m["id"]: m for m in CATALOGUE["data"]}
    assert rp.merge_row(by["gpt-image-2"], {}) is None


def test_an_extended_context_tier_is_recorded_verbatim():
    by = {m["id"]: m for m in CATALOGUE["data"]}
    tiers = rp.extended_tiers(CATALOGUE, ["openai-gpt-56-sol", "claude-opus-4-8"])
    assert tiers["openai-gpt-56-sol"] == {"context_token_threshold": 272000,
                                          "input": 5, "output": 18.75}
    assert "claude-opus-4-8" not in tiers
    assert by  # the tier data comes from the catalogue, untouched


# ------------------------------------------------------------------- wanted --
def _ledger(path, models):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE usage (id INTEGER PRIMARY KEY, model TEXT)")
    conn.executemany("INSERT INTO usage(model) VALUES (?)", [(m,) for m in models])
    conn.commit(); conn.close()


def test_ledger_models_are_read_and_a_missing_ledger_is_not_an_error(tmp_path):
    db = tmp_path / "l.db"
    assert rp.ledger_models(db) == []
    _ledger(db, ["grok-4-3", "grok-4-3", "claude-opus-4-8"])
    assert rp.ledger_models(db) == ["claude-opus-4-8", "grok-4-3"]


def test_the_ledger_is_opened_read_only(tmp_path):
    db = tmp_path / "l.db"
    _ledger(db, ["grok-4-3"])
    conn = rp.open_ledger(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO usage(model) VALUES ('nope')")


def test_wanted_is_the_union_of_the_table_the_ledger_and_the_panels(tmp_path):
    db = tmp_path / "l.db"
    _ledger(db, ["grok-4-3", "gpt-image-2"])
    panels = tmp_path / "panels.toml"
    panels.write_text('[settings]\nchair_model = "claude-opus-4-8"\n'
                      '[[panels.p.members]]\nmodel = "gemini-3-5-flash"\n')
    wanted = rp.wanted_models(existing=["deepseek-v4-pro"], db_path=db,
                              panels_path=panels, extra=["kimi-k3"])
    assert wanted == sorted({"deepseek-v4-pro", "grok-4-3", "gpt-image-2",
                             "claude-opus-4-8", "gemini-3-5-flash", "kimi-k3"})


def test_a_panels_own_chair_is_a_configured_model_too(tmp_path):
    # council lets a panel name its own chair ([panels.<name>] chair_model). It is called
    # and billed like any seat, so it must be priced from its first call as well.
    panels = tmp_path / "panels.toml"
    panels.write_text('[settings]\nchair_model = "claude-opus-4-8"\n'
                      '[panels.p]\nchair_model = "openai-gpt-56-sol"\n'
                      '[[panels.p.members]]\nmodel = "gemini-3-5-flash"\n'
                      '[panels.q]\nchair_model = 42\n')
    assert rp.panel_models(panels) == ["claude-opus-4-8", "gemini-3-5-flash", "openai-gpt-56-sol"]


def test_a_table_never_silently_shrinks(tmp_path):
    # An id already in the committed table stays wanted even with an empty ledger.
    wanted = rp.wanted_models(existing=["claude-opus-4-6"], db_path=tmp_path / "gone.db",
                              panels_path=tmp_path / "gone.toml")
    assert "claude-opus-4-6" in wanted


# ------------------------------------------------------------------ build ---
def _build(**kw):
    return rp.build_table(CATALOGUE, BILLING,
                          wanted=kw.pop("wanted", ["claude-opus-4-8", "claude-sonnet-5",
                                                   "deepseek-v4-pro", "openai-gpt-56-sol",
                                                   "gpt-image-2", "m"]),
                          refreshed_at="2026-09-12", **kw)


def test_build_prices_what_it_can_and_classifies_what_it_cannot():
    t = _build()
    assert t["prices"]["claude-opus-4-8"]["input"] == 6
    # an image model is declared unpriced, not quietly dropped
    assert "gpt-image-2" in t["unpriced"]
    # an id the catalogue has never heard of is named as such
    assert "m" in t["unknown"]
    assert set(t["covers"]) == {"claude-opus-4-8", "claude-sonnet-5", "deepseek-v4-pro",
                                "openai-gpt-56-sol", "gpt-image-2", "m"}


def test_every_covered_id_lands_in_exactly_one_bucket():
    t = _build()
    buckets = [set(t["prices"]), set(t["unpriced"]), set(t["unknown"])]
    for mid in t["covers"]:
        assert sum(mid in b for b in buckets) == 1


def test_build_records_the_billed_receipts_it_used():
    t = _build()
    assert t["billed"]["claude-sonnet-5"]["cache_write_1h"] == 6.0


# ----------------------------------------------------------------- render ---
def test_the_rendered_table_is_importable_python_that_round_trips(tmp_path):
    src = rp.render(_build())
    ns: dict = {}
    exec(compile(src, "price_table.py", "exec"), ns)      # noqa: S102 — that is the test
    assert ns["REFRESHED_AT"] == "2026-09-12"
    assert ns["PRICES"]["claude-opus-4-8"] == {"input": 6, "output": 30,
                                               "cache_read": 0.6, "cache_write_5m": 7.5}
    assert ns["PRICES"]["deepseek-v4-pro"]["output"] == 3.301
    assert "gpt-image-2" in ns["UNPRICED"]
    assert isinstance(ns["STALE_AFTER_DAYS"], int)


def test_the_rendered_table_says_it_is_generated_and_how_to_regenerate_it():
    src = rp.render(_build())
    assert "GENERATED" in src
    assert "refresh_prices" in src


def test_rendering_is_deterministic():
    assert rp.render(_build()) == rp.render(_build())


# ------------------------------------------------------------------- diff ---
def test_the_diff_marks_new_changed_dropped_and_unchanged_rows():
    before = {"keep": {"input": 1, "output": 2},
              "move": {"input": 1, "output": 2},
              "gone": {"input": 9, "output": 9}}
    after = {"keep": {"input": 1, "output": 2},
             "move": {"input": 3, "output": 4},
             "new": {"input": 5, "output": 6}}
    text = rp.describe(before, after)
    assert "  keep" in text and "unchanged" in text
    assert "~ move" in text and "+ new" in text and "- gone" in text


def test_the_diff_shows_the_size_of_a_move():
    text = rp.describe({"m": {"input": 15.0, "output": 75.0}},
                       {"m": {"input": 6.0, "output": 30.0}})
    assert "2.50x" in text          # the old number was 2.5x the live one


# ------------------------------------------------------------------ check ---
def test_check_passes_when_the_committed_table_matches_the_live_catalogue(tmp_path):
    path = tmp_path / "price_table.py"
    table = _build()
    path.write_text(rp.render(table))
    assert rp.check(table, path) == 0


def test_check_fails_loudly_when_the_committed_table_is_stale(tmp_path, capsys):
    path = tmp_path / "price_table.py"
    path.write_text(rp.render(_build()).replace('"input": 6.0,', '"input": 15.0,', 1))
    assert rp.check(_build(), path) == 2
    assert "stale" in capsys.readouterr().err.lower()


def test_check_fails_when_the_table_file_is_missing(tmp_path):
    assert rp.check(_build(), tmp_path / "nope.py") == 2


# --------------------------------------------------- catalogue vs the bills --
def test_a_bill_that_disagrees_with_the_catalogue_is_reported_not_applied():
    # openai-gpt-56-luna was billed 0.26666667 on 2026-09-03; the catalogue now
    # says 0.25. The catalogue is the live price, so the table keeps 0.25 — but a
    # silent divergence is how a stale number survives, so it is named.
    catalogue = {"data": [model("openai-gpt-56-luna",
                                {"input": cell(0.25), "output": cell(1.5)})]}
    rates = {"openai-gpt-56-luna": {"input": 0.26666667, "output": 1.5}}
    found = rp.disagreements(catalogue, rates)
    assert ("openai-gpt-56-luna", "input", 0.25, 0.26666667) in found
    assert not [d for d in found if d[1] == "output"]     # they agree
    assert rp.merge_row(catalogue["data"][0], rates["openai-gpt-56-luna"])["input"] == 0.25


def test_float_noise_alone_is_not_a_disagreement():
    catalogue = {"data": [model("deepseek-v4-flash",
                                {"input": cell(0.138), "output": cell(0.275)})]}
    assert rp.disagreements(catalogue, {"deepseek-v4-flash": {"output": 0.275003}}) == []


def test_the_built_table_carries_its_disagreements():
    t = _build()
    assert "disagreements" in t


def test_anything_this_account_was_billed_for_is_wanted_even_if_the_ledger_missed_it():
    """A model we paid for and cannot price is the same bug as a model in the
    ledger we cannot price — `claude-fable-5-1` is billed here and appears in no
    ledger row, and it is the documented cache-read anomaly."""
    wanted = rp.wanted_models(existing=[], billed=BILLING,
                              catalogue_ids={"claude-opus-4-8", "claude-sonnet-5",
                                             "openai-gpt-56-sol", "deepseek-v4-flash"})
    assert {"claude-opus-4-8", "claude-sonnet-5", "openai-gpt-56-sol",
            "deepseek-v4-flash"} <= set(wanted)


def test_a_billed_id_the_catalogue_cannot_name_is_not_wanted():
    # It would only land in UNKNOWN and add noise; `unresolved` already names it.
    wanted = rp.wanted_models(existing=[], billed=BILLING, catalogue_ids={"claude-opus-4-8"})
    assert "claude-sonnet-5-api" not in wanted


def test_check_ignores_the_evidence_that_legitimately_moves(tmp_path):
    """`--check` is meant to run on a clock. It must fire on a price that moved,
    not on today's date or on one more billed row inside the lookback window."""
    path = tmp_path / "price_table.py"
    path.write_text(rp.render(_build()))
    later = rp.build_table(CATALOGUE, BILLING + [
        billed("claude-opus-4-8-llm-input-mtoken", 1.0, 6.0, 6.0, "2026-09-30T00:00:00Z")],
        wanted=_build()["covers"], refreshed_at="2026-10-30", window="2026-10-09..2026-10-31")
    assert rp.check(later, path) == 0        # same prices, newer receipts


def test_check_still_fires_when_a_price_actually_moves(tmp_path):
    path = tmp_path / "price_table.py"
    path.write_text(rp.render(_build()))
    dearer = {"data": [dict(m, model_spec={"pricing": dict(
        m["model_spec"]["pricing"], input=cell(9))}) if m["id"] == "claude-opus-4-8" else m
        for m in CATALOGUE["data"]]}
    moved = rp.build_table(dearer, BILLING, wanted=_build()["covers"],
                           refreshed_at="2026-09-12")
    assert rp.check(moved, path) == 2


def test_check_fires_when_a_new_model_needs_covering(tmp_path):
    path = tmp_path / "price_table.py"
    path.write_text(rp.render(_build()))
    wider = rp.build_table(CATALOGUE, BILLING,
                           wanted=_build()["covers"] + ["claude-fable-5-1"],
                           refreshed_at="2026-09-12")
    assert rp.check(wider, path) == 2
