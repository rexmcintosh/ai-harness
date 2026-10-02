import json
import venice_usage.cli as cli
from venice_usage.ledger import connect

def test_log_appends_row_and_exits_zero(tmp_path, monkeypatch):
    db = tmp_path / "l.db"; monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    rc = cli.main(["log", "--project", "romance", "--task-type", "draft",
                   "--model", "claude-opus-4-8", "--tokens-in", "10", "--tokens-out", "20",
                   "--source", "venice-draft.sh"])
    assert rc == 0
    assert connect(db).execute("SELECT count(*) FROM usage").fetchone()[0] == 1

def test_log_never_raises_on_bad_db(tmp_path, monkeypatch, capsys):
    # unwritable path -> still exit 0, warning on stderr
    monkeypatch.setenv("VENICE_USAGE_DB", "/proc/nonexistent/l.db")
    rc = cli.main(["log", "--project", "p", "--task-type", "t", "--model", "m"])
    assert rc == 0
    assert "venice-usage" in capsys.readouterr().err

def test_report_json_rollup(tmp_path, monkeypatch, capsys):
    db = tmp_path / "l.db"; monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    cli.main(["log", "--project", "romance", "--task-type", "draft", "--model", "m", "--usd", "0.20"])
    cli.main(["log", "--project", "romance", "--task-type", "edit", "--model", "m", "--usd", "0.05"])
    rc = cli.main(["report", "--group-by", "project", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    # The JSON report names its price basis: a rollup whose vintage is unstated
    # is what put a 2.5x error into an audit. "m" is not a priceable model, so
    # both bases fall back to the --usd the caller supplied.
    assert payload["price_basis"] == "current"
    data = payload["rows"]
    assert data[0]["project"] == "romance" and abs(data[0]["usd"] - 0.25) < 1e-9

def test_log_never_raises_on_malformed_arguments(tmp_path, monkeypatch, capsys):
    # A buggy call-site (missing/bad flag) must not propagate argparse's own
    # SystemExit(2) either -> still exit 0, warning on stderr. Covers the failure
    # mode test_log_never_raises_on_bad_db doesn't: parse_args() itself raising,
    # before append() (and _cmd_log's try/except) ever runs. $VENICE_USAGE_DB is
    # still isolated to tmp_path (unused on the correct/expected path, since
    # parsing fails before append() runs) so a regression can't write to the real
    # default ledger at ~/.local/state/venice-usage/ledger.db.
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "l.db"))
    rc = cli.main(["log", "--project", "p", "--task-type", "t", "--model", "m",
                   "--tokens-in", "not-a-number"])
    assert rc == 0
    assert "log failed (ignored)" in capsys.readouterr().err

def test_log_missing_required_flag_still_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "l.db"))
    rc = cli.main(["log", "--project", "p", "--task-type", "t"])  # --model omitted
    assert rc == 0
    assert "log failed (ignored)" in capsys.readouterr().err

def test_report_argparse_error_not_swallowed(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "l.db"))
    # The log-only SystemExit guard in main() must NOT swallow report's argparse
    # errors — report is a diagnostic command and must still exit non-zero.
    with pytest.raises(SystemExit) as ei:
        cli.main(["report", "--nonexistent-flag"])
    assert ei.value.code == 2


# --- `reconcile` as a first-class subcommand -------------------------------
# It existed only as `python -m venice_usage.reconcile`, i.e. in practice only
# as `/home/dev/.local/share/pipx/venvs/council/bin/python -m ...`. Nobody
# remembers that, so nobody ran the one check that catches a mispriced ledger.

def test_reconcile_is_a_venice_usage_subcommand(monkeypatch, capsys):
    import venice_usage.reconcile as rec
    monkeypatch.setenv("VENICE_ADMIN_KEY", "sk-admin")
    seen = {}

    def fake_run(a):
        seen.update(since=a.since, project=a.project, backfill=a.backfill,
                    price_basis=a.price_basis)
        return 0

    monkeypatch.setattr(rec, "run", fake_run)
    assert cli.main(["reconcile", "--since", "2026-09-04", "--project", "council",
                     "--backfill"]) == 0
    assert seen == {"since": "2026-09-04", "project": "council",
                    "backfill": True, "price_basis": "current"}


def test_reconcile_is_listed_in_the_top_level_help(capsys):
    import pytest
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "reconcile" in capsys.readouterr().out


def test_reconcile_without_an_admin_key_says_so_and_does_not_exit_zero(monkeypatch,
                                                                       capsys):
    monkeypatch.delenv("VENICE_ADMIN_KEY", raising=False)
    assert cli.main(["reconcile", "--since", "2026-09-04"]) == 1
    assert "VENICE_ADMIN_KEY" in capsys.readouterr().err
