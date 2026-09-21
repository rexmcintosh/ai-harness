import json
import os
from datetime import datetime
from pathlib import Path
import pytest
import diem.cli as cli
from diem.balance import BalanceUnavailable
from diem.config import DiemConfig
from diem.queue import QueueDir
from diem.runners import RunResult

@pytest.fixture(autouse=True)
def _clear_ambient_venice_env(monkeypatch):
    """~/.zshenv sources ~/.env into every zsh shell on this box, so os.environ
    here may already carry real VENICE_API_KEY/VENICE_KEY/VENICE_* values. The
    _drain_env tests below assert exact values for the generic names and must
    never depend on -- or risk printing -- whatever real key happens to be
    ambient on the host running the suite. Strip every VENICE_* var per test;
    monkeypatch restores it automatically afterward (same guard as
    tests/test_config.py's _clear_ambient_council_key, generalized to the
    whole VENICE_ prefix and function-scoped instead of module-scoped)."""
    for name in list(os.environ):
        if name.startswith("VENICE_"):
            monkeypatch.delenv(name, raising=False)

def _cfg_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(f'daily_diem = 100.0\nrepos = []\n'
                 f'state_dir = "{tmp_path / "state"}"\n'
                 f'outputs_dir = "{tmp_path / "out"}"\n')
    return p

def _summary(**kw):
    base = {"aborted": None, "floor": 15.0, "started_balance": 40.0,
            "ended_balance": 20.0, "deadline": "2026-07-04T00:50:00",
            "ran": [], "skipped": []}
    base.update(kw); return base

def test_queue_add_and_list_and_rm(tmp_path, capsys, monkeypatch):
    cfgp = _cfg_file(tmp_path)
    assert cli.main(["queue", "add", "ask", "which host?", "--panel", "decision",
                     "--config", str(cfgp)]) == 0
    q = QueueDir(tmp_path / "state")
    items = q.pending("2026-07-03T21:00:00")
    assert len(items) == 1 and items[0].banked and items[0].type == "ask"
    assert cli.main(["queue", "list", "--config", str(cfgp)]) == 0
    out = capsys.readouterr().out
    assert "ask" in out and items[0].id[:8] in out
    assert cli.main(["queue", "rm", items[0].id, "--config", str(cfgp)]) == 0
    assert q.pending("2026-07-03T21:00:00") == []

def test_queue_add_review_and_images(tmp_path):
    cfgp = _cfg_file(tmp_path)
    cli.main(["queue", "add", "review", "/r/swim", "--config", str(cfgp)])
    cli.main(["queue", "add", "images", "/r/re", "7", "--config", str(cfgp)])
    q = QueueDir(tmp_path / "state")
    by_type = {i.type: i for i in q.pending("2026-07-03T21:00:00")}
    assert by_type["review"].payload == {"repo": "/r/swim", "diff": True}
    assert by_type["images"].payload["count"] == 7

@pytest.mark.parametrize("extra_args", [
    ["review"],                                  # no repo
    ["images", "/r/re"],                         # no count
    ["images", "/r/re", "notanumber"],           # non-int count
    ["cmd"],                                      # no name
])
def test_queue_add_missing_args_exit_2(tmp_path, capsys, extra_args):
    cfgp = _cfg_file(tmp_path)
    rc = cli.main(["queue", "add", *extra_args, "--config", str(cfgp)])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.strip() != "" and len(err.strip().splitlines()) == 1

def test_pause_and_resume(tmp_path):
    cfgp = _cfg_file(tmp_path)
    from diem.state import pause_until
    assert cli.main(["pause", "2", "--config", str(cfgp)]) == 0
    assert pause_until(tmp_path / "state") is not None
    assert cli.main(["resume", "--config", str(cfgp)]) == 0
    assert pause_until(tmp_path / "state") is None

def test_drain_requires_checkpoint_flag(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["drain", "--config", str(_cfg_file(tmp_path))])

def test_drain_env_prepends_pipx_bin_dir_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    # tmp_path has no .env -> generic names have nothing to fall back to but key.
    # (Never point env_path at the real ~/.env in a test -- see
    # test_drain_env_missing_env_file_still_sets_generic_names_from_key for why
    # this can't just omit env_path and rely on the default.)
    env = cli._drain_env("k", env_path=tmp_path / ".env")
    pipx_bin = str(Path.home() / ".local" / "bin")
    assert env["PATH"].startswith(pipx_bin)
    assert env["PATH"] == f"{pipx_bin}:/usr/bin:/bin"
    assert env["VENICE_API_KEY"] == "k" and env["VENICE_KEY"] == "k"

