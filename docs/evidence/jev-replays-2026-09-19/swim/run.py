"""Run Jev over two SEPARATE truth sets for the SwimTrack editorial scope-gate /
pillar-label replay experiment. Reads the pinned question wording from
questions_wording.json (written and frozen before this script was ever run) and
the historical Opus-decided candidates directly from the read-only editorial
repo cache. Never edits questions_wording.json. Writes results.json.
"""
from __future__ import annotations
import json
import sys

sys.path.insert(0, "/tmp/claude-1000/-home-dev-projects/3353706c-f59c-4c56-a420-e17dc8b669a6/scratchpad/jevlab")
from jevlib import noul, choice, score as score_q, ask_many

SWIM_DIR = "/tmp/claude-1000/-home-dev-projects/3353706c-f59c-4c56-a420-e17dc8b669a6/scratchpad/jevlab/swim"
CACHE_DIR = "/home/dev/projects/swimtrack/editorial/.cache"

with open(f"{SWIM_DIR}/questions_wording.json") as f:
    QW = json.load(f)

QUESTIONS = {
    "in_scope": noul(QW["in_scope"]["instructions"],
                      true=QW["in_scope"]["criteria"]["true"],
                      false=QW["in_scope"]["criteria"]["false"]),
    "pillar": choice(QW["pillar"]["instructions"], QW["pillar"]["criteria"]),
    "parent_value": score_q(QW["parent_value"]["instructions"], QW["parent_value"]["criteria"]),
}


def make_state(title, source, excerpt):
    return {"title": title, "source": source, "excerpt": (excerpt or "")[:600]}


# ---- Truth source A: Opus history (the single real scan, 2026-06-07) ----
# Both candidates that reached Opus AND produced a full recorded decision.
opus_history_items = []
opus_history_truth = {}
for cid in ("360308060d62", "ab781d3508dc"):
    with open(f"{CACHE_DIR}/candidates/{cid}.json") as f:
        cand = json.load(f)
    with open(f"{CACHE_DIR}/stacks/{cid}.json") as f:
        stack = json.load(f)
    opus_history_items.append({
        "id": cid,
        "state": make_state(cand["title"], cand["source"], cand["raw_excerpt"]),
    })
    opus_history_truth[cid] = {
        "title": cand["title"], "source": cand["source"],
        "in_scope": True,           # both reached the stack stage => Opus said in_scope=true
        "pillar": stack["pillar"],
    }
# The scan_report recorded filtered=3 additional candidates that Opus marked
# out-of-scope in that same run, but the pipeline version at the time did not
# persist their title/text/pillar (no filtered_items in scan_report.json, no
# candidate file written for a filtered item) -- so those 3 decisions exist as
# a bare count only. They cannot be replayed against Jev (no state to send).
opus_history_untestable_filtered_count = 3

# ---- Truth source B: my own blind labels over 45 freshly-collected candidates ----
with open(f"{SWIM_DIR}/collected_candidates.json") as f:
    collected = json.load(f)["candidates"]
with open(f"{SWIM_DIR}/labels_blind.json") as f:
    blind_labels = {l["id"]: l for l in json.load(f)}

blind_items = []
blind_truth = {}
for c in collected:
    lbl = blind_labels[c["id"]]
    blind_items.append({
        "id": c["id"],
        "state": make_state(c["title"], c["source"], c["raw_excerpt"]),
    })
    blind_truth[c["id"]] = {
        "title": c["title"], "source": c["source"],
        "in_scope": lbl["in_scope"], "pillar": lbl["pillar"],
        "marginal": lbl["marginal"], "reason": lbl["reason"],
        "rank_score": c["rank_score"], "would_reach_opus_top5": c["would_reach_opus_top5"],
    }

print(f"Opus-history items to send: {len(opus_history_items)} "
      f"(+ {opus_history_untestable_filtered_count} filtered decisions with no recoverable text)")
print(f"Blind-labelled items to send: {len(blind_items)}")

all_items = opus_history_items + blind_items
print("Calling Jev...")
raw_results = ask_many(all_items, QUESTIONS)

results_by_id = {r["id"]: r for r in raw_results}

out = {
    "questions": "see questions_wording.json (frozen before this run)",
    "opus_history": {
        "n_with_full_decision": len(opus_history_items),
        "n_filtered_no_text": opus_history_untestable_filtered_count,
        "scan_date": "2026-06-07T22:33:47Z",
        "items": [
            {"id": i["id"], "truth": opus_history_truth[i["id"]],
             "jev": results_by_id.get(i["id"])}
            for i in opus_history_items
        ],
    },
    "blind_labelled": {
        "n": len(blind_items),
        "collected_at": json.load(open(f"{SWIM_DIR}/collected_candidates.json"))["generated_at"],
        "select_top_n": json.load(open(f"{SWIM_DIR}/collected_candidates.json"))["select_top_n"],
        "items": [
            {"id": i["id"], "truth": blind_truth[i["id"]],
             "jev": results_by_id.get(i["id"])}
            for i in blind_items
        ],
    },
}

errors = [r for r in raw_results if "error" in r]
if errors:
    print(f"WARNING: {len(errors)} Jev call errors:", errors[:5])

with open(f"{SWIM_DIR}/results.json", "w") as f:
    json.dump(out, f, indent=2)
print(f"wrote {SWIM_DIR}/results.json")
