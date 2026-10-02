"""Which prices a report used, and saying so.

The decision this pins: historical `usd` values in the ledger are LEFT AS
WRITTEN. Nothing reprices a stored row. Reports value rows from the current
table on read instead, and every report states which basis it used — a report
that mixes vintages without saying so is exactly how the 2026-09-11 audit
published a 2.5x error.
"""
import json

import pytest

import venice_usage.cli as cli
from venice_usage.ledger import append, connect, query_rollup


def _seed(db):
    # opus was seeded at 15/75 and bills at 6/30; grok had no row at all and so
    # stored NULL; gpt-image-2 is billed per image and carries a real --usd.
    append(project="council", task_type="ask", model="claude-opus-4-8",
           tokens_in=1_000_000, tokens_out=0, usd=15.0,
           ts="2026-07-20T01:00:00", db_path=db)
    # A row logged when grok had no table entry: append() prices on write, so
    # the historical NULL has to be written directly rather than reproduced.
    conn = connect(db)
    conn.execute("INSERT INTO usage(ts,project,task_type,model,tokens_in,tokens_out,usd)"
                 " VALUES ('2026-07-20T02:00:00','council','ask','grok-4-3',1000000,0,NULL)")
    conn.commit(); conn.close()
    append(project="romance", task_type="image", model="gpt-image-2",
           usd=0.40, ts="2026-07-20T03:00:00", db_path=db)


# ------------------------------------------------------- stored is untouched --
def test_a_stored_usd_is_never_rewritten(tmp_path):
    db = tmp_path / "l.db"
    _seed(db)
    stored = connect(db).execute(
        "SELECT model, usd FROM usage ORDER BY ts").fetchall()
    query_rollup(db_path=db, price_basis="current")
    assert connect(db).execute("SELECT model, usd FROM usage ORDER BY ts").fetchall() \
        == stored
    assert stored[0] == ("claude-opus-4-8", 15.0)     # the wrong old estimate, kept


def test_the_stored_basis_reproduces_the_old_numbers_exactly(tmp_path):
    db = tmp_path / "l.db"
    _seed(db)
    rows = {r["project"]: r for r in
            query_rollup(db_path=db, group_by=("project",), price_basis="stored")}
    assert rows["council"]["usd"] == 15.0             # 15 + NULL-as-zero
    assert rows["romance"]["usd"] == 0.40


# ------------------------------------------------------ current reprices on read --
def test_the_current_basis_values_rows_from_the_live_table(tmp_path):
    db = tmp_path / "l.db"
    _seed(db)
    rows = {r["project"]: r for r in
            query_rollup(db_path=db, group_by=("project",), price_basis="current")}
    # 1 Mtok into opus at 6.00 + 1 Mtok into grok at 1.42 — grok was 0 before.
    assert rows["council"]["usd"] == pytest.approx(7.42)


def test_a_model_the_table_cannot_price_keeps_its_operator_supplied_usd(tmp_path):
    db = tmp_path / "l.db"
    _seed(db)
    rows = {r["project"]: r for r in
            query_rollup(db_path=db, group_by=("project",), price_basis="current")}
    # gpt-image-2 is billed per image; repricing it from tokens would say 0.
    assert rows["romance"]["usd"] == 0.40


def test_rows_that_can_be_valued_on_neither_basis_are_counted_not_hidden(tmp_path):
    db = tmp_path / "l.db"
    append(project="p", task_type="t", model="totally-unknown-model",
           tokens_in=500, usd=None, ts="2026-07-20T01:00:00", db_path=db)
    row = query_rollup(db_path=db, group_by=("project",), price_basis="current")[0]
    assert row["usd"] == 0.0
    assert row["unpriced_calls"] == 1


def test_grouping_that_omits_model_still_reprices_per_model(tmp_path):
    db = tmp_path / "l.db"
    _seed(db)
    by_project = query_rollup(db_path=db, group_by=("project",), price_basis="current")
    by_model = query_rollup(db_path=db, group_by=("model",), price_basis="current")
    assert sum(r["usd"] for r in by_project) == pytest.approx(
        sum(r["usd"] for r in by_model))


def test_current_is_the_default_basis(tmp_path):
    db = tmp_path / "l.db"
    _seed(db)
    assert query_rollup(db_path=db, group_by=("project",)) == \
        query_rollup(db_path=db, group_by=("project",), price_basis="current")


def test_an_unknown_basis_is_refused(tmp_path):
    with pytest.raises(ValueError):
        query_rollup(db_path=tmp_path / "l.db", price_basis="vibes")


def test_totals_and_token_counts_are_unaffected_by_the_basis(tmp_path):
    db = tmp_path / "l.db"
    _seed(db)
    a = query_rollup(db_path=db, group_by=("project",), price_basis="stored")
    b = query_rollup(db_path=db, group_by=("project",), price_basis="current")
    strip = lambda rows: [{k: v for k, v in r.items()          # noqa: E731
                           if k not in ("usd", "unpriced_calls")} for r in rows]
    assert sorted(strip(a), key=str) == sorted(strip(b), key=str)


# --------------------------------------------------------------- the report --
def test_the_report_header_states_which_prices_it_used(tmp_path, monkeypatch, capsys):
    db = tmp_path / "l.db"; monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    _seed(db)
    assert cli.main(["report", "--group-by", "project"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("prices:")
    assert "current table" in out


def test_the_report_says_so_when_it_is_reading_stored_values(tmp_path, monkeypatch, capsys):
    db = tmp_path / "l.db"; monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    _seed(db)
    assert cli.main(["report", "--price-basis", "stored"]) == 0
    out = capsys.readouterr().out
    assert "as stored" in out and "mixed vintages" in out


def test_the_json_report_carries_the_basis_as_data(tmp_path, monkeypatch, capsys):
    db = tmp_path / "l.db"; monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    _seed(db)
    assert cli.main(["report", "--group-by", "project", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["price_basis"] == "current"
    assert payload["prices_refreshed_at"]
    assert payload["prices_stale"] in (True, False)
    assert {r["project"] for r in payload["rows"]} == {"council", "romance"}


def test_a_bad_basis_is_rejected_by_the_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "l.db"))
    with pytest.raises(SystemExit):
        cli.main(["report", "--price-basis", "vibes"])


def test_the_report_warns_when_the_table_has_gone_stale(tmp_path, monkeypatch, capsys):
    db = tmp_path / "l.db"; monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    _seed(db)
    import venice_usage.pricing as pricing
    monkeypatch.setattr(pricing, "REFRESHED_AT", "2020-01-01")
    assert cli.main(["report", "--group-by", "project"]) == 0
    assert "STALE" in capsys.readouterr().out
