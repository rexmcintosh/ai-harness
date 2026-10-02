# loom/phantom.py
"""One-time reconcile of the phantom `<wiki-root>/wiki/` tree.

Until 2026-07-23 the router emitted targets prefixed with `wiki/` and confirm_route
took them verbatim, so a second, invisible tree grew at `~/wiki/wiki/<dir>/...`
beside the real one. The cause is fixed (`route.normalize_target` strips the
prefix); this module cleans up the content that already landed and was promoted.

Dry-run by default (`plan`). `apply` does, on the MASTER checkout only:

1. For every phantom file with a real counterpart: append the curated fold text
   (if a fold file exists for that target), merge the phantom's `<!-- loom-woven -->`
   ids into the real article's marker so those learnings count as present, and
   `git rm` the phantom.
2. A phantom with NO real counterpart is simply `git mv`-ed to its real path.
3. `wiki/_index.md` is a separate mini-index, never folded: deleted outright.
4. One commit on master. No push — the caller pushes (`promote` also pushes
   master nightly). If the loom-shadow worktree is exactly at master it is
   fast-forwarded so the phantom dir disappears there too.

Judgement stays with the human: `plan` lists each phantom paragraph that does
not appear verbatim in the real article (`candidate_paragraphs`) — a prompt for
review, not something the tool folds on its own. Only text in the folds dir is
ever written into a real article.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .fingerprint import markers_in, strip_markers, with_markers

PHANTOM_DIR = "wiki"
_HEADING_RE = re.compile(r"^#{1,6}\s")
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.S)
_MARKER_LINE_RE = re.compile(r"<!--\s*loom-woven:.*?-->", re.S)
_RUNNING_RE = r"loom\.cli (absorb|backfill|promote)"


class PhantomError(RuntimeError):
    pass


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise PhantomError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc


def loom_running() -> bool:
    """True when a loom absorb/backfill/promote process is live (never reconcile under one)."""
    proc = subprocess.run(["pgrep", "-f", _RUNNING_RE], capture_output=True, text=True)
    return proc.returncode == 0


@dataclass
class PhantomFile:
    phantom_rel: str
    real_rel: str
    real_exists: bool
    action: str                       # fold | delete | move
    fold: Optional[str] = None        # fold file applied to the real article, if any
    ids: List[str] = field(default_factory=list)
    candidate_paragraphs: List[str] = field(default_factory=list)


def find_phantom_files(wiki_root: Path) -> List[str]:
    root = Path(wiki_root) / PHANTOM_DIR
    if not root.is_dir():
        return []
    return sorted(str(p.relative_to(wiki_root)) for p in root.rglob("*") if p.is_file())


def _paragraphs(text: str) -> List[str]:
    body = _FRONTMATTER_RE.sub("", text or "")
    body = _MARKER_LINE_RE.sub("", body)
    out = []
    for chunk in re.split(r"\n\s*\n", body):
        chunk = chunk.strip()
        if not chunk or _HEADING_RE.match(chunk):
            continue
        out.append(chunk)
    return out


def _norm(text: str) -> str:
    text = re.sub(r"[*_`]", "", text or "").lower()
    return re.sub(r"\s+", " ", text).strip()


def unique_paragraphs(phantom_text: str, real_text: str) -> List[str]:
    """Phantom paragraphs whose normalized text is not contained in the real article."""
    real = _norm(real_text)
    return [p for p in _paragraphs(phantom_text) if _norm(p) not in real]


def _fold_for(folds_dir: Optional[Path], real_rel: str) -> Optional[Path]:
    if folds_dir is None:
        return None
    p = Path(folds_dir) / real_rel
    return p if p.is_file() else None


def _unmatched_folds(folds_dir: Optional[Path], real_rels: List[str]) -> List[str]:
    if folds_dir is None or not Path(folds_dir).is_dir():
        return []
    have = set(real_rels)
    return sorted(str(p.relative_to(folds_dir)) for p in Path(folds_dir).rglob("*.md")
                  if p.is_file() and str(p.relative_to(folds_dir)) not in have)


def _inspect(wiki_root: Path, folds_dir: Optional[Path]) -> List[PhantomFile]:
    wiki_root = Path(wiki_root)
    files: List[PhantomFile] = []
    for phantom_rel in find_phantom_files(wiki_root):
        real_rel = phantom_rel[len(PHANTOM_DIR) + 1:]
        phantom_text = (wiki_root / phantom_rel).read_text(encoding="utf-8")
        real_path = wiki_root / real_rel
        real_exists = real_path.is_file()
        ids = sorted(markers_in(phantom_text))
        if real_rel == "_index.md":
            action, fold, cands = "delete", None, _paragraphs(phantom_text)
        elif not real_exists:
            action, fold, cands = "move", None, _paragraphs(phantom_text)
        else:
            fold = _fold_for(folds_dir, real_rel)
            action = "fold"
            cands = unique_paragraphs(phantom_text, real_path.read_text(encoding="utf-8"))
        files.append(PhantomFile(phantom_rel=phantom_rel, real_rel=real_rel,
                                 real_exists=real_exists, action=action,
                                 fold=str(fold) if fold else None, ids=ids,
                                 candidate_paragraphs=cands))
    return files


def plan(wiki_root: Path, folds_dir: Optional[Path] = None) -> Dict[str, object]:
    """Read-only report. Never writes, never runs git mutations."""
    wiki_root = Path(wiki_root)
    files = _inspect(wiki_root, folds_dir)
    return {
        "apply": False,
        "wiki_root": str(wiki_root),
        "phantom_dir": str(wiki_root / PHANTOM_DIR),
        "folds_dir": str(folds_dir) if folds_dir else None,
        "folds_dir_exists": bool(folds_dir and Path(folds_dir).is_dir()),
        "files": [asdict(f) for f in files],
        "unmatched_folds": _unmatched_folds(folds_dir, [f.real_rel for f in files]),
    }


def folded_text(real_text: str, fold_text: Optional[str], phantom_ids: List[str]) -> str:
    """Real article + optional fold paragraph(s), re-stamped with the union of markers.

    The marker block stays last (as `weave` writes it); the fold goes just above it."""
    ids = markers_in(real_text) | set(phantom_ids)
    body = strip_markers(real_text)
    if fold_text and fold_text.strip():
        body = f"{body}\n\n{fold_text.strip()}"
    if not ids:
        return body + "\n"
    return with_markers(body, ids)


def _common_dir(root: Path) -> Path:
    # `--git-common-dir` is relative to the repo (".git") for the main checkout and
    # absolute for a linked worktree; join-then-resolve handles both.
    out = _git(root, "rev-parse", "--git-common-dir").stdout.strip()
    return (Path(root) / out).resolve()


def _preflight(wiki_root: Path, shadow_root: Optional[Path]) -> Optional[bool]:
    head = _git(wiki_root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if head != "master":
        raise PhantomError(f"wiki_root is on '{head}', expected master; aborting")
    if _git(wiki_root, "status", "--porcelain").stdout.strip():
        raise PhantomError("wiki working tree is dirty; aborting")
    if shadow_root is None:
        return None
    if _common_dir(shadow_root) != _common_dir(wiki_root):
        raise PhantomError("shadow_root is not a worktree of wiki_root; aborting")
    shead = _git(shadow_root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if shead != "loom-shadow":
        raise PhantomError(f"shadow_root is on '{shead}', expected loom-shadow; aborting")
    if _git(shadow_root, "status", "--porcelain").stdout.strip():
        raise PhantomError("shadow worktree is dirty; aborting")
    master = _git(wiki_root, "rev-parse", "HEAD").stdout.strip()
    shadow = _git(shadow_root, "rev-parse", "HEAD").stdout.strip()
    return master == shadow


def apply(wiki_root: Path, folds_dir: Optional[Path] = None, *,
          lock_path: Optional[Path] = None, shadow_root: Optional[Path] = None,
          running_check=None) -> Dict[str, object]:
    """Fold, delete/move, commit on master. Raises PhantomError; never pushes."""
    wiki_root = Path(wiki_root)
    shadow_root = Path(shadow_root) if shadow_root is not None else None
    if folds_dir is not None and not Path(folds_dir).is_dir():
        raise PhantomError(f"folds dir does not exist: {folds_dir}")
    if (running_check or loom_running)():
        raise PhantomError("a loom absorb/backfill/promote is running; retry later")
    if lock_path is None:
        return _apply(wiki_root, folds_dir, shadow_root)
    # Same single-writer discipline as run-absorb.sh / fixup: never touch the wiki
    # while a nightly run holds the lock.
    import fcntl
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise PhantomError("another loom run holds .run.lock; retry later")
        try:
            return _apply(wiki_root, folds_dir, shadow_root)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _apply(wiki_root: Path, folds_dir: Optional[Path], shadow_root: Optional[Path]) -> Dict[str, object]:
    files = _inspect(wiki_root, folds_dir)
    if not files:
        return {"applied": False, "reason": "no phantom tree", "phantom_dir": str(wiki_root / PHANTOM_DIR)}
    stray = _unmatched_folds(folds_dir, [f.real_rel for f in files])
    if stray:
        raise PhantomError(f"fold files match no phantom target: {stray}")
    shadow_in_sync = _preflight(wiki_root, shadow_root)

    folded: List[str] = []
    removed: List[str] = []
    moved: List[str] = []
    markers_merged: Dict[str, List[str]] = {}
    for f in files:
        if f.action == "move":
            (wiki_root / f.real_rel).parent.mkdir(parents=True, exist_ok=True)
            _git(wiki_root, "mv", "-k", "--", f.phantom_rel, f.real_rel)
            moved.append(f.real_rel)
            continue
        if f.action == "fold":
            real_path = wiki_root / f.real_rel
            before = real_path.read_text(encoding="utf-8")
            fold_text = Path(f.fold).read_text(encoding="utf-8") if f.fold else None
            after = folded_text(before, fold_text, f.ids)
            if after != before:
                real_path.write_text(after, encoding="utf-8")
                _git(wiki_root, "add", "--", f.real_rel)
                if fold_text:
                    folded.append(f.real_rel)
                if set(f.ids) - markers_in(before):
                    markers_merged[f.real_rel] = sorted(set(f.ids) - markers_in(before))
        _git(wiki_root, "rm", "-q", "--", f.phantom_rel)
        removed.append(f.phantom_rel)
    # git rm prunes emptied directories; sweep any stragglers so the dir is truly gone.
    phantom_dir = wiki_root / PHANTOM_DIR
    for d in sorted((p for p in phantom_dir.rglob("*") if p.is_dir()), reverse=True) if phantom_dir.exists() else []:
        if not any(d.iterdir()):
            d.rmdir()
    if phantom_dir.exists() and not any(phantom_dir.iterdir()):
        phantom_dir.rmdir()
    if phantom_dir.exists():
        raise PhantomError(f"{phantom_dir} still has untracked content; aborting before commit")

    if _git(wiki_root, "diff", "--cached", "--quiet", check=False).returncode == 0:
        raise PhantomError("nothing staged; aborting")
    msg = (f"reconcile: fold + remove phantom wiki/ tree ({len(removed)} removed, "
           f"{len(folded)} folded, {len(moved)} moved)")
    _git(wiki_root, "commit", "-q", "-m", msg)
    sha = _git(wiki_root, "rev-parse", "HEAD").stdout.strip()

    shadow_synced: Optional[bool] = None
    if shadow_root is not None:
        if shadow_in_sync:
            _git(shadow_root, "merge", "--ff-only", "-q", "master")
            shadow_synced = True
        else:
            shadow_synced = False   # unpromoted shadow commits: next promote carries the delete
    return {"applied": True, "commit": sha, "removed": removed, "folded": folded, "moved": moved,
            "markers_merged": markers_merged, "shadow_synced": shadow_synced,
            "push": f"git -C {wiki_root} push origin master"}
