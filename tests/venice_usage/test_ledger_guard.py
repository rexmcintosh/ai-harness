"""A fake transport must not be able to reach the production ledger.

`tests/conftest.py` redirects VENICE_USAGE_DB, but only under pytest. On
2026-09-11 a hand-run demo script built a real `council.venice.VeniceClient`
with a fake `post`, and its thirteen synthetic rows went into
`~/.local/state/venice-usage/ledger.db` and had to be deleted by hand.

The guard is not "remember to set VENICE_USAGE_DB". It is: an injected
transport means the call was not real, so its usage may not be written to the
production ledger at all. A caller that genuinely wraps a live transport says
so, once, in the constructor.

These tests never touch the real path: `production_db` is monkeypatched to a
tmp_path file, so "production" is simulated somewhere harmless even while the
guard is still missing.
"""
import sqlite3

import pytest

from venice_usage import ledger


@pytest.fixture
def fake_production(tmp_path, monkeypatch):
    """Make tmp_path/prod.db *be* the production ledger, and select it."""
    prod = tmp_path / "prod.db"
    monkeypatch.setattr(ledger, "production_db", lambda: prod)
    monkeypatch.setenv("VENICE_USAGE_DB", str(prod))
    return prod


def _count(db):
    if not db.exists():
        return 0
    with sqlite3.connect(str(db)) as conn:
        try:
            return conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
        except sqlite3.Error:
            return 0


def _reply(payload):
    class R:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return payload
    return R


BODY = {"choices": [{"message": {"content": "hi"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 22}}


@pytest.mark.parametrize("module,project", [("council.venice", "council"),
                                            ("loom.venice", "loom")])
def test_a_client_with_a_fake_transport_cannot_write_the_production_ledger(
        module, project, fake_production, capsys):
    mod = __import__(module, fromlist=["VeniceClient"])
    client = mod.VeniceClient(api_key="k", post=lambda *a, **k: _reply(BODY))
    client.complete("m", "s", "u")
    assert _count(fake_production) == 0
    assert "production ledger" in capsys.readouterr().err


@pytest.mark.parametrize("module", ["council.venice", "loom.venice"])
def test_the_real_transport_still_logs_normally(module, fake_production, monkeypatch):
    mod = __import__(module, fromlist=["VeniceClient"])
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _reply(BODY))
    mod.VeniceClient(api_key="k").complete("m", "s", "u")
    assert _count(fake_production) == 1


@pytest.mark.parametrize("module", ["council.venice", "loom.venice"])
def test_a_wrapped_live_transport_can_say_so_and_is_logged(module, fake_production):
    """The budget-capped wrapper injects `post` and really does call Venice.
    It opts in explicitly — one visible, auditable word at the call site."""
    mod = __import__(module, fromlist=["VeniceClient"])
    mod.VeniceClient(api_key="k", post=lambda *a, **k: _reply(BODY),
                     transport_is_real=True).complete("m", "s", "u")
    assert _count(fake_production) == 1


@pytest.mark.parametrize("module", ["council.venice", "loom.venice"])
def test_a_fake_transport_still_logs_to_a_throwaway_ledger(module, tmp_path,
                                                           monkeypatch):
    """Only the production path is refused. A test or demo that has already
    pointed VENICE_USAGE_DB somewhere disposable keeps its usage rows."""
    mod = __import__(module, fromlist=["VeniceClient"])
    monkeypatch.setattr(ledger, "production_db", lambda: tmp_path / "prod.db")
    scratch = tmp_path / "scratch.db"
    monkeypatch.setenv("VENICE_USAGE_DB", str(scratch))
    mod.VeniceClient(api_key="k",
                     post=lambda *a, **k: _reply(BODY)).complete("m", "s", "u")
    assert _count(scratch) == 1
    assert _count(tmp_path / "prod.db") == 0


def test_the_shared_door_will_not_let_a_caller_dodge_the_question():
    """`transport_is_real` has no default: a new client cannot forget it,
    because the call does not compile without an answer."""
    from venice_usage.guard import log_client_call
    with pytest.raises(TypeError):
        log_client_call(data=BODY, model="m", project="p", task_type="t",
                        source="s")


def test_is_production_db_follows_the_env_var(tmp_path, monkeypatch):
    prod = tmp_path / "prod.db"
    monkeypatch.setattr(ledger, "production_db", lambda: prod)
    monkeypatch.setenv("VENICE_USAGE_DB", str(prod))
    assert ledger.is_production_db()
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "other.db"))
    assert not ledger.is_production_db()
    assert ledger.is_production_db(prod)
