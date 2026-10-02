"""Live DIEM balance. Spec rule: if this is unreachable, the checkpoint
aborts — never drain blind."""
from __future__ import annotations
import requests

RATE_LIMITS_URL = "https://api.venice.ai/api/v1/api_keys/rate_limits"


class BalanceUnavailable(RuntimeError):
    pass


class BalanceClient:
    def __init__(self, api_key: str, *, get=None, timeout: int = 30):
        self.api_key = api_key
        self.timeout = timeout
        self._get = get or requests.get

    def diem_balance(self) -> float:
        try:
            r = self._get(RATE_LIMITS_URL,
                          headers={"Authorization": f"Bearer {self.api_key}"},
                          timeout=self.timeout)
        except Exception as e:  # noqa: BLE001 — any transport failure = unavailable
            raise BalanceUnavailable(f"rate_limits unreachable: {e}") from e
        if getattr(r, "status_code", 200) != 200:
            raise BalanceUnavailable(f"rate_limits HTTP {r.status_code}")
        try:
            body = r.json()
        except Exception as e:  # noqa: BLE001
            raise BalanceUnavailable(f"rate_limits non-JSON: {e}") from e
        try:
            balances = body.get("data", {}).get("balances") or body.get("balances") or {}
            if "DIEM" not in balances:
                raise BalanceUnavailable(f"no DIEM in balances: {body!r:.200}")
            return float(balances["DIEM"])
        except BalanceUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            raise BalanceUnavailable(f"unparseable balances envelope: {e}") from e
