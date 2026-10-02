"""Runs Jev's blind-solve check on every mc question in sat-prep/content/batches.

For each question, one Jev call asks two questions against ONE state
(passage + stem + choices; never the key or explanations):
  (a) a Choice over the question's own answer letters (options = the actual choice texts)
  (b) a Noul: "exactly one of the answer choices is defensible as correct"

Wording is frozen in questions_wording.json (written before any Jev output was seen).
Writes results.json: per question, the key, Jev's choice + per-option probabilities +
confidence, and the noul value. Never modifies content/sat-prep.
"""
from __future__ import annotations
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # jevlab/
from jevlib import noul, choice, ask_many  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # jevlab/sat/
from load_data import load_mc_questions  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WORDING = json.load(open(os.path.join(HERE, "questions_wording.json")))


def build_state(q):
    tpl = WORDING["state_template"]["with_passage" if q["passage"] else "without_passage"]
    return tpl.format(
        passage=q["passage"] or "",
        stem=q["stem"],
        A=q["choices"]["A"], B=q["choices"]["B"], C=q["choices"]["C"], D=q["choices"]["D"],
    )


def build_questions(item):
    q = item["_q"]
    return {
        "answer": choice(WORDING["choice_instructions"], {
            "A": q["choices"]["A"], "B": q["choices"]["B"],
            "C": q["choices"]["C"], "D": q["choices"]["D"],
        }),
        "one_defensible": noul(
            WORDING["noul_instructions"],
            true=WORDING["noul_criteria"]["true"],
            false=WORDING["noul_criteria"]["false"],
        ),
    }


def main():
    questions = load_mc_questions()
    items = [{"id": q["id"], "state": build_state(q), "_q": q} for q in questions]
    print(f"running {len(items)} questions through Jev...")
    raw = ask_many(items, build_questions, workers=12)

    by_id = {q["id"]: q for q in questions}
    results = []
    errors = []
    for r in raw:
        q = by_id[r["id"]]
        if "error" in r:
            errors.append({"id": r["id"], "error": r["error"]})
            results.append({
                **{k: q[k] for k in ("id", "batch", "section", "domain", "skill",
                                      "difficulty", "answer", "needs_figure")},
                "error": r["error"],
            })
            continue
        ans = r["answers"]["answer"]
        nd = r["answers"]["one_defensible"]
        results.append({
            "id": q["id"], "batch": q["batch"], "section": q["section"],
            "domain": q["domain"], "skill": q["skill"], "difficulty": q["difficulty"],
            "needs_figure": q["needs_figure"],
            "key": q["answer"],
            "jev_choice": ans["choice"],
            "jev_confidence": ans["confidence"],
            "jev_probabilities": ans["probabilities"],
            "jev_top_probability": max(ans["probabilities"].values()),
            "noul_one_defensible": nd["noul"],
            "correct": ans["choice"] == q["answer"],
            "input_tokens": r["input_tokens"],
        })

    out_path = os.path.join(HERE, "results.json")
    json.dump(results, open(out_path, "w"), indent=2)
    print(f"wrote {len(results)} results ({len(errors)} errors) to {out_path}")
    if errors:
        print("errors:", errors[:10])


if __name__ == "__main__":
    main()
