import json

from alertwatch.result import parse_result, started, usage_limit


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
    assert usage_limit(None, "", "Error: usage limit reached")[0] is True


def test_no_usage_limit_on_a_good_run():
    assert usage_limit({"is_error": False, "result": "all fine"}, "", "") == (False, None)


def test_started():
    assert started({"num_turns": 3}) is True
    assert started({"num_turns": 0}) is False
    assert started(None) is False
    assert started({}) is False
