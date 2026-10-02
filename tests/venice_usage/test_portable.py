"""Portable usage rows: export a ledger to JSONL and ingest it back.

Why this exists: the CI merge gate runs `scripts/venice_review.py` on a GitHub
runner. `VeniceClient._log_usage()` fires there and writes to the runner's own
`~/.local/state/venice-usage/ledger.db`, which the runner destroys when the job
ends — so ~34 DIEM/week of real spend is logged successfully into nothing. These
two primitives make that spend durable: the job exports its rows to a workflow
artifact, and the VPS ingests the artifacts back into the real ledger.

Ingest MUST be idempotent — artifacts get downloaded more than once, and a
double-counted ledger is worse than a missing one.
"""
from __future__ import annotations
import json
import sqlite3

import pytest

import venice_usage
from venice_usage import portable


ORIGIN = {"id": "gh:o/r:1001:1", "repo": "o/r", "run_id": "1001",
          "run_attempt": "1", "pr": "7", "event": "synchronize"}


def _seed(db, n=3, project="council", ts=None):
    for i in range(n):
        venice_usage.append(project=project, task_type="review",
                            model=f"m{i}", tokens_in=100 + i, tokens_out=10 + i,
                            usd=0.0, source="council/venice",
                            ts=ts or f"2026-09-11T0{i}:00:00", db_path=db)


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


# ---------- schema migration ------------------------------------------------

