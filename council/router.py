from __future__ import annotations

from .prompts import ROUTER_PROMPT
from .jsonparse import loads_lenient


def pick_panel(context: str, panels: dict, client, *, router_model: str,
               default: str, snippet_chars: int = 1500, task_type: str = "chat",
               max_completion_tokens=None) -> str:
    listing = "\n".join(f"- {name}: {p.description}" for name, p in panels.items())
    system = ROUTER_PROMPT + listing
    try:
        raw = client.complete(router_model, system, context[:snippet_chars],
                              task_type=task_type,
                              max_completion_tokens=max_completion_tokens)
        name = loads_lenient(raw).get("panel", "")
        return name if name in panels else default
    except Exception:  # noqa: BLE001
        return default
