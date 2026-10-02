# tests/loom/test_backends.py
import pytest
from loom import backends


def test_claude_backend_maps_roles_and_joins_prompt(monkeypatch):
    seen = {}
    monkeypatch.setattr(backends.llm, "run",
                        lambda prompt, model, **k: seen.update(prompt=prompt, model=model) or "OUT")
    b = backends.get_backend("claude")
    out = b.complete("weave", "SYSTEM", "USER")
    assert out == "OUT"
    assert seen["model"] == "opus"               # weave role → opus on claude backend
    assert "SYSTEM" in seen["prompt"] and "USER" in seen["prompt"]
    assert b.complete("route", "s", "u") or True  # route role exists
    # verify route maps to haiku
    monkeypatch.setattr(backends.llm, "run", lambda prompt, model, **k: model)
    assert backends.get_backend("claude").complete("route", "s", "u") == "haiku"


def test_venice_backend_maps_roles(monkeypatch):
    captured = {}
    class FakeClient:
        def __init__(self, *a, **k): pass
        def complete(self, model, system, user, json_mode=False, task="weave"):
            captured.update(model=model, json_mode=json_mode, task=task)
            return "VOUT"
    monkeypatch.setattr(backends, "VeniceClient", FakeClient)
    b = backends.get_backend("venice", api_key="k")
    assert b.complete("weave", "s", "u") == "VOUT"
    assert captured["model"] == "deepseek-v4-pro"
    assert captured["task"] == "weave"
    b.complete("route", "s", "u", json_mode=True)
    assert captured["model"] == "deepseek-v4-flash" and captured["json_mode"] is True
    b.complete("distill", "s", "u")
    assert captured["model"] == "deepseek-v4-pro" and captured["task"] == "distill"


def test_unknown_backend_raises():
    with pytest.raises(ValueError):
        backends.get_backend("bogus")


def test_venice_backend_prefers_the_loom_key(monkeypatch):
    monkeypatch.setenv("VENICE_LOOM_KEY", "loom-key")
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert backends.get_backend("venice")._client.api_key == "loom-key"


def test_venice_backend_falls_back_to_the_shared_key(monkeypatch):
    monkeypatch.delenv("VENICE_LOOM_KEY", raising=False)
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert backends.get_backend("venice")._client.api_key == "default-key"


def test_explicit_api_key_wins_over_both(monkeypatch):
    monkeypatch.setenv("VENICE_LOOM_KEY", "loom-key")
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert backends.get_backend("venice", api_key="explicit")._client.api_key == "explicit"


def test_blank_loom_key_falls_through(monkeypatch):
    monkeypatch.setenv("VENICE_LOOM_KEY", "")
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert backends.get_backend("venice")._client.api_key == "default-key"


@pytest.mark.parametrize("blank", [False, True], ids=["absent", "blank"])
def test_venice_backend_raises_when_no_key_source_is_set(monkeypatch, blank):
    # No explicit arg, and neither env var is usable: VeniceClient must refuse at
    # construction (VeniceError), not later at the first call, and its message
    # must name the preferred loom var first so the operator sets the right one.
    from loom.venice import VeniceError
    for name in ("VENICE_LOOM_KEY", "VENICE_API_KEY"):
        if blank:
            monkeypatch.setenv(name, "")
        else:
            monkeypatch.delenv(name, raising=False)
    with pytest.raises(VeniceError) as exc:
        backends.get_backend("venice")
    msg = str(exc.value)
    assert msg.index("VENICE_LOOM_KEY") < msg.index("VENICE_API_KEY")