def test_drain_env_does_not_double_prepend(tmp_path, monkeypatch):
    pipx_bin = str(Path.home() / ".local" / "bin")
    monkeypatch.setenv("PATH", f"{pipx_bin}:/usr/bin:/bin")
    env = cli._drain_env("k", env_path=tmp_path / ".env")
    assert env["PATH"] == f"{pipx_bin}:/usr/bin:/bin"
    assert env["PATH"].split(":").count(pipx_bin) == 1

def test_drain_env_passes_through_project_venice_vars(tmp_path):
    # Cron runs `diem drain` under /bin/sh, so ~/.zshenv never sources ~/.env
    # into os.environ -- _drain_env must read the project vars itself, or the
    # queued council/loom subprocesses fall back to the shared key and bill
    # to DEFAULT instead of their own project. This is the regression that
    # would otherwise let loom's Venice usage show zero forever.
    env_path = tmp_path / ".env"
    env_path.write_text("VENICE_COUNCIL_KEY=council-secret\nVENICE_LOOM_KEY=loom-secret\n")
    env = cli._drain_env("k", env_path=env_path)
    assert env["VENICE_COUNCIL_KEY"] == "council-secret"
    assert env["VENICE_LOOM_KEY"] == "loom-secret"

def test_drain_env_venice_key_from_file_not_overwritten_by_key(tmp_path):
    # VENICE_KEY is romance's var under the per-project map; the drain's own
    # DEFAULT `key` must never clobber it even though it's also one of the
    # two legacy generic names _drain_env used to force-set.
    env_path = tmp_path / ".env"
    env_path.write_text("VENICE_KEY=romance-secret\n")
    env = cli._drain_env("k", env_path=env_path)
    assert env["VENICE_KEY"] == "romance-secret"
    assert env["VENICE_API_KEY"] == "k"  # absent from the file -> falls back to key

def test_drain_env_missing_env_file_still_sets_generic_names_from_key(tmp_path):
    env_path = tmp_path / "nope" / ".env"  # parent dir doesn't exist -> unreadable
    env = cli._drain_env("k", env_path=env_path)
    assert env["VENICE_API_KEY"] == "k" and env["VENICE_KEY"] == "k"

def test_drain_env_does_not_leak_non_venice_entries(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("VENICE_COUNCIL_KEY=council-secret\nSOME_OTHER_SECRET=nope\n")
    env = cli._drain_env("k", env_path=env_path)
    assert "SOME_OTHER_SECRET" not in env

def test_status_exits_zero_when_balance_unavailable(tmp_path, monkeypatch, capsys):
    cfgp = _cfg_file(tmp_path)
    monkeypatch.setattr(cli, "load_venice_key", lambda: "k")
    class Down:
        def __init__(self, *a, **k): pass
        def diem_balance(self): raise BalanceUnavailable("down")
    monkeypatch.setattr(cli, "BalanceClient", Down)
    assert cli.main(["status", "--config", str(cfgp)]) == 0
    assert "unavailable" in capsys.readouterr().out

def test_drain_writes_diem_day_jsonl_and_pings(tmp_path, monkeypatch):
    cfgp = _cfg_file(tmp_path)
    sent = []
    monkeypatch.setattr(cli, "load_venice_key", lambda: "k")
    monkeypatch.setattr(cli, "BalanceClient", lambda key: object())
    monkeypatch.setattr(cli, "run_checkpoint", lambda *a, **k: _summary())
    monkeypatch.setattr(cli, "send_telegram", lambda cfg, text: sent.append(text) or True)
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 7, 3, 23, 5))
    assert cli.main(["drain", "--checkpoint", "--config", str(cfgp)]) == 0
    jl = tmp_path / "state" / "summaries" / "2026-07-03.jsonl"   # DIEM-day label
    assert jl.exists() and len(jl.read_text().splitlines()) == 1
    assert len(sent) == 1  # first checkpoint of the night → evening ping

def test_drain_last_checkpoint_writes_morning_report(tmp_path, monkeypatch):
    cfgp = _cfg_file(tmp_path)
    sent = []
    monkeypatch.setattr(cli, "load_venice_key", lambda: "k")
    monkeypatch.setattr(cli, "BalanceClient", lambda key: object())
    monkeypatch.setattr(cli, "run_checkpoint", lambda *a, **k: _summary())
    monkeypatch.setattr(cli, "send_telegram", lambda cfg, text: sent.append(text) or True)
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 7, 3, 23, 5))
    cli.main(["drain", "--checkpoint", "--config", str(cfgp)])
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 7, 4, 0, 20))
    cli.main(["drain", "--checkpoint", "--config", str(cfgp)])
    jl = tmp_path / "state" / "summaries" / "2026-07-03.jsonl"
    assert len(jl.read_text().splitlines()) == 2      # same night, same file
    report = tmp_path / "state" / "reports" / "2026-07-03.md"
    assert report.exists()
    assert len(sent) == 2 and "Report" in sent[1]

