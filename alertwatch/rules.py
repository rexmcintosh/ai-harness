"""Sort captured messages before any model runs: drop, ignore, or look."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("rules.toml")
ACTIONS = ("ignore", "look")
CHECK_SOURCE = "alert-watch-check"


@dataclass(frozen=True)
class Rule:
    name: str
    action: str
    source: str | None
    text: str | None


def load_rules(path: Path | None = None) -> list[Rule]:
    raw = tomllib.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    rules = []
    for r in raw.get("rule") or []:
        rule = Rule(name=str(r["name"]), action=str(r["action"]),
                    source=r.get("source"), text=r.get("text"))
        if rule.action not in ACTIONS:
            raise ValueError(f"alert-watch rule {rule.name!r}: action must be one of {ACTIONS}")
        if not rule.source and not rule.text:
            raise ValueError(f"alert-watch rule {rule.name!r}: needs a source or a text pattern")
        for pattern in (rule.source, rule.text):
            if pattern:
                re.compile(pattern)
        rules.append(rule)
    return rules


def is_own_source(source: str) -> bool:
    return source.startswith("alert-watch") and source != CHECK_SOURCE


def classify(entry: dict, rules: list[Rule]) -> tuple[str, str]:
    source = str(entry.get("source") or "")
    text = str(entry.get("text") or "")
    if is_own_source(source):
        return "drop", "own-source"
    for rule in rules:
        if rule.source and not re.search(rule.source, source):
            continue
        if rule.text and not re.search(rule.text, text, re.IGNORECASE | re.MULTILINE):
            continue
        return rule.action, rule.name
    return "look", "default"
