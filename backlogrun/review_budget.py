"""Make a council review wait for the DIEM reset instead of paying for it.

`backlog-run work` fires at 22:00 UTC so its reviews spend the day's expiring DIEM. On a day
the allowance is already gone (the refresh engine and daytime reviews can take all 31), a
review started before 00:00 UTC is billed in USD on a paid key, or fails on a key with USD
off. Neither is wanted when free allowance arrives within two hours. So: balance too low and
the reset close -> sleep until just after it. Everything else goes straight ahead, exactly as
before: enough DIEM, a reset too far away to wait for, a balance that cannot be read. One
wait at most, never a loop.
"""
from __future__ import annotations

import math
import os
import time
from datetime import datetime, timedelta, timezone

MIN_DIEM = 1.5                  # one council review costs about 0.5 to 1 DIEM
MAX_WAIT_S = 2 * 3600 + 600     # longest time-to-reset worth waiting for (the run starts 2 h before it)
AFTER_RESET_S = 90              # let the new epoch's balance appear

def enabled() -> bool:
    return os.environ.get("BACKLOG_REVIEW_WAIT", "on").strip().lower() not in ("off", "0", "false", "no")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def venice_balance(key: str) -> float:
    """The account's DIEM balance, read with the review key through the drain's own client
    (one place knows Venice's balance endpoint and envelope). Raises when it cannot be read."""
    from diem.balance import BalanceClient
    return BalanceClient(key, timeout=20).diem_balance()


def wait_for_review_budget(*, balance, now=_utc_now, sleep=time.sleep, log=print,
                           min_diem: float = MIN_DIEM, max_wait_s: float = MAX_WAIT_S) -> str:
    """"ok" (enough DIEM), "waited" (slept past the reset), "no_wait" (low, but the reset is too
    far off) or "unknown" (the balance could not be read). Never raises."""
    try:
        have = float(balance())
        if not math.isfinite(have):
            raise ValueError("balance is not a finite number")
    except Exception as exc:    # noqa: BLE001 - a broken balance read must never cost a review
        # Said out loud, or a revoked scope would switch the guard off for good with no trace.
        # The type only: an HTTP error message can carry the key.
        log(f"  DIEM balance not readable ({type(exc).__name__}); council review goes ahead")
        return "unknown"
    if have >= min_diem:
        return "ok"
    t = now()
    reset = (t + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    to_reset = (reset - t).total_seconds()
    if to_reset > max_wait_s:
        return "no_wait"
    wait_s = to_reset + AFTER_RESET_S
    log(f"  council review waits {int(wait_s // 60)} min for the DIEM reset "
        f"(balance {have:.2f}, a review needs about {min_diem})")
    sleep(wait_s)
    return "waited"