def test_drain_report_failure_does_not_crash(tmp_path, monkeypatch, capsys):
    cfgp = _cfg_file(tmp_path)
    monkeypatch.setattr(cli, "load_venice_key", lambda: "k")
    monkeypatch.setattr(cli, "BalanceClient", lambda key: object())
    monkeypatch.setattr(cli, "run_checkpoint", lambda *a, **k: _summary())
    monkeypatch.setattr(cli, "send_telegram", lambda cfg, text: True)
    def boom(*a, **k): raise OSError("disk full")
    monkeypatch.setattr(cli, "write_morning_report", boom)
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 7, 4, 0, 20))
    assert cli.main(["drain", "--checkpoint", "--config", str(cfgp)]) == 0


def test_now_is_utc(monkeypatch):
    # Force a non-UTC process TZ; _now must still report UTC wall-clock, not local.
    import time
    from datetime import timezone
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    try:
        got = cli._now()
        ref = datetime.now(timezone.utc).replace(tzinfo=None)
        assert got.tzinfo is None                              # naive
        assert abs((ref - got).total_seconds()) < 5           # UTC, not LA (~7-8h off)
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def _utc_cfg_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(f'daily_diem = 100.0\nrepos = []\n'
                 f'state_dir = "{tmp_path / "state"}"\n'
                 f'outputs_dir = "{tmp_path / "out"}"\n'
                 f'deadline = "23:50"\nreset = "00:00"\n'
                 f'[[checkpoints]]\ntime = "21:00"\nfloor = 0.40\n'
                 f'[[checkpoints]]\ntime = "23:00"\nfloor = 0.15\n'
                 f'[[checkpoints]]\ntime = "23:45"\nfloor = 0.0\n')
    return p

def test_morning_report_fires_with_midnight_reset(tmp_path, monkeypatch):
    p = _utc_cfg_file(tmp_path)
    sent = []
    monkeypatch.setattr(cli, "load_venice_key", lambda: "k")
    monkeypatch.setattr(cli, "BalanceClient", lambda key: object())
    monkeypatch.setattr(cli, "run_checkpoint", lambda *a, **k: _summary())
    monkeypatch.setattr(cli, "send_telegram", lambda cfg, text: sent.append(text) or True)
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 7, 3, 23, 5))
    cli.main(["drain", "--checkpoint", "--config", str(p)])       # first cp → evening ping
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 7, 3, 23, 50))
    cli.main(["drain", "--checkpoint", "--config", str(p)])       # last cp (23:45) → report
    assert (tmp_path / "state" / "reports" / "2026-07-03.md").exists()
    assert len(sent) == 2 and "Report" in sent[1]




# --- venice-usage: a real window, and a model axis both sides share ---------
# The old command read `trailingSevenDays` off /api_keys, so --days was decor.
# The rewrite reads /billing/usage-history over the window actually asked for.
# Bills carry no key or project tag, so the project table is ledger-only and
# the ledger-vs-billed comparison happens per model.

def _fake_billing(monkeypatch, by_model, seen=None):
    class FakeUsage:
        def __init__(self, *a, **k):
            pass

        def billed_by_model(self, *, start, end):
            if seen is not None:
                seen.append((start, end))
            return by_model
    monkeypatch.setattr(cli, "UsageClient", FakeUsage)
    monkeypatch.setattr(cli, "load_venice_admin_key", lambda: "sk-admin")


def test_venice_usage_asks_venice_for_the_window_days_names(tmp_path, monkeypatch,
                                                            capsys):
    cfgp = _cfg_file(tmp_path)
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 9, 12, 12, 0))
    seen = []
    _fake_billing(monkeypatch, {}, seen)
    assert cli.main(["venice-usage", "--days", "1", "--config", str(cfgp)]) == 0
    assert cli.main(["venice-usage", "--days", "7", "--config", str(cfgp)]) == 0
    assert seen[0][0] == datetime(2026, 9, 11, 12, 0)
    assert seen[1][0] == datetime(2026, 9, 5, 12, 0)
    assert seen[0][1] == seen[1][1] == datetime(2026, 9, 12, 12, 0)


