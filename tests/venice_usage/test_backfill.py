"""Recovering spend the ledger never saw.

Usage is written client-side, AFTER a response lands. A session killed mid-call
(this host OOM-kills ~17x/30d) leaves the bill with no local trace at all. The
confirmed hole: `claude-fable-5-1` billed 38.52 DIEM of output across three
weeks and appears in no ledger row — the account's dearest model, tracked by
nothing.

Nothing here touches the network or the real ledger: the billed rows are
literals and every ledger write goes to a tmp_path database.
"""
import sqlite3

import pytest

from venice_usage import reconcile as rc
from venice_usage.ledger import append


def line(sku, amount, ts, rid, pt, ct):
    return {"sku": sku, "notes": "API Inference", "units": 0.001, "amount": -amount,
            "currency": "DIEM", "timestamp": ts, "pricePerUnitUsd": 1.0,
            "inferenceDetails": {"requestId": rid, "promptTokens": pt,
                                 "completionTokens": ct}}


#: One tracked call (`a`, which the ledger logged) and one lost one (`f`, the
#: fable-5-1 shape: input + output + the cache-write premium the token estimate
#: cannot see).
BILLED = [
    line("claude-opus-4-8-llm-input-mtoken", 0.60, "2026-09-07T22:10:00.100Z", "a", 100, 20),
    line("claude-opus-4-8-llm-output-mtoken", 0.30, "2026-09-07T22:10:00.200Z", "a", 100, 20),
    line("claude-fable-5-1-llm-input-mtoken", 1.2000, "2026-09-07T22:15:24.500Z",
         "f", 100_000, 5_000),
    line("claude-fable-5-1-llm-output-mtoken", 0.3000, "2026-09-07T22:15:24.600Z",
         "f", 100_000, 5_000),
    line("claude-fable-5-1-llm-cache-write-5m-mtoken", 1.2549, "2026-09-07T22:15:24.700Z",
         "f", 100_000, 5_000),
]

LOGGED = {"ts": "2026-09-07T22:10:00", "project": "council", "model": "claude-opus-4-8",
          "tokens_in": 100, "tokens_out": 20, "usd": 0.9}


def _requests():
    return rc.fold_requests(BILLED)


def _rows(db):
    if not db.exists():
        return []
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM usage ORDER BY id")]


def test_a_billed_request_no_ledger_row_claims_is_an_orphan():
    reqs = _requests()
    matched, _ = rc.match([LOGGED], reqs)
    orphans = rc.orphan_requests(reqs, matched)
    assert [o["request_id"] for o in orphans] == ["f"]


def test_a_backfilled_row_carries_the_billed_amount_not_a_token_estimate(tmp_path):
    db = tmp_path / "l.db"
    orphans = rc.orphan_requests(_requests(), rc.match([LOGGED], _requests())[0])
    rc.backfill(orphans, db_path=db)
    row = _rows(db)[0]
    # 1.2000 + 0.3000 + 1.2549 — the cache-write premium included, because the
    # invoice is the fact and the token estimate cannot see it.
    assert row["usd"] == pytest.approx(2.7549)
    assert (row["model"], row["tokens_in"], row["tokens_out"]) == \
        ("claude-fable-5-1", 100_000, 5_000)
    assert row["ts"] == "2026-09-07T22:15:24"


def test_a_backfilled_row_is_marked_so_it_cannot_pass_as_client_written(tmp_path):
    db = tmp_path / "l.db"
    append(project="council", task_type="ask", model="claude-opus-4-8",
           tokens_in=100, tokens_out=20, source="council/venice", db_path=db)
    orphans = rc.orphan_requests(_requests(), rc.match([LOGGED], _requests())[0])
    rc.backfill(orphans, db_path=db)
    written = {r["source"]: r for r in _rows(db)}
    client, recovered = written["council/venice"], written[rc.BACKFILL_SOURCE]
    # Three independent marks, so no sloppy query mistakes one for the other.
    assert client["ext_id"] is None
    assert recovered["ext_id"] == "venice-bill:f"
    assert recovered["project"] == rc.BACKFILL_PROJECT == "unattributed"
    assert recovered["task_type"] == rc.BACKFILL_TASK_TYPE


def test_backfilling_twice_writes_the_row_once(tmp_path):
    db = tmp_path / "l.db"
    orphans = rc.orphan_requests(_requests(), rc.match([LOGGED], _requests())[0])
    first = rc.backfill(orphans, db_path=db)
    second = rc.backfill(orphans, db_path=db)
    assert first["written"] == 1 and second["written"] == 0
    assert second["skipped"] == 1
    assert len(_rows(db)) == 1


def test_the_second_pass_no_longer_sees_a_backfilled_request_as_an_orphan(tmp_path):
    db = tmp_path / "l.db"
    reqs = _requests()
    rc.backfill(rc.orphan_requests(reqs, rc.match([LOGGED], reqs)[0]), db_path=db)
    ledger = rc.read_ledger(db, since="2026-09-01")
    again = rc.orphan_requests(reqs, rc.match(ledger, reqs)[0])
    assert "f" not in [o["request_id"] for o in again]


