import pytest
from diem.balance import BalanceClient, BalanceUnavailable

class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}
    def json(self):
        return self._body

def _client(resp=None, exc=None):
    def get(url, headers=None, timeout=None):
        assert headers["Authorization"].startswith("Bearer ")
        if exc:
            raise exc
        return resp
    return BalanceClient("sk-test", get=get)

def test_parses_nested_data_balances():
    c = _client(FakeResp(body={"data": {"balances": {"DIEM": 42.5, "USD": 1.0}}}))
    assert c.diem_balance() == 42.5

def test_parses_top_level_balances():
    c = _client(FakeResp(body={"balances": {"DIEM": 7}}))
    assert c.diem_balance() == 7.0

def test_http_error_raises_unavailable():
    with pytest.raises(BalanceUnavailable):
        _client(FakeResp(status=500)).diem_balance()

def test_network_error_raises_unavailable():
    with pytest.raises(BalanceUnavailable):
        _client(exc=ConnectionError("down")).diem_balance()

def test_missing_key_raises_unavailable():
    with pytest.raises(BalanceUnavailable):
        _client(FakeResp(body={"data": {}})).diem_balance()

@pytest.mark.parametrize("body", [
    {"data": None, "balances": None},
    ["not", "a", "dict"],
    {"data": {"balances": {"DIEM": "not-a-number"}}},
])
def test_malformed_200_bodies_raise_unavailable(body):
    with pytest.raises(BalanceUnavailable):
        _client(FakeResp(body=body)).diem_balance()
