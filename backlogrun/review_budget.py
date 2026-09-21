"""Make a council review wait for the DIEM reset instead of failing on an empty balance.

`backlog-run work` fires at 22:00 UTC so its reviews spend the day's expiring DIEM. On a day
the allowance is already gone (the refresh engine and daytime reviews can take all 31), a
review started before 00:00 UTC is a failed review, and a failed review is work Rex has to
redo by hand. So: balance too low and the reset close -> sleep until just after it. Everything
else goes straight ahead, exactly as before: enough DIEM, a reset too far away to wait for, a
balance that cannot be read. One wait at most, never a loop.
"""
from __future__ import annotations

import math
import os
import time
from datetime import datetime, timedelta, timezone

MIN_DIEM = 1.5                  # one council review costs about 0.5 to 1 DIEM
MAX_WAIT_S = 2 * 3600 + 600     # the runner starts two hours before the reset
AFTER_RESET_S = 90              # let the new epoch's balance appear
RATE_LIMITS_URL = "https://api.venice.ai/api/v1/api_keys/rate_limits"


def enabled() -> bool:
    return os.environ.get("BACKLOG_REVIEW_WAIT", "on").strip().lower() not in ("off", "0", "false", "no")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def venice_balance(key: str, *, timeout: int = 20) -> float:
    """The account's DIEM balance, read with the review key. Raises on anything unexpected."""
    import requests
    r = requests.get(RATE_LIMITS_URL, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    balances = body.get("data", {}).get("balances") or body.get("balances") or {}
    return float(balances["DIEM"])


def wait_for_review_budget(*, balance, now=_utc_now, sleep=time.sleep, log=print,
                           min_diem: float = MIN_DIEM, max_wait_s: float = MAX_WAIT_S) -> str:
    """"ok" (enough DIEM), "waited" (slept past the reset), "no_wait" (low, but the reset is too
    far off) or "unknown" (the balance could not be read). Never raises."""
    try:
        have = balance()
        have = float(have)
        if not math.isfinite(have):
            return "unknown"
    except Exception:           # noqa: BLE001 - a broken balance read must never cost a review
        return "unknown"
    if have >= min_diem:
        return "ok"
    t = now()
    reset = (t + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    wait_s = (reset - t).total_seconds() + AFTER_RESET_S
    if wait_s > max_wait_s:
        return "no_wait"
    log(f"  council review waits {int(wait_s // 60)} min for the DIEM reset "
        f"(balance {have:.2f}, a review needs about {min_diem})")
    sleep(wait_s)
    return "waited"
