import pytest
from council.venice import VeniceClient, VeniceError


def _resp(content, status=200, usage=None):
    body = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage

    class R:
        status_code = status
        def json(self): return body
        def raise_for_status(self):
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")
    return R()


def test_complete_returns_content():
    calls = []
    def fake_post(url, **kw):
        calls.append((url, kw))
        return _resp('{"ok": true}')
    c = VeniceClient(api_key="k", post=fake_post)
    out = c.complete("model-x", "sys", "usr")
    assert out == '{"ok": true}'
    assert calls[0][1]["json"]["model"] == "model-x"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer k"


def test_complete_retries_then_raises():
    attempts = {"n": 0}
    def flaky_post(url, **kw):
        attempts["n"] += 1
        raise ConnectionError("nope")
    c = VeniceClient(api_key="k", post=flaky_post, retries=2, backoff=0)
    with pytest.raises(VeniceError):
        c.complete("m", "s", "u")
    assert attempts["n"] == 3  # 1 try + 2 retries


def test_complete_scrubs_api_key_from_prompt():
    sent = {}
    def fake_post(url, **kw):
        sent["messages"] = kw["json"]["messages"]
        return _resp('{"ok": true}')
    c = VeniceClient(api_key="sk-secret-123", post=fake_post)
    c.complete("m", "system has sk-secret-123 in it", "user also sk-secret-123")
    blob = str(sent["messages"])
    assert "sk-secret-123" not in blob
    assert "<redacted>" in blob


def test_complete_does_not_retry_on_4xx():
    # A 401/400 (auth / bad model) fails identically every time — fail fast,
    # don't burn 3x the billing retrying it.
    attempts = {"n": 0}
    def auth_fail_post(url, **kw):
        attempts["n"] += 1
        return _resp("", status=401)
    c = VeniceClient(api_key="k", post=auth_fail_post, retries=2, backoff=0)
    with pytest.raises(VeniceError):
        c.complete("m", "s", "u")
    assert attempts["n"] == 1  # no retries on 4xx


def test_complete_retries_on_5xx():
    attempts = {"n": 0}
    def server_error_post(url, **kw):
        attempts["n"] += 1
        return _resp("", status=503)
    c = VeniceClient(api_key="k", post=server_error_post, retries=2, backoff=0)
    with pytest.raises(VeniceError):
        c.complete("m", "s", "u")
    assert attempts["n"] == 3  # 503 is retryable


# --- usage-ledger instrumentation (Phase C) ---------------------------------

def _last_usage_row(db_path):
    import venice_usage
    row = venice_usage.connect(db_path).execute(
        "SELECT project, task_type, model, tokens_in, tokens_out, source "
        "FROM usage ORDER BY id DESC LIMIT 1").fetchone()
    return row


def test_complete_logs_one_usage_row_with_expected_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "l.db"))
    def fake_post(url, **kw):
        return _resp("hi", usage={"prompt_tokens": 123, "completion_tokens": 45})
    c = VeniceClient(api_key="k", post=fake_post)
    c.complete("model-x", "sys", "usr", task_type="ask")
    row = _last_usage_row(tmp_path / "l.db")
    assert row == ("council", "ask", "model-x", 123, 45, "council/venice")


def test_complete_defaults_task_type_to_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "l.db"))
    def fake_post(url, **kw):
        return _resp("hi", usage={"prompt_tokens": 10, "completion_tokens": 5})
    c = VeniceClient(api_key="k", post=fake_post)
    c.complete("model-x", "sys", "usr")  # task_type omitted
    row = _last_usage_row(tmp_path / "l.db")
    assert row[1] == "chat"


def test_complete_logs_zero_tokens_when_usage_missing(tmp_path, monkeypatch):
    # Not every response envelope includes a `usage` block — must not raise,
    # and must degrade to 0/0 rather than skip the row.
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "l.db"))
    def fake_post(url, **kw):
        return _resp("hi")  # no usage=... passed
    c = VeniceClient(api_key="k", post=fake_post)
    out = c.complete("model-x", "sys", "usr", task_type="review")
    assert out == "hi"
    row = _last_usage_row(tmp_path / "l.db")
    assert row == ("council", "review", "model-x", 0, 0, "council/venice")