def test_venice_usage_days_changes_the_numbers_it_prints(tmp_path, monkeypatch,
                                                         capsys):
    cfgp = _cfg_file(tmp_path)
    db = tmp_path / "ledger.db"
    monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    from venice_usage.ledger import append
    append(project="council", task_type="ask", model="m", usd=1.00,
           ts="2026-09-06T02:00:00", db_path=db)      # inside 7d, outside 1d
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 9, 12, 12, 0))

    class Windowed:
        def __init__(self, *a, **k):
            pass

        def billed_by_model(self, *, start, end):
            days = (end - start).days
            return {"m": {"diem": 1.0 * days, "calls": days}}
    monkeypatch.setattr(cli, "UsageClient", Windowed)
    monkeypatch.setattr(cli, "load_venice_admin_key", lambda: "sk-admin")

    cli.main(["venice-usage", "--days", "1", "--json", "--config", str(cfgp)])
    one = json.loads(capsys.readouterr().out)
    cli.main(["venice-usage", "--days", "7", "--json", "--config", str(cfgp)])
    seven = json.loads(capsys.readouterr().out)

    assert one["billed_diem"] != seven["billed_diem"]        # --days N means N
    assert one["projects"] == []                             # the row is 7d old
    assert seven["projects"][0]["project"] == "council"


def test_venice_usage_flags_a_model_venice_billed_and_the_ledger_never_saw(
        tmp_path, monkeypatch, capsys):
    """The claude-fable-5-1 hole, surfaced by the everyday command."""
    cfgp = _cfg_file(tmp_path)
    db = tmp_path / "ledger.db"
    monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    from venice_usage.ledger import append
    append(project="council", task_type="ask", model="m", usd=1.00,
           ts="2026-09-12T02:00:00", db_path=db)
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 9, 12, 12, 0))
    _fake_billing(monkeypatch, {"claude-fable-5-1": {"diem": 81.36, "calls": 36}})

    assert cli.main(["venice-usage", "--json", "--config", str(cfgp)]) == 0
    models = {r["model"]: r for r in json.loads(capsys.readouterr().out)["models"]}
    assert models["claude-fable-5-1"]["note"] == "untracked"
    assert models["m"]["note"] == "no bill"


def test_venice_usage_degrades_when_venice_unavailable(tmp_path, monkeypatch, capsys):
    cfgp = _cfg_file(tmp_path)
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setattr(cli, "load_venice_admin_key", lambda: "sk-admin")
    from diem.usage import UsageUnavailable

    class Down:
        def __init__(self, *a, **k):
            pass

        def billed_by_model(self, **k):
            raise UsageUnavailable("down")
    monkeypatch.setattr(cli, "UsageClient", Down)
    assert cli.main(["venice-usage", "--config", str(cfgp)]) == 0   # still exits 0
    assert "unavailable" in capsys.readouterr().out.lower()


def test_venice_usage_degrades_when_admin_key_missing(tmp_path, monkeypatch, capsys):
    cfgp = _cfg_file(tmp_path)
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "ledger.db"))

    def boom():
        raise SystemExit(2)                # load_venice_admin_key when key absent
    monkeypatch.setattr(cli, "load_venice_admin_key", boom)
    assert cli.main(["venice-usage", "--config", str(cfgp)]) == 0   # never hard-fail
    assert "unavailable" in capsys.readouterr().out.lower()


def test_venice_usage_labels_the_estimate_and_says_bills_have_no_project(
        tmp_path, monkeypatch, capsys):
    cfgp = _cfg_file(tmp_path)
    db = tmp_path / "ledger.db"
    monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    from venice_usage.ledger import append
    append(project="council", task_type="ask", model="m", usd=1.25,
           ts="2026-09-12T02:00:00", db_path=db)
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 9, 12, 12, 0))
    _fake_billing(monkeypatch, {"m": {"diem": 12.5, "calls": 3}})

    cli.main(["venice-usage", "--config", str(cfgp)])
    out = capsys.readouterr().out
    # The estimate must be labelled notional so it is never read as billed spend,
    # and the project table must say why it carries no billed column.
    assert "estimate" in out.lower()
    assert "billed" in out.lower()
    assert "no project" in out.lower()
    assert "12.5" in out


def test_queue_add_not_before_is_stored_and_a_typo_is_refused(tmp_path, capsys):
    cfgp = _cfg_file(tmp_path)
    assert cli.main(["queue", "add", "ask", "leftovers?", "--not-before", "23:00",
                     "--config", str(cfgp)]) == 0
    (it,) = QueueDir(tmp_path / "state").pending("2026-07-03T21:00:00")
    assert it.not_before == "23:00"
    # a typo would fail open at drain time and let the item take the morning allowance
    assert cli.main(["queue", "add", "ask", "typo", "--not-before", "11pm",
                     "--config", str(cfgp)]) == 2
    assert len(QueueDir(tmp_path / "state").pending("2026-07-03T21:00:00")) == 1
