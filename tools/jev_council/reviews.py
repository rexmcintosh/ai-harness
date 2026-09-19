"""Read saved council reviews (the markdown `council review --format md` prints) back into data.

Read-only. The reviews live outside this repo: the backlog runner's state directory and the
backlog repo's evidence folders. Nothing here is copied into the repo.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# The data-scope rule (which repos may be sent to TypeSafe) lives once, in council/jev.py.
# A saved review's id does not name its repo, so scope is decided from the backlog's
# id -> repo map, and an id the map does not know is refused.
from council.jev import OUT_OF_SCOPE, OUT_OF_SCOPE_REPOS, in_scope   # noqa: F401  (re-exported)

_REC = re.compile(r"^### Recommendation \(confidence (\d+)/10\)\s*$", re.M)
_SEAT = re.compile(r"^#### (?P<name>.+?) · (?P<model>\S+) — (?P<stance>\S+)\s*$", re.M)
_FINDING = re.compile(r"^- `(?P<sev>[^`]+)` \(c(?P<conf>\d+)\) (?P<text>.+)$")
_PANEL = re.compile(r"^\[panel: (?P<panel>[\w-]+)", re.M)


@dataclass
class Seat:
    name: str
    model: str
    stance: str
    headline: str = ""


@dataclass
class SeatFinding:
    fid: str            # F<seat number>.<finding number>, stable for a saved review
    seat: str
    severity: str
    confidence: int
    text: str
    tentative: bool = False


@dataclass
class Review:
    rid: str
    repo: str = ""
    checksum: str = ""      # of the file's review content; identical copies share it
    panel: str = ""
    recommendation: str = ""
    rec_confidence: int = 0
    consensus: list[str] = field(default_factory=list)
    seats: list[Seat] = field(default_factory=list)
    findings: list[SeatFinding] = field(default_factory=list)
    chair_blocks: list[dict] = field(default_factory=list)


def _bullets(block: str) -> list[str]:
    return [ln[2:].strip() for ln in block.splitlines() if ln.startswith("- ")]


def parse_review(text: str, rid: str) -> Review:
    r = Review(rid=rid)
    if (m := _PANEL.search(text)):
        r.panel = m.group("panel")
    if (m := _REC.search(text)):
        r.rec_confidence = int(m.group(1))
        rest = text[m.end():]
        r.recommendation = rest.split("\n**", 1)[0].split("\n---", 1)[0].strip()
        if "**Consensus:**" in rest:
            r.consensus = _bullets(rest.split("**Consensus:**", 1)[1].split("\n**", 1)[0].split("\n---", 1)[0])
    seats = list(_SEAT.finditer(text))
    for n, m in enumerate(seats, 1):
        body = text[m.end():seats[n].start() if n < len(seats) else len(text)]
        body = body.split("### Raw chair response", 1)[0]
        head = re.search(r"^_(.*)_\s*$", body, re.M)
        r.seats.append(Seat(m.group("name"), m.group("model"), m.group("stance"),
                            (head.group(1) if head else "").strip()))
        k = 0
        for line in body.splitlines():
            if (f := _FINDING.match(line)):
                k += 1
                body_text = f.group("text")
                tentative = body_text.rstrip().endswith("_(tentative)_")
                r.findings.append(SeatFinding(
                    f"F{n}.{k}", m.group("name"), f.group("sev").lower(), int(f.group("conf")),
                    body_text.replace("_(tentative)_", "").strip(), tentative))
    raw = re.search(r"### Raw chair response.*?```json\s*(\{.*?\})\s*```", text, re.S)
    if raw:
        try:
            blocks = json.loads(raw.group(1)).get("blocking_findings") or []
            r.chair_blocks = [b for b in blocks if isinstance(b, dict)]
        except ValueError:
            pass
    return r


def backlog_repo_map(*yaml_paths: Path) -> dict[str, str]:
    """{item id: repo} from the backlog's active and archive files."""
    import yaml
    out = {}
    for path in yaml_paths:
        doc = yaml.safe_load(Path(path).expanduser().read_text()) or {}
        for item in doc.get("items") or []:
            if item.get("id") and item.get("repo"):
                out[str(item["id"])] = str(item["repo"])
    return out


_RUN_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{6}\.\d+Z-")


def _repo_for(rid: str, repo_of: dict[str, str]) -> str | None:
    """Exact item id only. A runner record is "<UTC run stamp>-<item id>" and its legacy copy
    is "<item id>"; a looser suffix match could hand one item another item's repo and quietly
    defeat the scope rule."""
    return repo_of.get(_RUN_STAMP.sub("", rid, count=1))


def load_reviews(directory: Path, *, repo_of: dict[str, str] | None = None,
                 default_repo: str | None = None) -> list[Review]:
    """Reviews under `directory` that are in scope. Pass `repo_of` for runner records, or
    `default_repo` for a folder whose reviews all belong to one known repo."""
    out = []
    for p in sorted(Path(directory).expanduser().glob("*.md")):
        rid = p.stem.replace(".council", "")
        # A named repo wins over the folder default, and scope is judged on that FINAL repo,
        # so one out-of-scope file inside a trusted folder is still refused.
        repo = _repo_for(rid, repo_of or {}) or default_repo
        if not in_scope(rid, repo):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        r = parse_review(text, rid)
        if r.recommendation:
            r.repo = repo
            body = text[text.index("### Recommendation"):]
            r.checksum = hashlib.sha256(body.encode()).hexdigest()
            out.append(r)
    return out


def unique_reviews(reviews: list[Review]) -> list[Review]:
    """Drop byte-identical copies (the runner keeps a legacy copy of its latest review).
    Two different reviews that happen to share a verdict sentence are both kept."""
    seen, out = set(), []
    for r in reviews:
        if r.checksum not in seen:
            seen.add(r.checksum)
            out.append(r)
    return out
