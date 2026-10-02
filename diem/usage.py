"""What Venice actually billed, over the window the caller asked for.

This module used to read `GET /api/v1/api_keys` and take the `trailingSevenDays`
block off each key. That is a figure Venice computes for its own fixed window,
so `diem venice-usage --days N` printed the same DIEM numbers for every N:
`--days 1` and `--days 7` were byte-identical, and the flag was decoration.
`GET /billing/usage-history` takes a real `startTimestamp` and `endTimestamp`,
so the window now means what it says.

What the move costs, and why it is worth it. Usage-history line items carry only
`sku`, `amount`, `timestamp`, `units` and `inferenceDetails` — no API key id,
no key name, no project tag (verified against live rows on 2026-09-12). So
billed spend can no longer be split per project the way per-key totals allowed.
In exchange the number is real, and the axis both sides genuinely share — the
model — is a better join anyway: a model Venice billed that the ledger never
recorded is exactly the `claude-fable-5-1` hole, 38.52 DIEM of output over three
weeks with no ledger row behind it.

Read-only cross-check for the ledger — it never gates anything, so a failure
degrades the report instead of breaking it.

CURSOR TRAP, the same one `venice_usage.refresh_prices` documents: the response
carries `nextCursor`, but the follow-up request must send ONLY `cursor=<value>`.
Sending it alongside the original filters is a 400.
"""
from __future__ import annotations

from datetime import datetime

import requests

BILLING_URL = "https://api.venice.ai/api/v1/billing/usage-history"

#: Venice rejects a pageSize below 10; 200 is the documented ceiling.
PAGE_SIZE = 200


class UsageUnavailable(RuntimeError):
    pass


def _iso_z(when) -> str:
    """A naive-UTC datetime (or a string, passed through) as Venice wants it."""
    if isinstance(when, datetime):
        return when.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(when)


def model_of(sku: str) -> str:
    """The model a SKU belongs to.

    `<model>-llm-<kind>-mtoken` is parsed by the same code the price table uses,
    and the serving suffix (`deepseek-v4-pro-api`) is stripped to the id the
    ledger logs. A SKU that is not a token charge at all — `search-augmentation`
    is billed per web search — is bucketed under its own name rather than
    guessed into some model's total."""
    from venice_usage.reconcile import strip_route
    from venice_usage.refresh_prices import parse_sku
    parsed = parse_sku(sku)
    return strip_route(parsed[0]) if parsed else (sku or "(unknown)")


class UsageClient:
    def __init__(self, admin_key: str, *, get=None, timeout: int = 30,
                 max_pages: int = 500):
        self.admin_key = admin_key
        self.timeout = timeout
        self.max_pages = max_pages
        self._get = get or requests.get

    def _page(self, params: dict) -> dict:
        try:
            r = self._get(BILLING_URL,
                          headers={"Authorization": f"Bearer {self.admin_key}"},
                          params=params, timeout=self.timeout)
        except Exception as e:  # noqa: BLE001
            raise UsageUnavailable(f"usage-history unreachable: {e}") from e
        if getattr(r, "status_code", 200) != 200:
            raise UsageUnavailable(f"usage-history HTTP {r.status_code}")
        try:
            body = r.json()
        except Exception as e:  # noqa: BLE001
            raise UsageUnavailable(f"usage-history non-JSON: {e}") from e
        if not isinstance(body, dict):
            raise UsageUnavailable(f"unexpected usage-history envelope: {body!r:.200}")
        return body

    def line_items(self, *, start, end) -> list[dict]:
        """Every billed line item between `start` and `end`."""
        params = {"currency": "DIEM", "startTimestamp": _iso_z(start),
                  "endTimestamp": _iso_z(end), "pageSize": PAGE_SIZE}
        rows: list[dict] = []
        for _ in range(self.max_pages):
            body = self._page(params)
            batch = body.get("data")
            if batch is None or not isinstance(batch, list):
                raise UsageUnavailable(f"unexpected usage-history data: {batch!r:.200}")
            rows.extend(batch)
            cursor = body.get("nextCursor")
            if not cursor or not batch:
                break
            params = {"cursor": cursor}          # cursor alone. Nothing else.
        return rows

    def billed_by_model(self, *, start, end) -> dict[str, dict]:
        """Billed DIEM and call count per model over the real window.

        `calls` counts distinct request ids, not line items: one call is charged
        two to four SKUs (input, output, and Venice's injected cache write)."""
        out: dict[str, dict] = {}
        seen: dict[str, set] = {}
        for r in self.line_items(start=start, end=end):
            if not isinstance(r, dict):
                raise UsageUnavailable(f"unparseable usage-history row: {r!r:.200}")
            try:
                amount = abs(float(r.get("amount") or 0.0))
            except (TypeError, ValueError) as e:
                raise UsageUnavailable(f"unparseable usage-history amount: {e}") from e
            model = model_of(r.get("sku", ""))
            cell = out.setdefault(model, {"diem": 0.0, "calls": 0, "line_items": 0})
            cell["diem"] = round(cell["diem"] + amount, 8)
            cell["line_items"] += 1
            rid = (r.get("inferenceDetails") or {}).get("requestId")
            ids = seen.setdefault(model, set())
            if rid and rid not in ids:
                ids.add(rid)
                cell["calls"] += 1
        return out
