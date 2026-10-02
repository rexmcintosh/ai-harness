"""Spike-monitor metrics: pure functions for rate/volume detection.

Where triage.py answers "did something error?", this answers "is something doing far
more than normal?". Same boundary: these are pure functions over collected text/numbers;
the I/O (reading logs, `ps`, the Supabase count HTTP) lives in run.py. Results come back
as the same ``CheckStatus`` triage already knows how to escalate.
"""
from __future__ import annotations

import re

from .triage import CheckStatus


def sum_counter(log_text: str, key: str, *, lines: int) -> int:
    """Sum the integer value of ``key=<int>`` across the last ``lines`` lines of a log.
    A windowed rate: e.g. how many pollers were `relaunched` in the recent tail."""
    rx = re.compile(rf"\b{re.escape(key)}=(-?\d+)\b")
    total = 0
    for line in log_text.splitlines()[-lines:]:
        m = rx.search(line)
        if m:
            total += int(m.group(1))
    return total


def count_matches(text: str, pattern: str) -> int:
    """Count lines matching ``pattern`` (e.g. live `poller.py` processes in `ps` output)."""
    rx = re.compile(pattern)
    return sum(1 for line in text.splitlines() if rx.search(line))


def check_budget(name: str, value: int, *, warn_at: int, crit_at: int,
                 unit: str = "") -> CheckStatus:
    """Hard-threshold a value: >=crit_at crit, >=warn_at warn, else ok."""
    suffix = f" {unit}" if unit else ""
    summary = f"{name} = {value}{suffix}"
    if value >= crit_at:
        return CheckStatus(name, "crit", f"{summary} (>= {crit_at})")
    if value >= warn_at:
        return CheckStatus(name, "warn", f"{summary} (>= {warn_at})")
    return CheckStatus(name, "ok", summary)


_CONTENT_RANGE = re.compile(r"/(\d+)\s*$")


def parse_count_header(content_range) -> int | None:
    """Extract the total from a PostgREST ``Content-Range`` header value
    (e.g. ``0-999/183621`` or ``*/12``). Returns None if unparseable."""
    if not isinstance(content_range, str):
        return None
    m = _CONTENT_RANGE.search(content_range.strip())
    return int(m.group(1)) if m else None


def check_rate(name: str, current, prev, elapsed_s, *, warn_per_hour: int,
               crit_per_hour: int, unit: str = "rows") -> CheckStatus:
    """Turn a count delta into a per-hour rate and threshold it. First reading (prev is
    None), a count reset (current < prev), or zero elapsed time -> ok (record, don't
    alert): there is no trustworthy rate to judge."""
    label = f"rate:{name}"
    if prev is None or current is None or elapsed_s <= 0 or current < prev:
        return CheckStatus(label, "ok", f"{name}: {current} {unit} (no rate yet)")
    per_hour = int((current - prev) / elapsed_s * 3600)
    summary = f"{name}: +{per_hour} {unit}/hr"
    if per_hour >= crit_per_hour:
        return CheckStatus(label, "crit", f"{summary} (>= {crit_per_hour})")
    if per_hour >= warn_per_hour:
        return CheckStatus(label, "warn", f"{summary} (>= {warn_per_hour})")
    return CheckStatus(label, "ok", summary)
