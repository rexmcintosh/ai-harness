"""Find session transcripts that still need work (delta via LoomState)."""
from __future__ import annotations

from pathlib import Path
from typing import List

from .state import LoomState

_DONE = ("committed", "quarantined")

# Every loom prompt template opens with `<!-- loom/prompts/<name>.md -->`, and in a
# transcript persisted by loom's own `claude -p` calls that template IS the first
# user message. Only that message is checked: an interactive session that merely
# read a prompt file would carry the marker later, in a tool result, never there.
_SELF_MARKER = "<!-- loom/prompts/"


def session_id_for(transcript: Path) -> str:
    return Path(transcript).stem


def _content_text(record: dict) -> str:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def is_loom_generated(transcript: Path) -> bool:
    """True when this transcript was created by loom's own headless model calls
    (distill/route/weave). Distilling those feeds loom its own output — the
    recursion that filled the wiki with loom-about-loom articles."""
    import json
    try:
        with open(transcript, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("type") == "user":
                    return _SELF_MARKER in _content_text(record)
    except OSError:
        return False
    return False


def find_pending(projects_dir: Path, state: LoomState) -> List[Path]:
    transcripts = sorted(Path(projects_dir).glob("*/*.jsonl"))
    return [t for t in transcripts if state.state_of(session_id_for(t)) not in _DONE]
