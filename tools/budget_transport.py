"""A spend-capped HTTP `post` for `VeniceClient`, and the ledger of what it spent.

`tools/regress/README.md` has always documented `--transport-hook
/absolute/path/to/budget_transport.py:post`, and until now no such file existed
(`docs/chair-bakeoff-design-2026-09-11.md` §6 says so in as many words). This is
that file.

The contract, from the regress README: a module-level callable with
`requests.post`'s signature, handed to `VeniceClient(post=...)`. It really does
call Venice, so a client wired to it must also pass `transport_is_real=True` —
otherwise `venice_usage.guard` correctly refuses the ledger row (see that
module's docstring for the incident that guard exists for).

What the cap is, and what it is not
-----------------------------------

It is a **local running total, checked before each call**: once the calls this
process has already made are worth `BUDGET_TRANSPORT_CEILING_DIEM` or more, the
next call raises instead of being sent. It is not a Venice-side limit. A single
call already in flight cannot be clawed back, so the true worst case is the
ceiling plus one call — size the ceiling with that in mind.

Cost comes from the response's own `usage` block priced through
`venice_usage.pricing`, which is generated from the live catalogue. Prompt
caching is not modelled (Venice may bill part of a prompt as a cache read), so
the total reads slightly high; a guard that guesses low is not a guard.

A response with no usable `usage` block is charged `BUDGET_TRANSPORT_BLIND_DIEM`
(default 0.5) rather than zero, so an unpriceable model cannot spend forever.

Environment
-----------
  BUDGET_TRANSPORT_CEILING_DIEM  hard stop, DIEM (default 3.50)
  BUDGET_TRANSPORT_LOG           JSONL file, one line per call (optional)
  BUDGET_TRANSPORT_BLIND_DIEM    charge for an unpriceable response (default 0.50)
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import requests

_LOCK = threading.Lock()

#: Running DIEM this process has spent through `post`, and how many calls made it.
STATE: dict[str, float] = {"spent": 0.0, "calls": 0.0, "refused": 0.0}


class BudgetExceeded(RuntimeError):
    """Raised instead of sending a call that would spend past the ceiling."""


def _ceiling() -> float:
    return float(os.environ.get("BUDGET_TRANSPORT_CEILING_DIEM", "3.50"))


def _blind_charge() -> float:
    return float(os.environ.get("BUDGET_TRANSPORT_BLIND_DIEM", "0.50"))


def _price(model, usage) -> tuple[float | None, int, int]:
    tokens_in = int((usage or {}).get("prompt_tokens") or 0)
    tokens_out = int((usage or {}).get("completion_tokens") or 0)
    if not tokens_in and not tokens_out:
        return None, tokens_in, tokens_out
    try:
        from venice_usage.pricing import estimate_usd

        return estimate_usd(model, tokens_in, tokens_out), tokens_in, tokens_out
    except Exception:
        return None, tokens_in, tokens_out


def _record(entry: dict) -> None:
    path = os.environ.get("BUDGET_TRANSPORT_LOG")
    if not path:
        return
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as stream:
            stream.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception:
        pass  # Never let bookkeeping break or slow the call it is bookkeeping.


def post(url, **kwargs):
    """`requests.post`, refused once this process has spent past the ceiling."""
    ceiling = _ceiling()
    with _LOCK:
        spent = STATE["spent"]
        if spent >= ceiling:
            STATE["refused"] += 1
            raise BudgetExceeded(
                f"budget transport: refusing to call Venice — {spent:.4f} DIEM already "
                f"spent this process against a {ceiling:.4f} DIEM ceiling"
            )
    payload = kwargs.get("json") or {}
    model = payload.get("model", "?") if isinstance(payload, dict) else "?"
    started = time.monotonic()
    response = requests.post(url, **kwargs)
    elapsed = time.monotonic() - started

    usage = None
    try:
        usage = (response.json() or {}).get("usage")
    except Exception:
        usage = None
    cost, tokens_in, tokens_out = _price(model, usage)
    blind = cost is None
    charged = _blind_charge() if blind else float(cost)
    with _LOCK:
        STATE["spent"] += charged
        STATE["calls"] += 1
        running = STATE["spent"]
    _record({
        "ts": time.time(), "model": model,
        "status": getattr(response, "status_code", None),
        "tokens_in": tokens_in, "tokens_out": tokens_out,
        "diem": round(charged, 6), "blind_charge": blind,
        "running_diem": round(running, 6), "seconds": round(elapsed, 3),
    })
    return response
