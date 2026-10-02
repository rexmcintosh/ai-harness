"""The join between a ledger rollup and Venice's invoice.

Nothing here touches the network or the real ledger: the billed rows are
literals and every ledger read goes through a tmp_path database.
"""
import sqlite3

import pytest

from venice_usage import reconcile as rc


def line(sku, amount, ts, rid, pt, ct):
    return {"sku": sku, "units": 0.001, "amount": -amount, "currency": "DIEM",
            "timestamp": ts, "pricePerUnitUsd": 1.0,
            "inferenceDetails": {"requestId": rid, "promptTokens": pt,
                                 "completionTokens": ct}}


BILLED = [
    line("claude-opus-4-8-llm-input-mtoken", 0.60, "2026-09-04T03:10:02.855Z", "a", 100, 20),
    line("claude-opus-4-8-llm-output-mtoken", 0.30, "2026-09-04T03:10:02.864Z", "a", 100, 20),
    # a flat-fee SKU charged to the same call: part of that call's invoice
    line("search-augmentation", 0.01, "2026-09-04T03:10:02.870Z", "a", 100, 20),
    # the ledger logs `deepseek-v4-pro`; the bill logs the route it served on
    line("deepseek-v4-pro-api-llm-input-mtoken", 0.10, "2026-09-04T04:00:00.000Z",
         "b", 200, 30),
]


def test_every_sku_charged_to_a_request_lands_in_that_request_s_invoice():
    reqs = {r["request_id"]: r for r in rc.fold_requests(BILLED)}
    assert reqs["a"]["amount"] == pytest.approx(0.91)      # 0.60 + 0.30 + the 0.01 search
    assert reqs["a"]["model"] == "claude-opus-4-8"
    assert (reqs["a"]["prompt_tokens"], reqs["a"]["completion_tokens"]) == (100, 20)


def test_a_serving_suffix_on_the_bill_is_stripped_to_the_id_the_ledger_logged():
    reqs = {r["request_id"]: r for r in rc.fold_requests(BILLED)}
    assert reqs["b"]["model"] == "deepseek-v4-pro"


def test_a_ledger_row_matches_the_request_with_the_same_model_and_token_counts():
    rows = [{"ts": "2026-09-04T03:10:02", "model": "claude-opus-4-8",
             "tokens_in": 100, "tokens_out": 20, "usd": 1.0}]
    matched, unmatched = rc.match(rows, rc.fold_requests(BILLED))
    assert not unmatched
    assert matched[0][1]["request_id"] == "a"


def test_a_request_is_claimed_once_so_two_identical_calls_are_two_bills():
    rows = [{"ts": "2026-09-04T03:10:02", "model": "claude-opus-4-8",
             "tokens_in": 100, "tokens_out": 20, "usd": 1.0}] * 2
    matched, unmatched = rc.match(rows, rc.fold_requests(BILLED))
    assert len(matched) == 1 and len(unmatched) == 1


def test_a_row_whose_bill_has_not_landed_is_reported_not_dropped():
    rows = [{"ts": "2026-09-12T07:20:05", "model": "claude-opus-4-8",
             "tokens_in": 4758, "tokens_out": 1245, "usd": 0.16}]
    matched, unmatched = rc.match(rows, rc.fold_requests(BILLED))
    assert not matched and len(unmatched) == 1


def test_a_bill_too_far_from_the_row_in_time_is_not_the_same_call():
    rows = [{"ts": "2026-09-04T09:00:00", "model": "claude-opus-4-8",
             "tokens_in": 100, "tokens_out": 20, "usd": 1.0}]
    matched, unmatched = rc.match(rows, rc.fold_requests(BILLED))
    assert not matched and len(unmatched) == 1


def test_the_summary_values_the_ledger_side_from_the_current_table():
    # 1 Mtok into claude-opus-4-8, stored at the old seed's 15.00 estimate.
    row = {"ts": "2026-09-04T03:10:02", "model": "claude-opus-4-8",
           "tokens_in": 1_000_000, "tokens_out": 0, "usd": 15.0}
    pair = [(row, rc.fold_requests(BILLED)[0])]
    assert rc.summarise(pair, [])["ledger"] == pytest.approx(6.0)          # live rate
    assert rc.summarise(pair, [], price_basis="stored")["ledger"] == \
        pytest.approx(15.0)                                                # as written


def test_the_rendered_report_names_the_basis_and_the_drift():
    rows = [{"ts": "2026-09-04T03:10:02", "model": "claude-opus-4-8",
             "tokens_in": 100, "tokens_out": 20, "usd": 1.0}]
    matched, unmatched = rc.match(rows, rc.fold_requests(BILLED))
    text = rc.render(rc.summarise(matched, unmatched), since="2026-09-04", until=None,
                     project="council", price_basis="current")
    assert "prices:" in text and "TOTAL" in text and "claude-opus-4-8" in text


def test_the_ledger_is_read_through_a_read_only_connection(tmp_path):
    db = tmp_path / "l.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE usage (ts TEXT, project TEXT, model TEXT, "
                 "tokens_in INT, tokens_out INT, usd REAL)")
    conn.execute("INSERT INTO usage VALUES ('2026-09-04T00:00:00','council','grok-4-3',1,2,0.1)")
    conn.commit(); conn.close()
    assert rc.read_ledger(db, since="2026-09-01", project="council")[0]["model"] == "grok-4-3"
    assert rc.read_ledger(db, since="2026-09-01", project="loom") == []
