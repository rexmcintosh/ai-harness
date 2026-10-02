"""Recover `list[MemberResult]` JSON from a rendered council GitHub comment.

The chair bake-off replays real panels so that only the chair is billed and every
candidate arbitrates identical input. Those panels were never saved as objects —
the only surviving record is the `github-actions[bot]` comment each gate run
posted, rendered by `council/render.py::render_markdown`. This inverts that
rendering.

    python3 tools/regress/recover_panel.py <comment.md> tools/regress/panels/<id>.json

Fetch the source comment with, e.g.:

    gh api repos/<owner>/<repo>/issues/comments/<id> --jq .body > comment.md

**Known, accepted lossiness.** `render_markdown` calls `gate_findings`, which at
`daily` rigor drops findings below confidence 5 before rendering. Those cannot be
recovered, so a recovered panel can be missing a sub-c5 nit that the original
chair digest carried. Nothing below c5 clears any tier's candidate bar, so the
gate arithmetic is unaffected; only the digest text differs slightly. Everything
at c5 and above — including every finding that can gate a merge — round-trips.
""" 
import json
import re
import sys
from pathlib import Path

HEAD = re.compile(r"^#### (?P<member>.+?) · (?P<model>\S+) — (?P<stance>\S+)\s*$")
FIND = re.compile(r"^- `(?P<sev>[a-z]+)` \(c(?P<conf>\d+)\) (?P<point>.*)$")
SUGG = re.compile(r"^- _\(suggestion\)_ (?P<text>.*)$")
ERRD = re.compile(r"^_errored: (?P<err>.*)_$")
TENT = " _(tentative)_"


def parse(md: str):
    body = md.split("<details><summary>Raw panel</summary>", 1)[1]
    body = body.split("</details>", 1)[0]
    members, cur, expect_headline = [], None, False
    for line in body.splitlines():
        line = line.rstrip()
        m = HEAD.match(line)
        if m:
            cur = {"member": m["member"], "model": m["model"], "stance": m["stance"],
                   "headline": "", "findings": [], "suggestions": [], "error": None}
            members.append(cur)
            expect_headline = True
            continue
        if cur is None or not line:
            continue
        e = ERRD.match(line)
        if e:
            cur["error"] = e["err"]
            expect_headline = False
            continue
        if expect_headline and line.startswith("_") and line.endswith("_"):
            # `__` is render_markdown's empty headline (f"_{r.headline}_" with headline "").
            cur["headline"] = "" if line == "__" else line[1:-1]
            expect_headline = False
            continue
        f = FIND.match(line)
        if f:
            point = f["point"]
            if point.endswith(TENT):
                point = point[: -len(TENT)]
            cur["findings"].append({"point": point.strip(),
                                    "severity": f["sev"], "confidence": int(f["conf"])})
            continue
        s = SUGG.match(line)
        if s:
            cur["suggestions"].append(s["text"].strip())
            continue
    return members


if __name__ == "__main__":
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    out = parse(src.read_text())
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"{src.name} -> {dst}: {len(out)} members, "
          f"{sum(len(m['findings']) for m in out)} findings, "
          f"{sum(len(m['suggestions']) for m in out)} suggestions")
