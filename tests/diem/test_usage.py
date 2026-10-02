"""`diem venice-usage --days N` has to mean N days.

It used to read the `trailingSevenDays` block of `GET /api/v1/api_keys`, a fixed
figure Venice computes for its own window — so `--days 1` and `--days 7` printed
identical DIEM columns, and no value of N ever changed them. This is the
rewrite against `/billing/usage-history`, which takes a real start and end.
"""
from datetime import datetime

import pytest

from diem.usage import UsageClient, UsageUnavailable


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def line(sku, amount, rid):
    return {"sku": sku, "notes": "API Inference", "amount": -amount,
            "currency": "DIEM", "timestamp": "2026-09-11T03:08:00.423Z",
            "units": 0.001, "pricePerUnitUsd": 1.0,
            "inferenceDetails": {"requestId": rid, "promptTokens": 10,
                                 "completionTokens": 2}}


def _client(pages=None, exc=None, calls=None):
    pages = list(pages or [FakeResp(body={"data": []})])

    def get(url, headers=None, params=None, timeout=None):
        assert headers["Authorization"].startswith("Bearer ")
        assert "billing/usage-history" in url
        if calls is not None:
            calls.append(dict(params or {}))
        if exc:
            raise exc
        return pages.pop(0) if pages else FakeResp(body={"data": []})

    return UsageClient("sk-admin", get=get)


WINDOW = {"start": datetime(2026, 9, 9, 12, 0, 0), "end": datetime(2026, 9, 12, 12, 0, 0)}


def test_the_window_asked_of_venice_is_the_window_the_caller_gave():
    calls = []
    _client(calls=calls).billed_by_model(**WINDOW)
    assert calls[0]["startTimestamp"] == "2026-09-09T12:00:00Z"
    assert calls[0]["endTimestamp"] == "2026-09-12T12:00:00Z"
    assert calls[0]["currency"] == "DIEM"


def test_a_one_day_window_and_a_seven_day_window_are_not_the_same_request():
    calls = []
    c = _client(calls=calls)
    c.billed_by_model(start=datetime(2026, 9, 11), end=datetime(2026, 9, 12))
    c.billed_by_model(start=datetime(2026, 9, 5), end=datetime(2026, 9, 12))
    assert calls[0]["startTimestamp"] != calls[1]["startTimestamp"]


def test_the_follow_up_page_sends_the_cursor_and_nothing_else():
    """The documented 400: `cursor` alongside the original filters is rejected."""
    calls = []
    pages = [FakeResp(body={"data": [line("grok-4-3-llm-input-mtoken", 1.0, "r1")],
                            "nextCursor": "abc"}),
             FakeResp(body={"data": [line("grok-4-3-llm-output-mtoken", 2.0, "r1")]})]
    _client(pages=pages, calls=calls).billed_by_model(**WINDOW)
    assert calls[1] == {"cursor": "abc"}


def test_billed_diem_is_summed_per_model_with_the_serving_suffix_stripped():
    rows = [line("deepseek-v4-pro-api-llm-input-mtoken", 1.0, "r1"),
            line("deepseek-v4-pro-api-llm-output-mtoken", 0.5, "r1"),
            line("deepseek-v4-pro-api-llm-input-mtoken", 2.0, "r2"),
            line("claude-fable-5-1-llm-output-mtoken", 38.5221, "r3")]
    out = _client([FakeResp(body={"data": rows})]).billed_by_model(**WINDOW)
    assert out["deepseek-v4-pro"]["diem"] == pytest.approx(3.5)
    assert out["deepseek-v4-pro"]["calls"] == 2          # distinct request ids
    assert out["claude-fable-5-1"]["diem"] == pytest.approx(38.5221)


def test_a_flat_fee_sku_is_bucketed_under_its_own_name_not_guessed_at():
    rows = [{"sku": "search-augmentation", "amount": -0.01, "currency": "DIEM",
             "timestamp": "2026-09-11T03:08:00.423Z",
             "inferenceDetails": {"requestId": "r1"}}]
    out = _client([FakeResp(body={"data": rows})]).billed_by_model(**WINDOW)
    assert "search-augmentation" in out


def test_http_error_raises_unavailable():
    with pytest.raises(UsageUnavailable):
        _client([FakeResp(status=500)]).billed_by_model(**WINDOW)


def test_network_error_raises_unavailable():
    with pytest.raises(UsageUnavailable):
        _client(exc=ConnectionError("down")).billed_by_model(**WINDOW)


@pytest.mark.parametrize("body", [
    {"data": None},
    ["not", "a", "dict"],
    {"data": ["not-a-dict-item"]},
    {"data": [{"sku": "grok-4-3-llm-input-mtoken", "amount": "not-a-number"}]},
])
def test_malformed_200_bodies_raise_unavailable(body):
    with pytest.raises(UsageUnavailable):
        _client([FakeResp(body=body)]).billed_by_model(**WINDOW)


def test_a_runaway_pagination_loop_is_bounded():
    forever = [FakeResp(body={"data": [line("grok-4-3-llm-input-mtoken", 0.1, "r")],
                              "nextCursor": "next"}) for _ in range(50)]
    client = UsageClient("sk-admin", max_pages=3,
                         get=lambda *a, **k: forever.pop(0))
    client.billed_by_model(**WINDOW)
    assert len(forever) == 47