def test_ledger_migrates_an_old_db_that_has_no_ext_id_column(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
      CREATE TABLE usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, project TEXT NOT NULL,
        task_type TEXT NOT NULL, model TEXT NOT NULL, tokens_in INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0, usd REAL, source TEXT);
      INSERT INTO usage(ts,project,task_type,model,tokens_in,tokens_out,usd,source)
        VALUES ('2026-01-01T00:00:00','council','chat','m',1,2,0.0,'x');
    """)
    conn.commit(); conn.close()

    with venice_usage.connect(db) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(usage)")}
        assert "ext_id" in cols
        assert c.execute("SELECT count(*) FROM usage").fetchone()[0] == 1


def test_append_stores_ext_id_and_a_repeat_is_ignored(tmp_path):
    db = tmp_path / "l.db"
    a = venice_usage.append(project="council", task_type="review", model="m",
                            tokens_in=1, tokens_out=2, usd=0.0, ext_id="k1", db_path=db)
    b = venice_usage.append(project="council", task_type="review", model="m",
                            tokens_in=1, tokens_out=2, usd=0.0, ext_id="k1", db_path=db)
    assert a and b == 0            # second insert ignored, signalled by rowid 0
    with venice_usage.connect(db) as c:
        assert c.execute("SELECT count(*) FROM usage").fetchone()[0] == 1


def test_append_without_ext_id_still_allows_identical_rows(tmp_path):
    db = tmp_path / "l.db"
    for _ in range(2):
        venice_usage.append(project="council", task_type="review", model="m",
                            tokens_in=1, tokens_out=2, usd=0.0, db_path=db)
    with venice_usage.connect(db) as c:
        assert c.execute("SELECT count(*) FROM usage").fetchone()[0] == 2


# ---------- export ----------------------------------------------------------

def test_export_writes_a_header_then_one_row_per_ledger_row(tmp_path):
    db = tmp_path / "l.db"; out = tmp_path / "u.jsonl"
    _seed(db, 3)
    n = portable.export_jsonl(out, origin=ORIGIN, db_path=db)
    assert n == 3
    lines = _lines(out)
    assert len(lines) == 4
    head = lines[0]
    assert head["kind"] == portable.KIND and head["version"] == 1
    assert head["origin"] == ORIGIN and head["rows"] == 3
    assert [r["model"] for r in lines[1:]] == ["m0", "m1", "m2"]


def test_export_stamps_ext_id_from_the_origin_id_and_row_id(tmp_path):
    db = tmp_path / "l.db"; out = tmp_path / "u.jsonl"
    _seed(db, 2)
    portable.export_jsonl(out, origin=ORIGIN, db_path=db)
    assert [r["ext_id"] for r in _lines(out)[1:]] == ["gh:o/r:1001:1#1", "gh:o/r:1001:1#2"]


def test_export_of_an_empty_ledger_writes_the_header_only(tmp_path):
    db = tmp_path / "l.db"; out = tmp_path / "u.jsonl"
    assert portable.export_jsonl(out, origin=ORIGIN, db_path=db) == 0
    assert len(_lines(out)) == 1


def test_export_of_a_missing_ledger_writes_the_header_only(tmp_path):
    """The gate can fail before any Venice call — no ledger file at all. The
    artifact must still be written, so a run is never silently unaccounted."""
    out = tmp_path / "u.jsonl"
    assert portable.export_jsonl(out, origin=ORIGIN, db_path=tmp_path / "never.db") == 0
    assert _lines(out)[0]["rows"] == 0


def test_export_since_filters_older_rows(tmp_path):
    db = tmp_path / "l.db"; out = tmp_path / "u.jsonl"
    _seed(db, 3)
    n = portable.export_jsonl(out, origin=ORIGIN, db_path=db, since="2026-09-11T01:00:00")
    assert n == 2
    assert [r["model"] for r in _lines(out)[1:]] == ["m1", "m2"]


def test_export_requires_a_non_empty_origin_id(tmp_path):
    with pytest.raises(ValueError):
        portable.export_jsonl(tmp_path / "u.jsonl", origin={"id": ""},
                              db_path=tmp_path / "l.db")


# ---------- ingest ----------------------------------------------------------

def test_ingest_lands_every_exported_row(tmp_path):
    src, dst, out = tmp_path / "a.db", tmp_path / "b.db", tmp_path / "u.jsonl"
    _seed(src, 3)
    portable.export_jsonl(out, origin=ORIGIN, db_path=src)
    res = portable.ingest_jsonl(out, db_path=dst)
    assert res == {"files": 1, "rows": 3, "inserted": 3, "skipped": 0, "malformed": 0}
    with venice_usage.connect(dst) as c:
        got = c.execute("SELECT project,task_type,model,tokens_in,tokens_out,source,ext_id"
                        " FROM usage ORDER BY id").fetchall()
    assert got[0] == ("council", "review", "m0", 100, 10, "council/venice", "gh:o/r:1001:1#1")
    assert len(got) == 3


def test_ingest_is_idempotent(tmp_path):
    src, dst, out = tmp_path / "a.db", tmp_path / "b.db", tmp_path / "u.jsonl"
    _seed(src, 3)
    portable.export_jsonl(out, origin=ORIGIN, db_path=src)
    portable.ingest_jsonl(out, db_path=dst)
    again = portable.ingest_jsonl(out, db_path=dst)
    assert again["inserted"] == 0 and again["skipped"] == 3
    with venice_usage.connect(dst) as c:
        assert c.execute("SELECT count(*) FROM usage").fetchone()[0] == 3


def test_ingest_keeps_two_different_runs_apart(tmp_path):
    dst = tmp_path / "b.db"
    for i, run in enumerate(("1001", "1002")):
        src, out = tmp_path / f"a{i}.db", tmp_path / f"u{i}.jsonl"
        _seed(src, 2)
        portable.export_jsonl(out, origin={**ORIGIN, "id": f"gh:o/r:{run}:1",
                                           "run_id": run}, db_path=src)
        portable.ingest_jsonl(out, db_path=dst)
    with venice_usage.connect(dst) as c:
        assert c.execute("SELECT count(*) FROM usage").fetchone()[0] == 4


def test_ingest_never_touches_rows_already_in_the_ledger(tmp_path):
    dst, src, out = tmp_path / "b.db", tmp_path / "a.db", tmp_path / "u.jsonl"
    _seed(dst, 2, project="loom")
    _seed(src, 2)
    portable.export_jsonl(out, origin=ORIGIN, db_path=src)
    portable.ingest_jsonl(out, db_path=dst)
    with venice_usage.connect(dst) as c:
        by = dict(c.execute("SELECT project, count(*) FROM usage GROUP BY project"))
    assert by == {"loom": 2, "council": 2}


def test_ingest_accepts_a_directory_of_artifacts(tmp_path):
    dst = tmp_path / "b.db"
    pile = tmp_path / "pile"; pile.mkdir()
    for run in ("1001", "1002"):
        src = tmp_path / f"a{run}.db"
        _seed(src, 2)
        portable.export_jsonl(pile / f"{run}.jsonl",
                              origin={**ORIGIN, "id": f"gh:o/r:{run}:1"}, db_path=src)
    (pile / "notes.txt").write_text("ignore me")
    res = portable.ingest_jsonl(pile, db_path=dst)
    assert res["files"] == 2 and res["inserted"] == 4


def test_ingest_refuses_a_file_that_is_not_a_usage_export(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"kind": "something-else", "version": 1}) + "\n")
    with pytest.raises(ValueError, match="not a venice-usage export"):
        portable.ingest_jsonl(bad, db_path=tmp_path / "b.db")


def test_ingest_refuses_a_future_format_version(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"kind": portable.KIND, "version": 99}) + "\n")
    with pytest.raises(ValueError, match="version"):
        portable.ingest_jsonl(bad, db_path=tmp_path / "b.db")


def test_ingest_counts_malformed_rows_instead_of_aborting(tmp_path):
    src, dst, out = tmp_path / "a.db", tmp_path / "b.db", tmp_path / "u.jsonl"
    _seed(src, 2)
    portable.export_jsonl(out, origin=ORIGIN, db_path=src)
    out.write_text(out.read_text() + "{not json\n" + json.dumps({"ts": "x"}) + "\n")
    res = portable.ingest_jsonl(out, db_path=dst)
    assert res["inserted"] == 2 and res["malformed"] == 2


def test_round_trip_preserves_the_rollup(tmp_path):
    src, dst, out = tmp_path / "a.db", tmp_path / "b.db", tmp_path / "u.jsonl"
    _seed(src, 4)
    portable.export_jsonl(out, origin=ORIGIN, db_path=src)
    portable.ingest_jsonl(out, db_path=dst)
    a = venice_usage.query_rollup(group_by=("project", "model"), db_path=src)
    b = venice_usage.query_rollup(group_by=("project", "model"), db_path=dst)
    assert sorted(map(str, a)) == sorted(map(str, b))


# ---------- GitHub origin ---------------------------------------------------

GH_ENV = {"GITHUB_REPOSITORY": "rexmcintosh/swimtrack-website",
          "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "2",
          "GITHUB_EVENT_NAME": "pull_request", "GITHUB_WORKFLOW": "Venice Review Council",
          "GITHUB_SHA": "deadbeef", "PR_NUMBER": "11"}


def test_github_origin_ids_the_repo_run_and_attempt(monkeypatch):
    for k, v in GH_ENV.items():
        monkeypatch.setenv(k, v)
    o = portable.github_origin()
    assert o["id"] == "gh:rexmcintosh/swimtrack-website:42:2"
    assert o["repo"] == "rexmcintosh/swimtrack-website"
    assert o["pr"] == "11" and o["event"] == "pull_request" and o["sha"] == "deadbeef"


def test_github_origin_is_none_off_a_runner(monkeypatch):
    for k in GH_ENV:
        monkeypatch.delenv(k, raising=False)
    assert portable.github_origin() is None


def test_github_origin_defaults_a_missing_attempt_to_one(monkeypatch):
    for k, v in GH_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GITHUB_RUN_ATTEMPT")
    assert portable.github_origin()["id"].endswith(":42:1")


def test_two_attempts_of_the_same_run_do_not_collide(monkeypatch, tmp_path):
    """A re-run of a failed workflow is genuinely a second set of Venice calls
    and must be counted twice, not deduped away."""
    ids = []
    for attempt in ("1", "2"):
        for k, v in GH_ENV.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", attempt)
        ids.append(portable.github_origin()["id"])
    assert ids[0] != ids[1]
