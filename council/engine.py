from __future__ import annotations
import concurrent.futures

from .models import Member, Panel, Finding, MemberResult
from .prompts import MEMBER_OUTPUT
from .jsonparse import loads_lenient


def _as_int(value, default: int = 5) -> int:
    """Coerce a model-supplied confidence to an int, tolerating null/strings."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class UnusableReply(ValueError):
    """The model answered, the answer parsed, and it says nothing a seat can use."""


def _usable(data) -> bool:
    """A seat reply counts only if it carries a stance, a headline or a finding. Valid JSON
    alone proves nothing: `{": ": ", "}` parses, and used to pass as a quiet stance "na"."""
    if not isinstance(data, dict):
        return False
    return bool(str(data.get("stance") or "").strip() or str(data.get("headline") or "").strip()
                or any(isinstance(f, dict) and str(f.get("point") or "").strip()
                       for f in data.get("findings") or []))


def _ask_member(member: Member, context: str, client, *, task_type: str = "chat",
                max_completion_tokens=None) -> MemberResult:
    try:
        raw = client.complete(
            member.model,
            member.system + "\n\n" + MEMBER_OUTPUT,
            f"Here is the input to weigh in on:\n\n{context}",
            json_mode=member.json_mode,
            task_type=task_type,
            max_completion_tokens=max_completion_tokens,
        )
        data = loads_lenient(raw)
        if not _usable(data):
            raise UnusableReply(f"no usable answer in the reply ({len(raw or '')} chars): "
                                f"{' '.join(str(raw or '').split())[:80]!r}")
        findings = [
            Finding(point=str(f.get("point", "")),
                    severity=str(f.get("severity", "info")),
                    confidence=_as_int(f.get("confidence", 5)))
            for f in data.get("findings", []) if isinstance(f, dict)
        ]
        return MemberResult(
            member=member.name, model=member.model,
            stance=str(data.get("stance") or "na"),
            headline=str(data.get("headline", "")),
            findings=findings,
            suggestions=[str(s) for s in data.get("suggestions", [])],
        )
    except Exception as e:  # noqa: BLE001
        return MemberResult(member=member.name, model=member.model, stance="na",
                            headline="(member errored)", error=f"{type(e).__name__}: {e}")


def run_panel(panel: Panel, context: str, client, *, max_workers=None,
              task_type: str = "chat", budget=None) -> list[MemberResult]:
    # Cap concurrency so an oversized custom panel can't open a thread / rate-limit
    # storm; real panels are 3-4 seats so this is a safety bound, not a throttle.
    workers = max_workers or min(8, max(1, len(panel.members)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_ask_member, m, context, client, task_type=task_type,
                               max_completion_tokens=(budget.member(m.name)
                                                      if budget else None)): m
                  for m in panel.members}
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    order = [m.name for m in panel.members]
    results.sort(key=lambda r: order.index(r.member))
    return results
