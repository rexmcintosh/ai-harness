"""Run the frozen questions (questions_wording.json) over the evaluation set.

State per item = {repository, title, task}. Nothing about the outcome is sent.
Writes results.json: one row per item with the raw Jev answers.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from jevlib import ask_many, noul, score  # noqa: E402

W = json.loads((HERE / "questions_wording.json").read_text())["questions"]
QUESTIONS = {
    "A_outward": noul(W["A_outward"]["instructions"],
                      true=W["A_outward"]["criteria"]["true"],
                      false=W["A_outward"]["criteria"]["false"]),
    "B_selfcontained": noul(W["B_selfcontained"]["instructions"],
                            true=W["B_selfcontained"]["criteria"]["true"],
                            false=W["B_selfcontained"]["criteria"]["false"]),
    "C_onepass": score(W["C_onepass"]["instructions"], W["C_onepass"]["criteria"]),
}

dataset = json.loads((HERE / "dataset.json").read_text())
items = [{"id": d["id"], "state": {"repository": d["repo"], "title": d["title"], "task": d["prompt"]}}
         for d in dataset]

out = ask_many(items, QUESTIONS, workers=8)
by_id = {r["id"]: r for r in out}

rows = []
for d in dataset:
    r = by_id[d["id"]]
    row = {k: d[k] for k in ("id", "repo", "title", "status", "outcome_truth", "hold_reason",
                             "session_ran", "run_cost_usd", "run_minutes", "session_outcome", "timed_out")
           if k in d}
    if "error" in r:
        row["error"] = r["error"]
    else:
        a = r["answers"]
        row["jev_A"] = a["A_outward"]["noul"]
        row["jev_B"] = a["B_selfcontained"]["noul"]
        row["jev_C_score"] = a["C_onepass"]["score"]
        row["jev_C_conf"] = a["C_onepass"]["confidence"]
        row["jev_C_probs"] = a["C_onepass"]["probabilities"]
        row["input_tokens"] = r["input_tokens"]
        row["seconds"] = r["seconds"]
    rows.append(row)

(HERE / "results.json").write_text(json.dumps(rows, indent=1))
ok = [r for r in rows if "error" not in r]
print(f"wrote results.json: {len(ok)} ok, {len(rows) - len(ok)} errors")
