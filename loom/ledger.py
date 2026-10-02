# loom/ledger.py
"""Per-learning weave ledger — the idempotency unit. Keyed `<session>#<index>`.
Git (loom-shadow trailers) is authoritative; this is a rebuildable cache that
reconcile_from_git() repopulates. `deferred` is retryable; `rejected` is permanent
and surfaced every run. Both committed and rejected are 'settled'."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

LEARNING_STATES = ("planned", "woven", "committed", "deferred", "rejected", "quarantined")
_SETTLED = ("committed", "rejected", "quarantined")


class WeaveLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: Dict[str, dict] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text() or "{}")

    def entry(self, lid: str) -> dict:
        return self._data.get(lid, {})

    def status_of(self, lid: str) -> Optional[str]:
        return self._data.get(lid, {}).get("status")

    def plan(self, lid: str, target: str, action: str) -> None:
        e = self._data.setdefault(lid, {"deferrals": 0})
        e.update(target=target, action=action, status="planned")
        self._save()

    def mark(self, lid: str, status: str, commit_sha: Optional[str] = None,
             reason: Optional[str] = None) -> None:
        if status not in LEARNING_STATES:
            raise ValueError(f"unknown status: {status}")
        e = self._data.setdefault(lid, {"deferrals": 0})
        e["status"] = status
        if commit_sha:
            e["commit_sha"] = commit_sha
        if reason:
            e["reason"] = reason
        self._save()

    def defer(self, lid: str, reason: str) -> None:
        e = self._data.setdefault(lid, {"deferrals": 0})
        e["status"] = "deferred"
        e["reason"] = reason
        e["deferrals"] = e.get("deferrals", 0) + 1
        self._save()

    def reject(self, lid: str, reason: str) -> None:
        self.mark(lid, "rejected", reason=reason)

    def quarantine(self, lid: str, reason: str) -> None:
        """Settled-but-recoverable: the weave tripped a guard, so it must not reach
        loom-shadow unreviewed, but the learning is NOT discarded — it is surfaced
        for a human decision. Settled so it is never silently re-woven (that would
        re-trip the same deterministic guard and burn DIEM every run)."""
        self.mark(lid, "quarantined", reason=reason)

    def reconcile_from_git(self, committed_ids: Set[str]) -> None:
        for lid in committed_ids:
            e = self._data.setdefault(lid, {"deferrals": 0})
            e["status"] = "committed"
        self._save()

    def items(self) -> List[Tuple[str, dict]]:
        return sorted(self._data.items())

    def clear_route(self, lid: str) -> None:
        """Drop a cached route so the next run re-routes with current context
        (index + roster). Status and deferral history are kept."""
        e = self._data.get(lid)
        if e:
            e.pop("target", None)
            e.pop("action", None)
            self._save()

    def pending_ids(self) -> List[str]:
        return [lid for lid, e in sorted(self._data.items())
                if e.get("status") not in _SETTLED]

    def rejected(self) -> List[Tuple[str, str]]:
        return [(lid, e.get("reason", "")) for lid, e in sorted(self._data.items())
                if e.get("status") == "rejected"]

    def quarantined(self) -> List[Tuple[str, str]]:
        return [(lid, e.get("reason", "")) for lid, e in sorted(self._data.items())
                if e.get("status") == "quarantined"]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.path)
