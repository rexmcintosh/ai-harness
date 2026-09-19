#!/usr/bin/env python3
"""Run both Jev gate designs over the rebuilt clarity-scan evaluation set.

Design A: the whole chapter is the state; one Noul ("at least one sentence fails to resolve
          to a single meaning on a first linear read") plus a 4-level Score.
Design B: the chapter is cut into consecutive ~400-500 word windows on paragraph
          boundaries; the SAME Noul per window. Chapter score = max window probability
          (the mean is recorded too).

Question wording is loaded from questions_wording.json and was frozen before the first call.

  python3 run.py smoke      # 3 chapters, both designs
  python3 run.py a          # design A over every row
  python3 run.py b          # design B over every row
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from jevlib import ask_many, noul, score  # noqa: E402

Q = json.load(open(HERE / "questions_wording.json"))
A = Q["design_a"]["questions"]
BREAKS = noul(A["breaks"]["instructions"],
              true=A["breaks"]["criteria"]["true"],
              false=A["breaks"]["criteria"]["false"])
CLEAN = score(A["clean"]["instructions"], A["clean"]["criteria"])
QUESTIONS_A = {"breaks": BREAKS, "clean": CLEAN}
QUESTIONS_B = {"breaks": BREAKS}

TARGET_WORDS = 450
MAX_WORDS = 600


def windows(text: str) -> list[dict]:
    """Consecutive ~400-500 word windows on paragraph boundaries, with char offsets."""
    paras, pos = [], 0
    for block in text.split("\n\n"):
        start = text.find(block, pos)
        paras.append((start, start + len(block), block))
        pos = start + len(block)
    out, cur, n = [], [], 0
    for start, end, block in paras:
        w = len(block.split())
        if cur and n + w > MAX_WORDS:
            out.append(cur)
            cur, n = [], 0
        cur.append((start, end, block))
        n += w
        if n >= TARGET_WORDS:
            out.append(cur)
            cur, n = [], 0
    if cur:
        if out and n < 120:                      # fold a short tail into the previous window
            out[-1].extend(cur)
        else:
            out.append(cur)
    res = []
    for i, group in enumerate(out):
        body = "\n\n".join(b for _, _, b in group)
        res.append({"i": i, "start": group[0][0], "end": group[-1][1],
                    "words": len(body.split()), "text": body})
    return res


def unpack(answer) -> dict:
    """Normalise one Jev answer into plain numbers, keeping the raw shape for the record."""
    if not isinstance(answer, dict):
        return {"raw": answer}
    out = {}
    for k in ("probability", "confidence", "value", "score", "level", "choice", "answer",
              "probabilities", "index"):
        if k in answer:
            out[k] = answer[k]
    return out or {"raw": answer}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    ds = json.load(open(HERE / "dataset.json"))
    if mode == "smoke":
        ds = ds[:3]

    if mode in ("smoke", "a"):
        items = [{"id": d["row_id"], "state": d["text"]} for d in ds]
        res = ask_many(items, QUESTIONS_A, workers=16)
        out = {r["id"]: r for r in res}
        path = HERE / ("smoke_a.json" if mode == "smoke" else "jev_a.json")
        path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        print("wrote", path)

    if mode in ("smoke", "b"):
        items, meta = [], {}
        for d in ds:
            for w in windows(d["text"]):
                wid = f"{d['row_id']}#w{w['i']}"
                items.append({"id": wid, "state": w["text"]})
                meta[wid] = {"row_id": d["row_id"], "i": w["i"], "start": w["start"],
                             "end": w["end"], "words": w["words"]}
        print(f"design B: {len(items)} windows over {len(ds)} chapters "
              f"({len(items)/len(ds):.1f} per chapter)")
        res = ask_many(items, QUESTIONS_B, workers=16)
        out = {r["id"]: {**r, **meta[r["id"]]} for r in res}
        path = HERE / ("smoke_b.json" if mode == "smoke" else "jev_b.json")
        path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
