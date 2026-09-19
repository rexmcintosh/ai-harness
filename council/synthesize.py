from __future__ import annotations

from .models import MemberResult, Disagreement, Synthesis, ConfirmedBlock
from .prompts import SYNTH_OUTPUT
from .jsonparse import loads_lenient


def _as_int(value, default: int = 5) -> int:
    """Coerce a model-supplied confidence to an int, tolerating null/strings."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _panel_digest(results: list[MemberResult]) -> str:
    lines = []
    for r in results:
        if r.error:
            lines.append(f"### {r.member} ({r.model}) — errored, no input")
            continue
        lines.append(f"### {r.member} ({r.model}) — stance: {r.stance}")
        lines.append(f"headline: {r.headline}")
        for f in r.findings:
            lines.append(f"- [{f.severity} c{f.confidence}] {f.point}")
        for s in r.suggestions:
            lines.append(f"- (suggestion) {s}")
    return "\n".join(lines)


def synthesize(context: str, results: list[MemberResult], client, *, chair_model: str,
               system: str = SYNTH_OUTPUT, task_type: str = "chat",
               max_completion_tokens=None) -> Synthesis:
    user = (f"ORIGINAL INPUT:\n{context}\n\n"
            f"PANELIST ANSWERS (they answered independently, blind to each other):\n"
            f"{_panel_digest(results)}")
    raw = ""
    try:
        raw = client.complete(chair_model, system, user, task_type=task_type,
                              max_completion_tokens=max_completion_tokens)
        d = loads_lenient(raw)
        if not isinstance(d, dict) or not str(d.get("recommendation") or "").strip():
            # Valid JSON is not an answer. An empty Synthesis with no error reads as "the
            # chair confirmed nothing", which passes the merge gate; callers fail closed
            # on `error`, so a chair that said nothing must set it.
            raise ValueError(f"no usable answer in the chair's reply ({len(raw or '')} chars)")
        dis = [Disagreement(
            topic=str(x.get("topic", "")), type=str(x.get("type", "taste")),
            positions=str(x.get("positions", "")), resolution=str(x.get("resolution", "")),
            what_we_might_miss=str(x.get("what_we_might_miss", "")),
            if_wrong_cost=str(x.get("if_wrong_cost", "")),
        ) for x in d.get("disagreements", []) if isinstance(x, dict)]
        blocks = [ConfirmedBlock(point=str(b.get("point", "")),
                                 severity=str(b.get("severity", "")),
                                 why=str(b.get("why", "")))
                  for b in d.get("blocking_findings", []) if isinstance(b, dict)]
        return Synthesis(
            recommendation=str(d.get("recommendation", "")),
            raw_response=raw,
            confidence=_as_int(d.get("confidence", 5)),
            consensus=[str(c) for c in d.get("consensus", [])],
            disagreements=dis,
            cross_panel_themes=[str(t) for t in d.get("cross_panel_themes", [])],
            blocking_findings=blocks,
            review_status=(d.get("review_status")
                           if d.get("review_status") in ("clean", "changes_requested")
                           and isinstance(d.get("blocking_findings"), list)
                           and all(isinstance(b, dict) and isinstance(b.get("point"), str)
                                   and b["point"].strip() and isinstance(b.get("why"), str)
                                   and isinstance(b.get("severity"), str)
                                   for b in d["blocking_findings"]) else "unknown"),
            required_changes=(d["required_changes"] if isinstance(d.get("required_changes"), list)
                              and all(isinstance(x, str) and x.strip() for x in d["required_changes"])
                              else None),
        )
    except Exception as e:  # noqa: BLE001
        return Synthesis(recommendation="(synthesis unavailable — see raw panel below)",
                         confidence=0, error=f"{type(e).__name__}: {e}", raw_response=raw)
