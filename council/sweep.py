"""`council sweep` — autonomous, repo-wide security research.

Beyond the diff-only PR review: walk the whole tree, fan the security panel across
it chunk by chunk, aggregate + dedup the findings, gate by confidence, and let the
chair write a phone-glanceable summary. Explicitly *bounded* (max_chunks) and it
*reports what it drops* — never a silent cap. It reports; it opens nothing.
"""
from __future__ import annotations

import concurrent.futures
import re
from pathlib import Path

from .models import Panel, Finding, SweepFinding, SweepReport
from .engine import run_panel
from .prompts import SWEEP_SUMMARY
from .jsonparse import loads_lenient

_SEVERITY_ORDER = {"critical": 4, "high": 3, "med": 2, "medium": 2, "low": 1, "info": 0}


def _looks_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(4096)
    except OSError:
        return True


def chunk_repo(path: str, *, cap: int, max_chunks: int):
    """Walk `path` into one labeled chunk per source file. Skips dotfiles, binaries,
    and symlinks (the same exclusions as `council review`). Bounded by `max_chunks`;
    returns ``(chunks, dropped)`` where ``dropped`` is the count of eligible files
    left out by the cap — surfaced so coverage is never silently truncated."""
    root = Path(path)
    files = sorted(root.rglob("*")) if root.is_dir() else [root]
    eligible = []
    for f in files:
        if f.is_symlink() or not f.is_file():
            continue
        if any(p.startswith(".") for p in f.parts):
            continue
        if _looks_binary(f):
            continue
        eligible.append(f)

    chunks = []
    for f in eligible[:max_chunks]:
        try:
            text = f.read_text(errors="ignore")[:cap]
        except OSError:
            continue
        chunks.append((str(f), text))
    dropped = max(0, len(eligible) - max_chunks)
    return chunks, dropped


def _norm(point: str) -> str:
    """Normalize a finding's text for dedup: lowercase, collapse whitespace, drop
    punctuation, keep the leading signal. Near-identical points from different
    panelists/chunks collapse to one."""
    s = re.sub(r"[^a-z0-9 ]+", "", point.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return s[:80]


def dedup_findings(tagged) -> list[SweepFinding]:
    """Merge ``(chunk_label, member, Finding)`` tuples into ``SweepFinding``s,
    keyed by (severity, normalized point). Keeps the highest confidence and the
    union of locations + sources."""
    merged: dict[tuple[str, str], SweepFinding] = {}
    for label, member, f in tagged:
        key = (f.severity, _norm(f.point))
        sf = merged.get(key)
        if sf is None:
            merged[key] = SweepFinding(point=f.point, severity=f.severity,
                                       confidence=f.confidence,
                                       locations=[label], sources=[member])
        else:
            sf.confidence = max(sf.confidence, f.confidence)
            if label not in sf.locations:
                sf.locations.append(label)
            if member not in sf.sources:
                sf.sources.append(member)
    return list(merged.values())


def _keep(f: Finding, min_conf: int) -> bool:
    """Gate: critical is always kept; everything else needs confidence >= min_conf."""
    return f.severity == "critical" or f.confidence >= min_conf


def _jev_note(findings, jev_repo):
    """Shadow only: which of the reported findings would Jev also group? Runs AFTER the
    chair's summary and never touches `findings`. The sweep itself cannot know which
    repository its chunks came from, so the caller names it (`jev_repo`); without a name,
    or for a repo that is not on the council's allow list, nothing is sent."""
    if not jev_repo:
        return None
    try:
        from . import signals
        return signals.collect_sweep(jev_repo, findings)
    except Exception:  # noqa: BLE001
        return None


def run_sweep(chunks, panel: Panel, client, *, chair_model: str,
              min_conf: int = 7, max_workers=None, budget=None, jev_repo=None) -> SweepReport:
    """Run `panel` over each chunk, gate + dedup the findings, sort worst-first, and
    have the chair summarize. A chair failure surfaces in ``error`` but never drops
    the findings. `jev_repo` (the swept repository's name) allows the display-only Jev
    note; it changes no finding, count, order or summary."""
    chunks = list(chunks)
    workers = max_workers or min(8, max(1, len(chunks)))
    tagged = []

    def _scan(chunk):
        label, text = chunk
        ctx = f"Security review of {label}. Find real, exploitable issues:\n\n{text}"
        return label, run_panel(panel, ctx, client, budget=budget)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for label, results in pool.map(_scan, chunks):
            for r in results:
                for f in r.findings:
                    if _keep(f, min_conf):
                        tagged.append((label, r.member, f))

    findings = dedup_findings(tagged)
    findings.sort(key=lambda f: (-_SEVERITY_ORDER.get(f.severity, 0), -f.confidence))

    report = SweepReport(findings=findings, chunks_scanned=len(chunks))
    if not findings:
        report.summary = "No findings above the confidence gate."
        return report
    digest = "\n".join(f"- [{f.severity} c{f.confidence}] {f.point} "
                       f"(in {', '.join(f.locations)})" for f in findings)
    try:
        d = loads_lenient(client.complete(
            chair_model, SWEEP_SUMMARY, digest,
            max_completion_tokens=budget.chair if budget else None))
        report.summary = str(d.get("summary", ""))
    except Exception as e:  # noqa: BLE001
        report.error = f"{type(e).__name__}: {e}"
        report.summary = "(summary unavailable — see findings below)"
    report.jev_shadow = _jev_note(findings, jev_repo)
    return report
