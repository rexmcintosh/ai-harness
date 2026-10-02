"""`venice-usage export` / `venice-usage ingest` — the CI reconciliation path.

The workflow calls `export` as an `if: always()` step and uploads the file as a
build artifact; the VPS calls `ingest` on whatever it downloads. Export follows
the same best-effort contract as `log` (accounting must never fail a merge gate);
ingest is an operator command and reports real failures.
"""
from __future__ import annotations
import json

import venice_usage
from venice_usage import cli as ucli


GH_ENV = {"GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "7", "GITHUB_RUN_ATTEMPT": "1",
          "GITHUB_EVENT_NAME": "pull_request", "PR_NUMBER": "3"}


def _seed(db, n=2):
    for i in range(n):
        venice_usage.append(project="council", task_type="review", model=f"m{i}",
                            tokens_in=10, tokens_out=20, usd=0.0,
                            source="council/venice", db_path=db)


def test_export_writes_the_artifact_and_exits_zero(tmp_path, monkeypatch):
    db, out = tmp_path / "l.db", tmp_path / "u.jsonl"
    _seed(db, 2)
    monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    assert ucli.main(["export", "--output", str(out), "--origin-id", "run-1"]) == 0
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert lines[0]["rows"] == 2
    assert lines[1]["ext_id"] == "run-1#1"


def test_export_builds_its_origin_from_the_github_environment(tmp_path, monkeypatch):
    db, out = tmp_path / "l.db", tmp_path / "u.jsonl"
    _seed(db, 1)
    monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    for k, v in GH_ENV.items():
        monkeypatch.setenv(k, v)
    assert ucli.main(["export", "--output", str(out)]) == 0
    head = json.loads(out.read_text().splitlines()[0])
    assert head["origin"]["id"] == "gh:o/r:7:1"
    assert head["origin"]["pr"] == "3"


def test_export_with_no_origin_at_all_warns_but_exits_zero(tmp_path, monkeypatch, capsys):
    db, out = tmp_path / "l.db", tmp_path / "u.jsonl"
    _seed(db, 1)
    monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    for k in GH_ENV:
        monkeypatch.delenv(k, raising=False)
    assert ucli.main(["export", "--output", str(out)]) == 0
    assert "warning" in capsys.readouterr().err.lower()
    assert not out.exists()


def test_export_failure_is_strict_only_on_demand(tmp_path, monkeypatch):
    db, out = tmp_path / "l.db", tmp_path / "u.jsonl"
    _seed(db, 1)
    monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    for k in GH_ENV:
        monkeypatch.delenv(k, raising=False)
    assert ucli.main(["export", "--output", str(out), "--strict"]) == 2


def test_export_of_a_never_created_ledger_still_writes_a_header(tmp_path, monkeypatch):
    out = tmp_path / "u.jsonl"
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "never.db"))
    assert ucli.main(["export", "--output", str(out), "--origin-id", "run-1"]) == 0
    assert json.loads(out.read_text().splitlines()[0])["rows"] == 0


def test_ingest_merges_and_reports_counts(tmp_path, monkeypatch, capsys):
    src, dst, out = tmp_path / "a.db", tmp_path / "b.db", tmp_path / "u.jsonl"
    _seed(src, 3)
    venice_usage.export_jsonl(out, origin={"id": "run-9"}, db_path=src)
    monkeypatch.setenv("VENICE_USAGE_DB", str(dst))
    assert ucli.main(["ingest", str(out), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["inserted"] == 3 and res["skipped"] == 0
    assert venice_usage.connect(dst).execute(
        "SELECT count(*) FROM usage").fetchone()[0] == 3


def test_ingest_twice_does_not_double_count(tmp_path, monkeypatch, capsys):
    src, dst, out = tmp_path / "a.db", tmp_path / "b.db", tmp_path / "u.jsonl"
    _seed(src, 3)
    venice_usage.export_jsonl(out, origin={"id": "run-9"}, db_path=src)
    monkeypatch.setenv("VENICE_USAGE_DB", str(dst))
    ucli.main(["ingest", str(out)])
    capsys.readouterr()
    assert ucli.main(["ingest", str(out), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["inserted"] == 0 and res["skipped"] == 3


def test_ingest_of_a_foreign_file_exits_two(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"kind": "nope", "version": 1}\n')
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "b.db"))
    assert ucli.main(["ingest", str(bad)]) == 2
    assert "error" in capsys.readouterr().err.lower()


def test_ingest_accepts_several_paths(tmp_path, monkeypatch, capsys):
    dst = tmp_path / "b.db"
    outs = []
    for run in ("1", "2"):
        src = tmp_path / f"a{run}.db"
        _seed(src, 2)
        o = tmp_path / f"u{run}.jsonl"
        venice_usage.export_jsonl(o, origin={"id": f"run-{run}"}, db_path=src)
        outs.append(str(o))
    monkeypatch.setenv("VENICE_USAGE_DB", str(dst))
    assert ucli.main(["ingest", *outs, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["inserted"] == 4
