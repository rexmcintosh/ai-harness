"""Review findings Rex actually sees. The drain's nightly council reviews of
each repo's main were saved to outputs/reviews/ and read by nobody (581 in a
week, ~20 of them "request changes"). After every drain `review` job the saved
markdown's "### Recommendation" paragraph is read; when it asks for changes,
one record lands in <state_dir>/findings.json with status "new". `diem
findings` lists them and `--ack` marks them seen; bebop/findings_line.py turns
the count of "new" into one morning briefing line.

Parsing is plain text matching, no model. Only a recommendation that opens
with a request for changes (or block / reject / do not merge) counts; every
flavour of "Approve ..." does not, even "approve with fixes". Anything that
cannot be read is skipped, never raised: a findings hiccup must not cost the
drain its night."""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from .state import _atomic_write

FILENAME = "findings.json"
LINE_MAX = 160

_HEADING = re.compile(r"^### Recommendation\b.*$", re.M)
_REVIEW_NAME = re.compile(r"^(?P<repo>.+)-(?P<id>[0-9a-f]{32})\.md$")
# Leading markdown noise before the verdict word: bold, quotes, bullets, spaces.
_LEAD = re.compile(r"^[\s*_>#`\-]+")
_BLOCKING = ("request changes", "request a ", "request small", "request minor",
             "changes requested", "changes required", "needs changes",
             "block", "reject", "do not merge", "don't merge")


def parse_recommendation(text: str) -> str | None:
    """The first paragraph under the first `### Recommendation` heading,
    joined onto one line. None when there is no heading or no paragraph."""
    m = _HEADING.search(text or "")
    if not m:
        return None
    para: list[str] = []
    for line in text[m.end():].lstrip("\n").splitlines():
        if not line.strip():
            break
        if line.startswith("#"):
            break
        para.append(line.strip())
    return " ".join(para) or None


def is_blocking(rec: str | None) -> bool:
    """True when the recommendation opens by asking for changes before merge."""
    if not rec:
        return False
    head = _LEAD.sub("", rec).lower()
    if head.startswith("approve"):
        return False
    return head.startswith(_BLOCKING)


def one_line(rec: str) -> str:
    """First sentence, printable, at most LINE_MAX characters."""
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in rec).split())
    m = re.search(r"[.;:!?](\s|$)", text)
    first = text[:m.start() + 1] if m else text
    if len(first) > LINE_MAX:
        first = first[:LINE_MAX - 3].rstrip() + "..."
    return first


class Findings:
    """findings.json: a list of records, one per review id. A file that does
    not parse is left alone (writes refuse) rather than overwritten, so an
    operator's acks are never silently lost."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / FILENAME
        self.broken = False
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            data = []
        except (OSError, ValueError):
            data, self.broken = [], True
        if not isinstance(data, list):
            data, self.broken = [], True
        self.data = [r for r in data if isinstance(r, dict) and r.get("id")]

    def all(self) -> list[dict]:
        return list(self.data)

    def new(self) -> list[dict]:
        return [r for r in self.data if r.get("status") == "new"]

    def _save(self) -> None:
        if self.broken:
            raise ValueError(f"{self.path} does not parse; not overwriting it")
        _atomic_write(self.path, json.dumps(self.data, indent=1))

    def add(self, rec: dict) -> bool:
        """Append unless this review id is already recorded (idempotent)."""
        if self.broken or any(r["id"] == rec["id"] for r in self.data):
            return False
        self.data.append(rec)
        self._save()
        return True

    def ack(self, ids) -> list[str]:
        """Mark findings acked by full id or unique prefix. Returns acked ids;
        unknown or ambiguous prefixes ack nothing."""
        done = []
        for want in ids:
            hits = [r for r in self.data if r["id"].startswith(str(want))]
            if len(hits) != 1:
                continue
            hits[0]["status"] = "acked"
            done.append(hits[0]["id"])
        if done:
            self._save()
        return done


def _reviewed(payload: dict) -> str:
    if payload.get("diff"):
        return "working-tree diff"
    return str(payload.get("range") or payload.get("head") or "?")


def _record(item_id: str, repo: str, reviewed: str, day: str, output_path: str):
    text = Path(output_path).read_text(encoding="utf-8", errors="replace")
    rec = parse_recommendation(text)
    if not is_blocking(rec):
        return None
    return {"id": item_id, "repo": repo, "reviewed": reviewed, "date": day,
            "recommendation": one_line(rec), "output_path": str(output_path),
            "status": "new"}


def record_review(state_dir: Path, item, output_path: str | None, *,
                  now: datetime) -> bool:
    """Called after a successful drain review. True when a new finding was
    written. Never raises."""
    try:
        if not output_path:
            return False
        rec = _record(item.id, Path(item.payload.get("repo", "?")).name,
                      _reviewed(item.payload), now.date().isoformat(), output_path)
        return bool(rec) and Findings(state_dir).add(rec)
    except Exception:  # noqa: BLE001 — a findings hiccup must not stop the drain
        return False


def backfill(state_dir: Path, outputs_dir: Path, *, days: int = 14,
             now: datetime) -> int:
    """Record findings from review outputs saved in the last `days` days.
    Range and date come from the archived queue item when it is still there,
    else the file name and mtime. Idempotent. Returns the number added."""
    findings = Findings(state_dir)
    if findings.broken:
        raise ValueError(f"{findings.path} does not parse; fix or move it first")
    cutoff = (now - timedelta(days=days)).timestamp()
    added = 0
    files = sorted(Path(outputs_dir, "reviews").glob("*.md"),
                   key=lambda p: p.stat().st_mtime)
    for path in files:
        m = _REVIEW_NAME.match(path.name)
        if not m or path.stat().st_mtime < cutoff:
            continue
        item_id, repo = m.group("id"), m.group("repo")
        reviewed = "?"
        day = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
        try:
            arch = json.loads((Path(state_dir) / "archive" / f"{item_id}.json").read_text())
            reviewed = _reviewed(arch.get("payload") or {})
            day = str(arch.get("created") or day)[:10]
        except (OSError, ValueError, AttributeError):
            pass
        try:
            rec = _record(item_id, repo, reviewed, day, str(path))
        except OSError:
            continue
        if rec and findings.add(rec):
            added += 1
    return added
