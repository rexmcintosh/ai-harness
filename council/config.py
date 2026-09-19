from __future__ import annotations
import os
import sys
import tomllib
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from .models import Member, Panel


@dataclass
class Settings:
    default_panel: str = "decision"
    router_model: str = ""
    chair_model: str = ""
    byte_cap: int = 200_000
    timeout: int = 180
    # Output ceilings, in completion tokens (Venice counts reasoning tokens
    # against these). Sized from ~/.local/state/venice-usage/ledger.db, 1,732
    # council rows over 2026-07-20..2026-09-11: the largest member completion
    # ever recorded is 17,142 (openai-gpt-53-codex) and the largest chair
    # completion is 5,174 (claude-opus-4-8). These defaults clear both by ~40%
    # and ~55%, so nothing in that history would have been truncated, while
    # bounding a runaway at a small fraction of the 128,000 the catalogue
    # otherwise allows. 0 disables the cap. See docs/output-caps-2026-09-11.md.
    max_completion_tokens: int = 24_000
    chair_max_completion_tokens: int = 8_000
    router_max_completion_tokens: int = 2_000
    # {"daily": {...}, "deep": {...}} — per-rigor overrides of the three above.
    rigor: dict = field(default_factory=dict)


@dataclass
class TokenBudget:
    """The resolved output ceilings for one panel at one rigor."""
    default_member: int
    chair: int
    router: int
    per_member: dict = field(default_factory=dict)

    def member(self, name: str) -> int:
        return self.per_member.get(name, self.default_member)


def _first(*values, fallback):
    """First value that was actually set. `is not None`, never truthiness — 0
    is a real setting ("no cap"), and treating it as unset would silently
    re-enable the parent's ceiling."""
    for v in values:
        if v is not None:
            return int(v)
    return int(fallback)


def _rigor_block(source, rigor):
    return (getattr(source, "rigor", None) or {}).get(rigor) or {}


def resolve_budget(settings: Settings, panel, rigor: str) -> TokenBudget:
    """Output ceilings for `panel` at `rigor`, most specific wins:

        seat  >  panel+rigor  >  panel  >  settings+rigor  >  settings

    The member, chair and router ladders are independent on purpose: raising a
    panel's member ceiling must not quietly raise what the chair may spend.
    """
    prig, srig = _rigor_block(panel, rigor), _rigor_block(settings, rigor)
    return TokenBudget(
        default_member=_first(prig.get("max_completion_tokens"),
                              getattr(panel, "max_completion_tokens", None),
                              srig.get("max_completion_tokens"),
                              settings.max_completion_tokens,
                              fallback=Settings.max_completion_tokens),
        chair=_first(prig.get("chair_max_completion_tokens"),
                     getattr(panel, "chair_max_completion_tokens", None),
                     srig.get("chair_max_completion_tokens"),
                     settings.chair_max_completion_tokens,
                     fallback=Settings.chair_max_completion_tokens),
        router=_first(srig.get("router_max_completion_tokens"),
                      settings.router_max_completion_tokens,
                      fallback=Settings.router_max_completion_tokens),
        per_member={m.name: int(m.max_completion_tokens) for m in panel.members
                    if getattr(m, "max_completion_tokens", None) is not None},
    )


# One Venice key per project, so per-key billing attributes spend correctly.
# The generic VENICE_API_KEY stays as a fallback: it is what `~/.env` puts in
# every shell, so this code is safe to deploy before the council key exists.
_KEY_VARS = ("VENICE_COUNCIL_KEY", "VENICE_API_KEY")


def get_api_key() -> str:
    for name in _KEY_VARS:
        key = os.environ.get(name)
        if key:
            return key
    print("error: no Venice key set. Add VENICE_COUNCIL_KEY (preferred) or "
          "VENICE_API_KEY to your environment or .env (see .env.example).",
          file=sys.stderr)
    raise SystemExit(2)


def _panels_path(path=None) -> Path:
    if path:
        return Path(path)
    override = Path.home() / ".config" / "council" / "panels.toml"
    if override.exists():
        return override
    return Path(str(files("council") / "panels.toml"))


def load_panels(path=None):
    with open(_panels_path(path), "rb") as fh:
        data = tomllib.load(fh)
    s = data.get("settings", {})
    settings = Settings(
        default_panel=s.get("default_panel", "decision"),
        router_model=s.get("router_model", ""),
        chair_model=s.get("chair_model", ""),
        byte_cap=int(s.get("byte_cap", 200_000)),
        timeout=int(s.get("timeout", 180)),
        max_completion_tokens=int(s.get("max_completion_tokens",
                                        Settings.max_completion_tokens)),
        chair_max_completion_tokens=int(s.get("chair_max_completion_tokens",
                                              Settings.chair_max_completion_tokens)),
        router_max_completion_tokens=int(s.get("router_max_completion_tokens",
                                               Settings.router_max_completion_tokens)),
        rigor=s.get("rigor", {}) or {},
    )
    panels = {}
    for name, p in data.get("panels", {}).items():
        members = [Member(name=m["name"], model=m["model"], system=m["system"],
                          max_completion_tokens=m.get("max_completion_tokens"))
                   for m in p.get("members", [])]
        panels[name] = Panel(name=name, description=p.get("description", ""),
                             members=members, default_rigor=p.get("default_rigor", "daily"),
                             max_completion_tokens=p.get("max_completion_tokens"),
                             chair_max_completion_tokens=p.get("chair_max_completion_tokens"),
                             rigor=p.get("rigor", {}) or {})
    return settings, panels


def truncate(text: str, cap: int) -> str:
    b = text.encode("utf-8", errors="ignore")
    if len(b) <= cap:
        return text
    head = b[: cap // 2].decode("utf-8", errors="ignore")
    tail = b[-cap // 2:].decode("utf-8", errors="ignore")
    return f"{head}\n\n... [input truncated, {len(b)} bytes total] ...\n\n{tail}"