def test_complete_appends_exactly_one_row_per_call(tmp_path, monkeypatch):
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "l.db"))
    def fake_post(url, **kw):
        return _resp("hi", usage={"prompt_tokens": 1, "completion_tokens": 1})
    c = VeniceClient(api_key="k", post=fake_post)
    c.complete("m", "s", "u")
    c.complete("m", "s", "u")
    import venice_usage
    n = venice_usage.connect(tmp_path / "l.db").execute(
        "SELECT count(*) FROM usage").fetchone()[0]
    assert n == 2


def test_complete_logging_failure_does_not_propagate(tmp_path, monkeypatch):
    # A broken ledger (raises on append) must be completely invisible to the caller —
    # complete() still returns the content normally.
    import venice_usage
    def boom(*a, **k):
        raise RuntimeError("ledger is on fire")
    monkeypatch.setattr(venice_usage, "append", boom)
    def fake_post(url, **kw):
        return _resp("still works", usage={"prompt_tokens": 1, "completion_tokens": 1})
    c = VeniceClient(api_key="k", post=fake_post)
    out = c.complete("m", "s", "u")
    assert out == "still works"


def test_complete_logging_survives_missing_venice_usage_package(tmp_path, monkeypatch):
    # If the package itself can't be imported, the call must still succeed.
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *a, **k):
        if name == "venice_usage":
            raise ImportError("no such package")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    def fake_post(url, **kw):
        return _resp("ok", usage={"prompt_tokens": 1, "completion_tokens": 1})
    c = VeniceClient(api_key="k", post=fake_post)
    assert c.complete("m", "s", "u") == "ok"


def test_requires_key_and_names_the_council_var_first():
    with pytest.raises(VeniceError) as exc:
        VeniceClient(api_key="")
    msg = str(exc.value)
    assert msg.index("VENICE_COUNCIL_KEY") < msg.index("VENICE_API_KEY")


# ── forced JSON is a per-model transport fact, decided in ONE place ───────────────────────
# Venice's `response_format: json_object` garbles deepseek-v4-pro's first key and the model
# often stops there (0 of 4 usable with it on, 4 of 4 clean with it off, 2026-09-19). The
# rule lives in the client, keyed by model, so every caller is covered with no new argument:
# panel seats, the chair, the router, compare, the sweep, and the per-repo CI shims.
def _payloads():
    sent = []

    def fake_post(url, **kw):
        sent.append(kw["json"])
        return _resp('{"ok": true}')
    return sent, fake_post


def test_forced_json_is_not_sent_to_a_model_it_corrupts():
    sent, post = _payloads()
    c = VeniceClient(api_key="k", post=post)
    c.complete("deepseek-v4-pro", "sys", "usr")
    c.complete("grok-4-3", "sys", "usr")
    c.complete("deepseek-v4-flash", "sys", "usr")            # a different model: unaffected
    assert "response_format" not in sent[0]
    assert sent[1]["response_format"] == {"type": "json_object"}
    assert sent[2]["response_format"] == {"type": "json_object"}


def test_json_mode_false_still_sends_no_response_format_for_any_model():
    sent, post = _payloads()
    VeniceClient(api_key="k", post=post).complete("grok-4-3", "sys", "usr", json_mode=False)
    assert "response_format" not in sent[0]


def test_the_no_forced_json_list_can_be_replaced_per_client():
    sent, post = _payloads()
    c = VeniceClient(api_key="k", post=post, no_forced_json=("model-y",))
    c.complete("deepseek-v4-pro", "sys", "usr")
    c.complete("model-y-large", "sys", "usr")
    assert "response_format" in sent[0] and "response_format" not in sent[1]


def test_the_no_forced_json_list_must_be_model_names_not_a_bare_string():
    # A bare string would be iterated as characters and match nearly every model.
    with pytest.raises(TypeError, match="model names"):
        VeniceClient(api_key="k", no_forced_json="deepseek-v4-pro")

