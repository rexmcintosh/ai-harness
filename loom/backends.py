# loom/backends.py
"""Pluggable text->text completion. Weaving never touches the filesystem, so the
backend is swappable: `claude` runs the Max session via `claude -p`; `venice` runs
the same-tier models through Venice on DIEM. Role -> model is per backend."""
from __future__ import annotations

from typing import Optional

from . import llm
from .venice import VeniceClient

CLAUDE_MODELS = {"distill": "sonnet", "route": "haiku", "weave": "opus"}
# Route stays a validated-output, high-volume stage priced for volume
# (deepseek-v4-flash: 1M ctx, ~11x cheaper than the gemini flash it replaced).
# Distill moved flash -> pro on 2026-08-19: flash quarantined 14% (nightly
# 08-19) then 60% (daytime backfill, 12/20) of sessions as unparseable even
# after retry — the spend is wasted at that rate.
# Weave: deepseek-v4-pro won the 2026-08-18 bake-off vs claude-opus-4-8 /
# kimi-k2-6 / glm-5 — identical guard-pass rate (19/20, shared failure was a
# content-level sentinel trip), comparable article quality, 5.7x cheaper.
VENICE_MODELS = {"distill": "deepseek-v4-pro", "route": "deepseek-v4-flash",
                 "weave": "deepseek-v4-pro"}


class Backend:
    name = "base"
    def complete(self, role: str, system: str, user: str, json_mode: bool = False) -> str:
        raise NotImplementedError


class ClaudeBackend(Backend):
    name = "claude"
    def complete(self, role: str, system: str, user: str, json_mode: bool = False) -> str:
        model = CLAUDE_MODELS[role]
        prompt = f"{system}\n\n{user}"            # claude -p takes one stdin prompt
        return llm.run(prompt, model=model)


class VeniceBackend(Backend):
    name = "venice"
    def __init__(self, api_key: str) -> None:
        self._client = VeniceClient(api_key)
    def complete(self, role: str, system: str, user: str, json_mode: bool = False) -> str:
        return self._client.complete(VENICE_MODELS[role], system, user,
                                     json_mode=json_mode, task=role)


def get_backend(name: str, api_key: Optional[str] = None) -> Backend:
    if name == "claude":
        return ClaudeBackend()
    if name == "venice":
        import os
        # One Venice key per project; VENICE_API_KEY remains the fallback so
        # this is safe to deploy before the loom key is minted.
        key = (api_key
               or os.environ.get("VENICE_LOOM_KEY")
               or os.environ.get("VENICE_API_KEY", ""))
        return VeniceBackend(key)
    raise ValueError(f"unknown backend: {name}")