def test_a_row_logged_by_another_project_still_claims_its_bill(tmp_path):
    """The double-count footgun: orphans must be judged against the WHOLE
    ledger, never one project's slice, or `--project council` would backfill a
    call loom already recorded."""
    db = tmp_path / "l.db"
    append(project="loom", task_type="weave", model="claude-fable-5-1",
           tokens_in=100_000, tokens_out=5_000, ts="2026-09-07T22:15:24",
           source="loom/venice", db_path=db)
    reqs = _requests()
    council_only = rc.read_ledger(db, since="2026-09-01", project="council")
    everything = rc.read_ledger(db, since="2026-09-01")

    def lost(rows):
        return [o["request_id"] for o in
                rc.orphan_requests(reqs, rc.match(rows, reqs)[0])]

    assert "f" in lost(council_only)        # against one project's slice: looks lost
    assert "f" not in lost(everything)      # against the whole ledger: is not


def test_a_request_whose_sku_names_no_model_is_skipped_not_guessed(tmp_path):
    db = tmp_path / "l.db"
    flat = [line("search-augmentation", 0.01, "2026-09-07T22:20:00.000Z", "s", 0, 0)]
    out = rc.backfill(rc.fold_requests(flat), db_path=db)
    assert out["unusable"] == 1 and out["written"] == 0
    assert _rows(db) == []


def test_a_dry_run_reports_the_recovery_and_writes_nothing(tmp_path):
    db = tmp_path / "l.db"
    orphans = rc.orphan_requests(_requests(), rc.match([LOGGED], _requests())[0])
    out = rc.backfill(orphans, db_path=db, dry_run=True)
    assert out["candidates"] == 1 and out["written"] == 0
    assert out["diem"] == pytest.approx(2.7549)
    assert not db.exists()


# --- the window the operator asked for is the window that gets written -------
# The billing fetch is deliberately widened a day on each side, so a call at the
# edge of the window still finds its bill and is not mistaken for an orphan.
# That widening must NOT leak into the set of rows written. It did: a run for
# 2026-09-11 wrote 2026-09-10's calls too, and the reported total (39.3229 DIEM
# of claude-fable-5-1) was two days of spend under a one-day heading. The rows
# were individually right; the scope and the headline were wrong.

SPREAD = (
    line("claude-fable-5-1-llm-input-mtoken", 17.1602, "2026-09-10T10:21:00.000Z",
         "before", 100, 20)
    , line("claude-fable-5-1-llm-input-mtoken", 22.1627, "2026-09-11T10:20:38.000Z",
           "inside", 100, 20)
    , line("claude-fable-5-1-llm-input-mtoken", 9.9999, "2026-09-12T10:00:00.000Z",
           "after", 100, 20)
)


def test_only_requests_billed_inside_the_window_are_backfilled(tmp_path):
    from datetime import datetime
    reqs = rc.fold_requests(list(SPREAD))
    inside = rc.requests_in_window(reqs, datetime(2026, 9, 11), datetime(2026, 9, 12))
    assert [r["request_id"] for r in inside] == ["inside"]

    db = tmp_path / "l.db"
    out = rc.backfill(inside, db_path=db)
    assert out["written"] == 1
    assert out["diem"] == pytest.approx(22.1627)
    assert [r["ts"][:10] for r in _rows(db)] == ["2026-09-11"]


def test_a_request_with_no_timestamp_is_still_reported_not_silently_dropped():
    """It cannot be placed in the window, so it cannot be excluded by date
    either. It stays in the candidate set and comes out as `unusable`."""
    undated = {"request_id": "x", "model": "m", "amount": 1.0, "ts": None,
               "prompt_tokens": 1, "completion_tokens": 1}
    from datetime import datetime
    kept = rc.requests_in_window([undated], datetime(2026, 9, 11), datetime(2026, 9, 12))
    assert kept == [undated]
    assert rc.backfill(kept, dry_run=True)["unusable"] == 1


def test_run_clips_the_backfill_to_the_window_even_though_it_fetches_wider(
        tmp_path, monkeypatch, capsys):
    """End to end through `run()`: the fetch is three days wide, the write is
    one. This is the exact shape of the 2026-09-11 over-report."""
    import argparse
    db = tmp_path / "l.db"
    monkeypatch.setenv("VENICE_ADMIN_KEY", "sk-admin")
    seen = {}

    def fake_fetch(key, *, start, end, **kw):
        seen.update(start=start, end=end)
        return list(SPREAD)
    monkeypatch.setattr(rc, "fetch_billing", fake_fetch)

    a = argparse.Namespace(since="2026-09-11", until="2026-09-12", project=None,
                           price_basis="current", db=str(db), tolerance=5.0,
                           backfill=True, backfill_project=rc.BACKFILL_PROJECT,
                           dry_run=False)
    rc.run(a)
    assert seen["start"].startswith("2026-09-10")     # fetched wide
    assert seen["end"].startswith("2026-09-13")
    written = _rows(db)
    assert [r["ext_id"] for r in written] == ["venice-bill:inside"]   # wrote narrow
