"""Experiments that need KNOWN answers: the three regression fixtures and the chair bake-off.

    python3 -m tools.jev_council.fixtures_run --dry-run
    python3 -m tools.jev_council.fixtures_run --out DIR

link      Which panel finding does a chair block confirm? 4 real chair blocks from the bake-off
          (all about comment ownership, true source baw-pr11 F1.1), each asked three ways: with
          the right panel (answer F1.1), with the right panel minus F1.1 (answer none: same
          file and function, different problem) and with another fixture's panel (answer none).
kinds     Re-score the 19 fixture findings whose real-world outcome is known.
slices    The Node-compat false alarms of stw-pr11, checked against the cited code alone and
          again with the one line of package.json that makes them moot.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import experiments as ex
from . import jev
from .reviews import SeatFinding

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ("baw-pr11", "stw-pr11", "aris-pr1")
TRUE_SOURCE = "F1.1"          # baw-pr11, Eng Manager: marker-only ownership check


def panel(fixture: str) -> list[SeatFinding]:
    seats = json.loads((ROOT / "tools/regress/panels" / f"{fixture}.json").read_text())
    return [SeatFinding(f"F{n}.{k}", s["member"], f["severity"].lower(), int(f["confidence"]), f["point"])
            for n, s in enumerate(seats, 1) if not s.get("error")
            for k, f in enumerate(s.get("findings", []), 1)]


def chair_blocks() -> list[dict]:
    runs = json.loads((ROOT / "docs/evidence/chair-bakeoff-2026-09-12.json").read_text())["runs"]
    seen, out = set(), []
    for r in runs:
        for b in r["blocking_findings"]:
            if r["fixture"] == "baw-pr11" and b["point"] not in seen:
                seen.add(b["point"])
                out.append(b)
    return out


def link_requests() -> list[tuple[ex.Request, str]]:
    out = []
    baw = panel("baw-pr11")
    for n, block in enumerate(chair_blocks()):
        out.append((ex.link_request(f"block{n}:own-panel", block, baw), TRUE_SOURCE))
        out.append((ex.link_request(f"block{n}:own-panel-without-source", block,
                                    [f for f in baw if f.fid != TRUE_SOURCE]), "none"))
        for other in ("stw-pr11", "aris-pr1"):
            out.append((ex.link_request(f"block{n}:{other}", block, panel(other)), "none"))
    return out


def slice_requests() -> list[ex.Request]:
    head = ROOT / "tools/regress/fixtures/stw-pr11/head"
    source = (head / "tools/i18n/translate.mjs").read_text().splitlines()
    code = "\n".join(source[60:100])
    engines = next((ln.strip() for ln in (head / "package.json").read_text().splitlines() if '"node"' in ln), "")
    out = []
    for f in panel("stw-pr11"):
        if "Node" not in f.text:
            continue
        out.append(ex.slice_request(f"stw:{f.fid}:code-only", f.text, code))
        out.append(ex.slice_request(f"stw:{f.fid}:code+engines", f.text,
                                    code + f"\n\n// package.json engines: {engines}"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    links = link_requests()
    kinds = [ex.finding_request(fx, f) for fx in FIXTURES for f in panel(fx)]
    slices = slice_requests()
    if args.dry_run:
        print(json.dumps({"link": len(links), "kinds": len(kinds), "slices": len(slices)}))
        return 0
    key = jev.load_key()
    if not key or not args.out:
        print("need TYPESAFE_API_KEY and --out DIR", file=sys.stderr)
        return 2
    out = args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    result = {
        "link": ex.run("link", key=key, requests=[r for r, _ in links]),
        "kinds": ex.run("kinds", key=key, requests=kinds),
        "slices": ex.run("slices", key=key, requests=slices),
    }
    for row, (_, truth) in zip(result["link"]["rows"], links):
        row["truth"] = truth
    (out / "fixtures.json").write_text(json.dumps(result, indent=1))
    print({k: (len(v["rows"]), v["errors"], v["input_tokens"]) for k, v in result.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
