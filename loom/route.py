# loom/route.py
"""Route-confirm: a model picks the target file for one learning. Deterministic
fallback to the distill-suggested `route` when the model output is unparseable;
None when there is no usable suggestion either (caller defers the learning)."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

_PROMPTS = Path(__file__).parent / "prompts"
_JSON_RE = re.compile(r"\{.*\}", re.S)
_LEAD_RE = re.compile(r"^(?:\./|/)+")
_WIKI_RE = re.compile(r"^wiki/", re.IGNORECASE)
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
# A slug never contains whitespace, a comma or a dash of this kind; a sentence always
# does. The router sometimes answers with its own routing verdict instead of a path.
_PROSE_RE = re.compile(r"[\s,;:–—]")


def normalize_target(raw: object, wiki_root: Optional[Path] = None) -> Optional[str]:
    """Canonicalize a proposed target to a wiki-root-relative `.md` path.

    Targets arrive from two places that both need this: the model's JSON, and the
    distill artifact's `route` field. Both routinely carry a `wiki/` prefix — the
    wiki root IS `~/wiki`, so taken verbatim that builds a second tree at
    `~/wiki/wiki/` (found 2026-07-23: 4 stray articles, 19 more learnings queued
    to join them). Returns None for anything that would escape the wiki root, so
    the caller defers the learning rather than writing outside the wiki.

    When `wiki_root` is given, a target whose TOP-LEVEL directory does not
    already exist under it is refused too — a brand-new top-level tree appearing
    silently is the exact bug class that grew wiki/wiki/ (`etc/passwd.md` would
    otherwise happily mint ~/wiki/etc/). Root-level files and every existing
    directory (including new SUBdirs under them) still pass; the live ledger
    shows both are legitimate, so a hard allow-list was rejected. Without
    `wiki_root` the function stays a pure string check.
    """
    if not isinstance(raw, str):
        return None
    target = raw.strip().replace("\\", "/")
    if _DRIVE_RE.match(target):
        # `C:/x.md` / `C:x.md` are contained on this POSIX host (they'd just make a
        # `C:` directory), but the containment promise above is unconditional — and a
        # drive-qualified target is never a real wiki path. Refuse rather than fold.
        return None
    previous = None
    while previous != target:                    # "./wiki/wiki/x" -> "x"
        previous = target
        target = _WIKI_RE.sub("", _LEAD_RE.sub("", target))
    parts = [p for p in target.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:               # containment: never leave the root
        return None
    if any(_PROSE_RE.search(p) for p in parts):  # a verdict sentence, not a path
        return None
    if wiki_root is not None and len(parts) > 1 \
            and not (Path(wiki_root) / parts[0]).is_dir():
        return None                              # never mint a new top-level dir
    target = "/".join(parts)
    return target if target.endswith(".md") else f"{target}.md"


def _suggested_target(learning: dict, wiki_root: Optional[Path] = None) -> Optional[dict]:
    slug = normalize_target(learning.get("route") or "", wiki_root)
    if not slug:
        return None
    return {"target": slug, "action": "update", "cross_links": []}


def confirm_route(backend, learning: dict, index_listing: str,
                  wiki_root: Optional[Path] = None, roster: str = "") -> Optional[dict]:
    prompt = (_PROMPTS / "route.md").read_text()
    user = prompt.replace("{{LEARNING}}", json.dumps(learning, ensure_ascii=False)) \
                 .replace("{{ROSTER}}", roster or "(none)") \
                 .replace("{{INDEX}}", index_listing or "(empty)")
    try:
        raw = backend.complete("route", "Route one learning. Output only JSON.", user, json_mode=True)
        m = _JSON_RE.search(raw)
        data = json.loads(m.group(0)) if m else {}
        target = normalize_target(data.get("target"), wiki_root)
        if target:
            return {"target": target,
                    "action": data.get("action", "update"),
                    "cross_links": data.get("cross_links", [])}
        if data.get("target"):
            # Silence here is how the wiki/wiki/ tree grew unnoticed for weeks. A
            # refused target means the router is drifting — say so in the cron log.
            logging.warning("route: refused unusable target %r", data["target"])
    except Exception:
        pass
    return _suggested_target(learning, wiki_root)
