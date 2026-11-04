"""Live DIEM balance. Spec rule: if this is unreachable, the checkpoint
aborts — never drain blind."""
from __future__ import annotations
import math
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
        return self.snapshot()["diem"]

    def snapshot(self) -> dict:
        """{"diem": float, "next_epoch": str or None}. The next-epoch time is a bonus: without
        it the balance is still good."""
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
            data = body.get("data") or {}
            balances = data.get("balances") or body.get("balances") or {}
            if "DIEM" not in balances:
                raise BalanceUnavailable(f"no DIEM in balances: {body!r:.200}")
            diem = float(balances["DIEM"])
            if not math.isfinite(diem):      # NaN is never under a floor: it would read as "plenty"
                raise BalanceUnavailable(f"DIEM is not a number: {diem}")
            return {"diem": diem, "next_epoch": data.get("nextEpochBegins") or body.get("nextEpochBegins")}
        except BalanceUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            raise BalanceUnavailable(f"unparseable balances envelope: {e}") from e
