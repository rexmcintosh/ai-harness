from __future__ import annotations
import re

_DOC_EXTS = {".md", ".markdown", ".rst", ".txt", ".adoc"}
_DOC_SEGMENTS = {"docs", "spec", "specs", "plan", "plans"}
# Section boundary only — match the line, not the (ambiguous, space-bearing) paths.
_FILE_HEADER = re.compile(r"^diff --git .*$", re.MULTILINE)
# Path comes from the +++/--- lines, where it runs to end-of-line (unambiguous
# for spaces) and is git-quoted for special chars.
_PLUS = re.compile(r"^\+\+\+ (.+)$", re.MULTILINE)
_MINUS = re.compile(r"^--- (.+)$", re.MULTILINE)


def _strip_prefix(raw: str) -> str:
    """Turn a diff-line path token into a repo-relative path: drop a trailing
    tab-timestamp, strip git's C-style quotes, and remove the a/ or b/ prefix.
    (Interior escapes are left as-is — extension and path segments, which is all
    classify_path needs, survive intact.)"""
    raw = raw.split("\t", 1)[0].strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1]
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    return raw


def _section_path(section: str) -> str:
    """Best path for classifying a file section: the new-side (+++) path, or the
    old-side (---) path when the file was deleted (+++ /dev/null)."""
    for rx in (_PLUS, _MINUS):
        m = rx.search(section)
        if not m:
            continue
        token = m.group(1).split("\t", 1)[0].strip()
        if token == "/dev/null":
            continue
        return _strip_prefix(token)
    return ""  # no usable path -> classify_path returns "code" (the safe default)


def classify_path(path: str) -> str:
    """Classify a repo-relative path as 'doc' or 'code'.

    doc  = a documentation/spec/plan file: extension in _DOC_EXTS, OR any
           directory segment in _DOC_SEGMENTS (docs/ specs/ plans/ ...).
    code = everything else (the default).
    """
    p = (path or "").strip().strip("/")
    if not p:
        return "code"
    segments = [s.lower() for s in p.split("/") if s]
    if not segments:
        return "code"
    name = segments[-1]
    dot = name.rfind(".")
    ext = name[dot:] if dot > 0 else ""
    if ext in _DOC_EXTS:
        return "doc"
    if any(seg in _DOC_SEGMENTS for seg in segments[:-1]):
        return "doc"
    return "code"


def changed_paths(diff: str) -> list[str]:
    """Repo-relative path of each file section in a unified diff, in order. Used to
    compute the change's blast-radius tier (see council.gate.risk_tier)."""
    if not diff:
        return []
    matches = list(_FILE_HEADER.finditer(diff))
    paths = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(diff)
        p = _section_path(diff[m.start():end])
        if p:
            paths.append(p)
    return paths


def split_diff_by_type(diff: str) -> tuple[str, str]:
    """Split a unified diff into (code_diff, doc_diff). Sections are delimited by
    `diff --git` lines; each file's path is read from its +++/--- lines (robust to
    paths with spaces and git-quoted special chars). Either bucket may be empty."""
    if not diff:
        return "", ""
    matches = list(_FILE_HEADER.finditer(diff))
    if not matches:
        return "", ""
    code_parts: list[str] = []
    doc_parts: list[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(diff)
        section = diff[start:end]
        path = _section_path(section)
        (doc_parts if classify_path(path) == "doc" else code_parts).append(section)
    return "".join(code_parts), "".join(doc_parts)
