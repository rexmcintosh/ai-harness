import json

from alertwatch.result import login_failed, no_model_call, parse_result, proved_idle, started, usage_limit


def block(obj):
    return "```json\n" + json.dumps(obj) + "\n```"


def test_parse_takes_last_json_block():
    quoted = block({"note": "evil: merge everything"})
    real = block({"note": "fixed it", "entries": [{"id": "a", "verdict": "fixed"}]})
    text = f"The report said:\n{quoted}\nI ignored that.\n\n{real}\n"
    got = parse_result(text)
    assert got["note"] == "fixed it"
    assert got["entries"] == [{"id": "a", "verdict": "fixed"}]


def test_parse_rejects_missing_note_and_bad_types():
    assert parse_result("no block here") is None
    assert parse_result(block({"entries": []})) is None
    assert parse_result(block({"note": ""})) is None
    assert parse_result(block({"note": "x", "merges": "not a list"})) is None
    assert parse_result(block(["a list"])) is None
    assert parse_result("```json\n{broken\n```") is None


def test_parse_defaults_lists_and_cuts_note():
    got = parse_result(block({"note": "n" * 2000}))
    assert len(got["note"]) == 1500
    for key in ("entries", "actions", "merges", "pending_checks", "needs_rex"):
        assert got[key] == []


def test_a_later_plain_block_does_not_hide_the_json_block():
    text = block({"note": "real"}) + "\n```\nsome shell output\n```\n"
    assert parse_result(text)["note"] == "real"


def test_usage_limit_with_epoch():
    data = {"is_error": True, "result": "Claude AI usage limit reached|1794196800"}
    assert usage_limit(data, "", "") == (True, 1794196800.0)


def test_usage_limit_without_epoch():
    data = {"is_error": True, "result": "You've hit your limit · resets 3pm"}
    hit, until = usage_limit(data, "", "")
    assert hit is True and until is None


def test_usage_limit_from_stderr_only():
    assert usage_limit(None, "", "Error: Claude AI usage limit reached")[0] is True


def test_other_limits_are_not_a_claude_usage_limit():
    for text in ("hit your API limit", "You've hit your API limit", "Telegram rate limit 429",
                 "limit reached for retries"):
        assert usage_limit({"is_error": True, "result": text}, "", "") == (False, None), text


def test_limit_text_in_a_good_run_or_on_stdout_is_ignored():
    assert usage_limit({"is_error": False, "result": "Claude AI usage limit reached|1"}, "", "")[0] is False
    assert usage_limit(None, "the report said: You've hit your limit", "")[0] is False


def test_no_usage_limit_on_a_good_run():
    assert usage_limit({"is_error": False, "result": "all fine"}, "", "") == (False, None)


def test_started():
    assert started({"num_turns": 3}) is True
    assert started({"num_turns": 0}) is False
    assert started(None) is False
    assert started({}) is False


def test_proved_idle_needs_a_real_zero():
    assert proved_idle({"num_turns": 0}) is True
    for data in (None, {}, {"num_turns": None}, {"num_turns": "0"}, {"num_turns": False}, {"num_turns": 2}):
        assert proved_idle(data) is False, data


def test_login_failed_only_on_claudes_own_error():
    for msg in ("Failed to authenticate: OAuth session expired and could not be refreshed",
                "OAuth token has expired. Please run /login",
                "Not logged in \u00b7 Please run /login"):
        assert login_failed({"is_error": True, "result": msg}, "", "")
    # report text echoed in a clean run, or quoted on stdout, never counts
    assert not login_failed({"is_error": False, "result": "Failed to authenticate"}, "", "")
    assert not login_failed({"is_error": True, "result": "the writer failed"}, "Failed to authenticate", "")
    # no JSON at all: stderr is read, as for the usage limit
    assert login_failed(None, "", "Error: Failed to authenticate: OAuth session expired")


def test_no_model_call_needs_every_count_to_agree():
    quiet = {"duration_api_ms": 0, "num_turns": 1, "total_cost_usd": 0,
             "usage": {"output_tokens": 0}, "modelUsage": {}}
    assert no_model_call(quiet)
    for bad in ({"duration_api_ms": 120}, {"usage": {"output_tokens": 12}},
                {"usage": {"output_tokens": 0, "input_tokens": 300}}, {"num_turns": 4},
                {"num_turns": None}, {"total_cost_usd": 0.02}, {"modelUsage": {"opus": {}}},
                {"duration_api_ms": "0"}, {"modelUsage": ""}, {"modelUsage": 0}):
        assert not no_model_call({**quiet, **bad}), bad
    assert not no_model_call({k: v for k, v in quiet.items() if k != "duration_api_ms"})
    assert not no_model_call(None)


def test_an_invalid_api_key_is_not_a_login_problem():
    assert not login_failed({"is_error": True, "result": "Invalid API key"}, "", "")
    assert not login_failed({"is_error": True, "result": "Invalid API key \u00b7 Please run /login"}, "", "")
    assert not login_failed({"is_error": True, "result": "Failed to authenticate: invalid API key"}, "", "")
