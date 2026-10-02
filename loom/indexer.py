# loom/indexer.py
"""Maintain the wiki's _backlinks.json (fully regenerated, deterministic) and
_index.md (incremental: articles have one summary line under their section).
Summaries are passed in by the caller; existing entries are updated in place.

Writes are atomic via os.replace, but are not fsync-durable or locked against
concurrent writers. Callers must serialize index updates.
"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Dict, List

_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
SUMMARY_LIMIT = 110


def clean_summary(text: str, limit: int = SUMMARY_LIMIT) -> str:
    """One-line index summary: collapse whitespace, truncate at a word boundary with an
    ellipsis (never mid-word). Strips trailing markdown/backtick noise from the cut."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:—-`\"'")
    return f"{cut}…"


SECTION_FOR = {
    "people": "People", "companies": "Companies", "projects": "Projects",
    "places": "Places", "eras": "Eras", "transitions": "Transitions",
    "decisions": "Decisions", "philosophies": "Philosophies", "patterns": "Patterns",
    "skills": "Skills", "tools": "Tools", "relationships": "Relationships",
    "health": "Health",
}


def _articles(root: Path) -> List[Path]:
    return sorted(p for p in root.rglob("*.md")
                  if not p.name.startswith("_") and ".git" not in p.parts)


def rebuild_backlinks(root: Path) -> None:
    root = Path(root)
    back: Dict[str, set] = {}
    for art in _articles(root):
        slug = art.stem
        for m in _WIKILINK.finditer(art.read_text(encoding="utf-8")):
            target = m.group(1).strip()
            if target and target != slug:
                back.setdefault(target, set()).add(slug)
    out = {k: sorted(v) for k, v in sorted(back.items())}
    (root / "_backlinks.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


def _write_index_atomic(path: Path, text: str) -> None:
    temp_path = None
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as temp:
            temp_path = Path(temp.name)
            temp.write(text)
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _refresh_last_updated(text: str, today: str) -> str:
    text = re.sub(r"(?m)^(last_updated:).*$", rf"\1 {today}", text)
    return re.sub(
        r"(?im)(last updated:)\s*[0-9-]+", rf"\1 {today}", text
    )


def upsert_index_entry(root: Path, slug: str, directory: str, summary: str, today: str) -> None:
    root = Path(root)
    idx = root / "_index.md"
    text = idx.read_text(encoding="utf-8") if idx.exists() else "# RexBrain — Master Index\n"
    line = f"- [[{slug}]] — {clean_summary(summary)}"
    section = SECTION_FOR.get(directory, "Unsorted")
    heading = f"## {section}"
    heading_match = re.search(rf"(?m)^{re.escape(heading)}\r?$", text)
    if heading_match:
        section_start = heading_match.end()
        next_heading = re.search(
            r"(?m)^##[ \t]+[^\r\n]*\r?$", text[section_start:]
        )
        section_end = (
            section_start + next_heading.start() if next_heading else len(text)
        )
        section_text = text[section_start:section_end]
        entry = re.compile(
            rf"(?m)^[ \t]*[-*][ \t]+\[\[{re.escape(slug)}\]\]"
            rf"(?:[ \t]+(?:—|-)[ \t]*[^\r\n]*)?[ \t]*(?:\r?\n|$)"
        )
        if entry.search(section_text):
            first = True

            def _replace_entry(match):
                nonlocal first
                if not first:
                    return ""
                first = False
                return line + ("\n" if match.group(0).endswith("\n") else "")

            updated_section = entry.sub(_replace_entry, section_text)
            updated = text[:section_start] + updated_section + text[section_end:]
            updated = _refresh_last_updated(updated, today)
            if updated != text:
                _write_index_atomic(idx, updated)
            return
    lines = text.splitlines()
    if heading in lines:
        at = lines.index(heading) + 1              # insert right under the heading
        lines.insert(at, line)
    else:
        lines += ["", heading, line]
    text = "\n".join(lines) + ("\n" if not text.endswith("\n") else "")
    text = _refresh_last_updated(text, today)
    text = _bump_total_pages(text)
    _write_index_atomic(idx, text if text.endswith("\n") else text + "\n")


def _bump_total_pages(text: str) -> str:
    """Increment the curated article count by one (frontmatter + intro line), preserving the
    wiki's editorial counting convention rather than recomputing from a file glob."""
    def _inc(m):
        return f"{m.group(1)}{int(m.group(2)) + 1}"
    text = re.sub(r"(?m)^(total_pages:\s*)(\d+)", _inc, text)
    text = re.sub(r"(?i)(Total pages:\s*)(\d+)", _inc, text)
    return text
