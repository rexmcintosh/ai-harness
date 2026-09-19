"""Loads every multiple-choice question from the sat-prep content batches.
READ-ONLY against /home/dev/projects/sat-prep — this module never writes there.
"""
from __future__ import annotations
import json
import glob
import os

BATCH_DIR = "/home/dev/projects/sat-prep/content/batches"


def load_mc_questions():
    """Returns a list of dicts, one per multiple-choice question, in file order.
    spr (student-produced response) questions are excluded entirely.
    """
    out = []
    for path in sorted(glob.glob(os.path.join(BATCH_DIR, "*.json"))):
        batch_id = os.path.basename(path)[:-5]
        data = json.load(open(path))
        for idx, q in enumerate(data):
            if q.get("format") != "mc":
                continue
            out.append({
                "id": f"{batch_id}#{idx:03d}",
                "batch": batch_id,
                "section": q["section"],
                "domain": q["domain"],
                "skill": q["skill"],
                "difficulty": q["difficulty"],
                "passage": q.get("passage"),
                "stem": q["stem"],
                "choices": q["choices"],
                "answer": q["answer"],
                "needs_figure": bool(q.get("figure_svg")),
            })
    return out


if __name__ == "__main__":
    qs = load_mc_questions()
    print(f"loaded {len(qs)} mc questions")
    needs_fig = sum(1 for q in qs if q["needs_figure"])
    print(f"needs_figure: {needs_fig}")
