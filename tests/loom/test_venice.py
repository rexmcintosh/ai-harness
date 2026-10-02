# tests/loom/test_venice.py
import pytest
from loom.venice import VeniceClient, VeniceError


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
    def json(self):
        return self._payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _ok(content="HELLO", usage=None):
    body = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return body


def test_requires_key():
    with pytest.raises(VeniceError):
        VeniceClient(api_key="")


def test_complete_returns_content_and_sends_key_in_header_only():
    seen = {}
    def fake_post(url, headers=None, json=None, timeout=None):
        seen["headers"] = headers
        seen["body"] = json
        return _Resp(200, _ok("WOVEN"))
    c = VeniceClient(api_key="sk-test-123", post=fake_post)
    out = c.complete("claude-opus-4-8", "sys", "user text", json_mode=False)
    assert out == "WOVEN"
    assert seen["headers"]["Authorization"] == "Bearer sk-test-123"
    import json as _j
    assert "sk-test-123" not in _j.dumps(seen["body"])


def test_4xx_is_not_retried():
    calls = {"n": 0}
    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _Resp(400)
    c = VeniceClient(api_key="k", post=fake_post, retries=2, backoff=0)
    with pytest.raises(VeniceError):
        c.complete("m", "s", "u")
    assert calls["n"] == 1


def test_5xx_is_retried_then_fails():
    calls = {"n": 0}
    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _Resp(503)
    c = VeniceClient(api_key="k", post=fake_post, retries=2, backoff=0)
    with pytest.raises(VeniceError):
        c.complete("m", "s", "u")
    assert calls["n"] == 3   # 1 + 2 retries


# --- usage-ledger instrumentation (Phase C) ---------------------------------

def _last_usage_row(db_path):
    import venice_usage
    return venice_usage.connect(db_path).execute(
        "SELECT project, task_type, model, tokens_in, tokens_out, source "
        "FROM usage ORDER BY id DESC LIMIT 1").fetchone()


def test_complete_logs_one_usage_row_with_expected_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "l.db"))
    def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp(200, _ok("WOVEN", usage={"prompt_tokens": 200, "completion_tokens": 80}))
    c = VeniceClient(api_key="k", post=fake_post)
    c.complete("claude-opus-4-8", "sys", "user text")
    row = _last_usage_row(tmp_path / "l.db")
    assert row == ("loom", "weave", "claude-opus-4-8", 200, 80, "loom/venice")


def test_complete_logs_zero_tokens_when_usage_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "l.db"))
    def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp(200, _ok("WOVEN"))  # no usage block
    c = VeniceClient(api_key="k", post=fake_post)
    out = c.complete("m", "s", "u")
    assert out == "WOVEN"
    row = _last_usage_row(tmp_path / "l.db")
    assert row == ("loom", "weave", "m", 0, 0, "loom/venice")


def test_complete_logging_failure_does_not_propagate(tmp_path, monkeypatch):
    import venice_usage
    def boom(*a, **k):
        raise RuntimeError("ledger is on fire")
    monkeypatch.setattr(venice_usage, "append", boom)
    def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp(200, _ok("still works", usage={"prompt_tokens": 1, "completion_tokens": 1}))
    c = VeniceClient(api_key="k", post=fake_post)
    assert c.complete("m", "s", "u") == "still works"
