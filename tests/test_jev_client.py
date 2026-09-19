"""`jev`: the one place that talks to TypeSafe. Every caller, in any repo, goes through it.

What every caller gets for free, and what these tests pin down: a pinned model version, a
refusal of replies from any other version, no redirects, a key that is never printed, a
global off switch that is on in every test, fail-open helpers, and a usage ledger.
"""
import io
import json

import pytest

import jev
from jev import client, usage


def reply(answers=None, model=None, tokens=120):
    return {"model": model or jev.MODEL, "usage": {"input_tokens": tokens, "output_tokens": 0},
            "answers": answers or {"q": {"type": "noul", "noul": 0.9}}}


@pytest.fixture
def live(monkeypatch, tmp_path):
    """Opt one test in to the 'enabled' path. The network is still fake: see `transport=`."""
    monkeypatch.delenv("JEV_DISABLED", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    return tmp_path


# --- question builders ------------------------------------------------------

def test_builders_make_the_three_typed_questions():
    assert jev.noul("Is it urgent?", true="deadline today", false="no deadline") == {
        "type": "noul", "instructions": "Is it urgent?", "criteria": {"true": "deadline today", "false": "no deadline"}}
    assert jev.noul("Is it urgent?") == {"type": "noul", "instructions": "Is it urgent?"}
    assert jev.choice("Which team?", {"billing": "payments", "none": None})["criteria"] == {"billing": "payments", "none": None}
    assert jev.score("How angry?", ["Calm", "Angry"])["criteria"] == ["Calm", "Angry"]


def test_a_score_needs_two_levels_and_a_choice_needs_two_options():
    with pytest.raises(ValueError):
        jev.score("x", ["only one"])
    with pytest.raises(ValueError):
        jev.choice("x", {"only": None})


# --- ask --------------------------------------------------------------------

def test_ask_sends_the_pinned_model_and_returns_answers_tokens_and_cost(live):
    seen = {}
    def transport(req, key, timeout):
        seen.update(req=req, key=key)
        return reply(tokens=1_000_000)
    got = jev.ask("state text", {"q": jev.noul("?")}, project="t", task="unit", transport=transport)
    assert seen["req"] == {"state": "state text", "model": jev.MODEL, "questions": {"q": jev.noul("?")}}
    assert seen["key"] == "test-key"
    assert got["answers"]["q"]["noul"] == 0.9 and got["input_tokens"] == 1_000_000
    assert got["model"] == jev.MODEL and got["cost_usd"] == pytest.approx(0.042)


def test_the_pinned_model_is_an_exact_version_never_a_moving_alias():
    assert jev.MODEL.startswith("jev-") and "latest" not in jev.MODEL and "preview" not in jev.MODEL


def test_a_caller_with_a_measured_threshold_can_pin_its_own_version(live):
    seen = {}
    got = jev.ask("s", {"q": jev.noul("?")}, project="t", task="unit", model="jev-1.13.0",
                  transport=lambda req, key, timeout: seen.update(req) or reply(model="jev-1.13.0"))
    assert seen["model"] == "jev-1.13.0" and got["model"] == "jev-1.13.0"


def test_a_reply_from_another_model_version_is_an_error(live):
    with pytest.raises(jev.JevError, match="jev-9.9.9"):
        jev.ask("s", {"q": jev.noul("?")}, project="t", task="unit",
                transport=lambda *a: reply(model="jev-9.9.9"))


def test_a_reply_missing_an_asked_question_is_an_error(live):
    with pytest.raises(jev.JevError, match="missing"):
        jev.ask("s", {"q": jev.noul("?"), "other": jev.noul("?")}, project="t", task="unit",
                transport=lambda *a: reply())


def test_transient_failures_are_retried_then_raised(live, monkeypatch):
    monkeypatch.setattr(client.time, "sleep", lambda s: None)
    calls = []
    def flaky(req, key, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise client.TransientError("HTTP 529")
        return reply()
    assert jev.ask("s", {"q": jev.noul("?")}, project="t", task="unit", transport=flaky)["answers"]
    assert len(calls) == 3
    calls.clear()
    def down(req, key, timeout):
        calls.append(1)
        raise client.TransientError("HTTP 529")
    with pytest.raises(jev.JevError):
        jev.ask("s", {"q": jev.noul("?")}, project="t", task="unit", transport=down, retries=2)
    assert len(calls) == 3


def test_a_permanent_failure_is_not_retried(live):
    calls = []
    def bad(req, key, timeout):
        calls.append(1)
        raise RuntimeError("HTTP 422 malformed question")
    with pytest.raises(jev.JevError, match="422"):
        jev.ask("s", {"q": jev.noul("?")}, project="t", task="unit", transport=bad)
    assert len(calls) == 1


def test_no_key_is_an_error_that_names_the_variable_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.delenv("JEV_DISABLED", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(client, "ENV_FILE", tmp_path / "no-such-env")
    with pytest.raises(jev.JevError, match="TYPESAFE_API_KEY"):
        jev.ask("s", {"q": jev.noul("?")}, project="t", task="unit", transport=lambda *a: reply())


def test_the_key_is_read_from_the_env_file_as_text(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('OTHER=1\nTYPESAFE_API_KEY="from-file"\n')
    assert client.load_key(env) == "from-file"


def test_an_error_message_never_contains_the_key(live):
    def leaky(req, key, timeout):
        raise RuntimeError(f"boom with {key} inside")
    with pytest.raises(jev.JevError) as err:
        jev.ask("s", {"q": jev.noul("?")}, project="t", task="unit", transport=leaky)
    assert "test-key" not in str(err.value)


# --- the global off switch --------------------------------------------------

def test_every_test_runs_with_jev_disabled():
    # tests/conftest.py sets this for the whole suite: no test reaches TypeSafe by accident.
    assert jev.disabled()


def test_disabled_means_no_transport_call_at_all():
    calls = []
    with pytest.raises(jev.JevDisabled):
        jev.ask("s", {"q": jev.noul("?")}, project="t", task="unit", transport=lambda *a: calls.append(1))
    assert calls == []


def test_the_real_transport_refuses_redirects():
    assert client._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example/") is None


def test_the_real_transport_posts_json_with_a_bearer_key(monkeypatch):
    seen = {}
    class Opener:
        def open(self, request, timeout=None):
            seen.update(url=request.full_url, auth=request.get_header("Authorization"), body=json.loads(request.data))
            return io.BytesIO(json.dumps(reply()).encode())
    monkeypatch.setattr(client, "_OPENER", Opener())
    assert client.http_post({"state": "s"}, "k", 5)["model"] == jev.MODEL
    assert seen == {"url": jev.URL, "auth": "Bearer k", "body": {"state": "s"}}


# --- fail-open helper -------------------------------------------------------

def test_try_ask_returns_none_instead_of_raising(live):
    def boom(*a):
        raise RuntimeError("anything")
    assert jev.try_ask("s", {"q": jev.noul("?")}, project="t", task="unit", transport=boom) is None


def test_try_ask_returns_none_when_disabled():
    assert jev.try_ask("s", {"q": jev.noul("?")}, project="t", task="unit", transport=lambda *a: reply()) is None


# --- many at once -----------------------------------------------------------

def test_ask_many_keeps_order_and_reports_each_failure_in_place(live):
    def transport(req, key, timeout):
        if req["state"] == "bad":
            raise RuntimeError("HTTP 422")
        return reply({"q": {"type": "noul", "noul": 0.1 if req["state"] == "a" else 0.8}})
    out = jev.ask_many([{"id": 1, "state": "a"}, {"id": 2, "state": "bad"}, {"id": 3, "state": "c"}],
                       {"q": jev.noul("?")}, project="t", task="unit", transport=transport, workers=3)
    assert [r["id"] for r in out] == [1, 2, 3]
    assert out[0]["answers"]["q"]["noul"] == 0.1 and "422" in out[1]["error"] and out[2]["answers"]["q"]["noul"] == 0.8


def test_ask_many_accepts_questions_built_per_item(live):
    seen = []
    jev.ask_many([{"id": 1, "state": "s", "options": {"x": None, "y": None}}],
                 lambda item: {"pick": jev.choice("Which?", item["options"])}, project="t", task="unit",
                 transport=lambda req, key, timeout: seen.append(req["questions"]) or reply({"pick": {"type": "choice", "choice": "x"}}))
    assert seen[0]["pick"]["criteria"] == {"x": None, "y": None}


# --- usage ledger -----------------------------------------------------------

def test_every_call_lands_in_the_usage_ledger_with_project_and_task(live, monkeypatch):
    log = live / "usage.jsonl"
    monkeypatch.setenv("JEV_USAGE_LOG", str(log))
    jev.ask("s", {"q": jev.noul("?")}, project="watchdog", task="log-tail", transport=lambda *a: reply(tokens=2000))
    jev.try_ask("s", {"q": jev.noul("?")}, project="watchdog", task="log-tail", transport=lambda *a: 1 / 0)
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert [(r["project"], r["task"], r["ok"], r["input_tokens"]) for r in rows] == [
        ("watchdog", "log-tail", True, 2000), ("watchdog", "log-tail", False, 0)]
    assert rows[0]["model"] == jev.MODEL and rows[0]["cost_usd"] == pytest.approx(2000 * 0.042 / 1e6)
    assert "state" not in rows[0] and "answers" not in rows[0]        # the ledger holds counts, never content


def test_a_broken_ledger_never_breaks_a_call(live, monkeypatch):
    monkeypatch.setenv("JEV_USAGE_LOG", str(live / "is-a-dir"))
    (live / "is-a-dir").mkdir()
    assert jev.ask("s", {"q": jev.noul("?")}, project="t", task="unit", transport=lambda *a: reply())["answers"]


def test_usage_summary_groups_by_project_and_task(tmp_path):
    log = tmp_path / "u.jsonl"
    rows = [{"ts": 1_790_000_000, "project": "a", "task": "x", "ok": True, "input_tokens": 1000, "cost_usd": 0.000042, "seconds": 0.5, "model": "m"},
            {"ts": 1_790_000_100, "project": "a", "task": "x", "ok": False, "input_tokens": 0, "cost_usd": 0.0, "seconds": 0.1, "model": "m"},
            {"ts": 1_790_000_200, "project": "b", "task": "y", "ok": True, "input_tokens": 500, "cost_usd": 0.000021, "seconds": 0.7, "model": "m"}]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
    s = usage.summarize(log)
    assert s[("a", "x")] == {"calls": 2, "errors": 1, "input_tokens": 1000, "cost_usd": pytest.approx(0.000042)}
    assert s[("b", "y")]["calls"] == 1


def test_project_and_task_are_required_so_the_ledger_is_never_anonymous(live):
    with pytest.raises(TypeError):
        jev.ask("s", {"q": jev.noul("?")}, transport=lambda *a: reply())
