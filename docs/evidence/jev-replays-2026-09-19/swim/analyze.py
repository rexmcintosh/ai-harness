from __future__ import annotations
import json

SWIM_DIR = "/tmp/claude-1000/-home-dev-projects/3353706c-f59c-4c56-a420-e17dc8b669a6/scratchpad/jevlab/swim"

with open(f"{SWIM_DIR}/results.json") as f:
    d = json.load(f)

blind = d["blind_labelled"]["items"]
history = d["opus_history"]["items"]

PILLARS = ["training", "mindset", "body", "competition", "parent_role", "pathways", "safety", "none_of_these"]


def jev_in_scope_p(item):
    return item["jev"]["answers"]["in_scope"]["noul"]


def jev_pillar_top2(item):
    probs = item["jev"]["answers"]["pillar"]["probabilities"]
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    return [k for k, _ in ranked[:2]]


def jev_score(item):
    return item["jev"]["answers"]["parent_value"]["score"]


# ---------- in_scope confusion + thresholds ----------
def confusion(items, thresh):
    tp = fp = tn = fn = 0
    for it in items:
        truth = it["truth"]["in_scope"]
        pred = jev_in_scope_p(it) >= thresh
        if truth and pred:
            tp += 1
        elif truth and not pred:
            fn += 1
        elif not truth and pred:
            fp += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": round(tp / (tp + fp), 3) if (tp + fp) else None,
            "recall": round(tp / (tp + fn), 3) if (tp + fn) else None,
            "accuracy": round((tp + tn) / len(items), 3) if items else None}


print("=" * 70)
print("BLIND SET (n=45) -- in_scope confusion at thresholds")
for t in (0.3, 0.5, 0.7):
    print(f"  threshold {t}: {confusion(blind, t)}")

print()
print("HISTORY SET (n=2, both truth in_scope=True) -- in_scope confusion at thresholds")
for t in (0.3, 0.5, 0.7):
    print(f"  threshold {t}: {confusion(history, t)}")

# ---------- pillar agreement (only meaningfully defined where truth in_scope True; but
# also report over ALL items since I gave a nominal true pillar for out-of-scope ones too) ----------
print()
print("=" * 70)
print("PILLAR AGREEMENT -- blind set, ALL 45 items (nominal true pillar even when out-of-scope)")
top1_match = 0
top2_match = 0
per_pillar = {p: {"n": 0, "top1": 0, "top2": 0} for p in PILLARS}
for it in blind:
    truth_p = it["truth"]["pillar"]
    top1 = it["jev"]["answers"]["pillar"]["choice"]
    top2 = jev_pillar_top2(it)
    per_pillar.setdefault(truth_p, {"n": 0, "top1": 0, "top2": 0})
    per_pillar[truth_p]["n"] += 1
    if top1 == truth_p:
        top1_match += 1
        per_pillar[truth_p]["top1"] += 1
    if truth_p in top2:
        top2_match += 1
        per_pillar[truth_p]["top2"] += 1
print(f"  top-1 exact match: {top1_match}/{len(blind)} = {top1_match/len(blind):.2f}")
print(f"  true pillar in Jev's top-2: {top2_match}/{len(blind)} = {top2_match/len(blind):.2f}")
print("  per true-pillar breakdown (n, top1 hits, top2 hits):")
for p, v in per_pillar.items():
    if v["n"]:
        print(f"    {p:15s} n={v['n']:2d}  top1={v['top1']}/{v['n']}  top2={v['top2']}/{v['n']}")

print()
print("PILLAR AGREEMENT -- blind set, restricted to the 4 items I labelled in_scope=True")
in_scope_items = [it for it in blind if it["truth"]["in_scope"]]
for it in in_scope_items:
    truth_p = it["truth"]["pillar"]
    top1 = it["jev"]["answers"]["pillar"]["choice"]
    top2 = jev_pillar_top2(it)
    print(f"  {it['id']} truth={truth_p:12s} jev_top1={top1:12s} jev_top2={top2} match_top1={top1==truth_p} match_top2={truth_p in top2}")

print()
print("PILLAR AGREEMENT -- Opus-history set (n=2)")
for it in history:
    truth_p = it["truth"]["pillar"]
    top1 = it["jev"]["answers"]["pillar"]["choice"]
    top2 = jev_pillar_top2(it)
    print(f"  {it['id']} truth={truth_p:12s} jev_top1={top1:12s} jev_top2={top2} match_top1={top1==truth_p} match_top2={truth_p in top2}")

# ---------- calibration by confidence bucket (in_scope: use noul distance from 0.5 as informal "confidence") ----------
print()
print("=" * 70)
print("CALIBRATION -- in_scope noul, bucketed by |p-0.5| (informal confidence), all 45 blind items")
buckets = [(0.0, 0.1), (0.1, 0.3), (0.3, 0.5)]
for lo, hi in buckets:
    bucket_items = [it for it in blind if lo <= abs(jev_in_scope_p(it) - 0.5) < hi]
    if not bucket_items:
        print(f"  |p-0.5| in [{lo},{hi}): n=0")
        continue
    correct = sum(1 for it in bucket_items if (jev_in_scope_p(it) >= 0.5) == it["truth"]["in_scope"])
    print(f"  |p-0.5| in [{lo},{hi}): n={len(bucket_items)}  accuracy={correct}/{len(bucket_items)}={correct/len(bucket_items):.2f}")

print()
print("CALIBRATION -- pillar choice, bucketed by Jev's own stated confidence, all 45 blind items")
conf_buckets = [(0.0, 0.5), (0.5, 0.75), (0.75, 0.9), (0.9, 1.01)]
for lo, hi in conf_buckets:
    bucket_items = [it for it in blind if lo <= it["jev"]["answers"]["pillar"]["confidence"] < hi]
    if not bucket_items:
        print(f"  conf in [{lo},{hi}): n=0")
        continue
    correct = sum(1 for it in bucket_items if it["jev"]["answers"]["pillar"]["choice"] == it["truth"]["pillar"])
    print(f"  conf in [{lo},{hi}): n={len(bucket_items)}  top1_accuracy={correct}/{len(bucket_items)}={correct/len(bucket_items):.2f}")

# ---------- ranker comparison ----------
print()
print("=" * 70)
print("RANKER COMPARISON (single scan, n=45 fresh candidates, real select_top_n=5)")
n_true_in_scope = sum(1 for it in blind if it["truth"]["in_scope"])
print(f"  true in-scope items in this pool: {n_true_in_scope}/45")

by_keyword = sorted(blind, key=lambda it: it["truth"]["rank_score"], reverse=True)
by_jev = sorted(blind, key=lambda it: jev_score(it), reverse=True)

for k in (5, 10):
    kw_hits = sum(1 for it in by_keyword[:k] if it["truth"]["in_scope"])
    jev_hits = sum(1 for it in by_jev[:k] if it["truth"]["in_scope"])
    print(f"  top-{k}: keyword ranker catches {kw_hits}/{n_true_in_scope} true in-scope; "
          f"Jev parent_value score catches {jev_hits}/{n_true_in_scope} true in-scope")

print()
print("  keyword-ranker top 10 (id, rank_score, truth_in_scope, title):")
for it in by_keyword[:10]:
    print(f"    {it['id']} kw={it['truth']['rank_score']:.3f} truth_in_scope={it['truth']['in_scope']!s:5s} {it['truth']['title'][:55]}")
print()
print("  Jev-score top 10 (id, jev_score, jev_conf, truth_in_scope, title):")
for it in by_jev[:10]:
    j = it["jev"]["answers"]["parent_value"]
    print(f"    {it['id']} jev={j['score']:.2f} conf={j['confidence']:.2f} truth_in_scope={it['truth']['in_scope']!s:5s} {it['truth']['title'][:55]}")

# ---------- disagreements ----------
print()
print("=" * 70)
print("IN-SCOPE DISAGREEMENTS (threshold 0.5), blind set")
disagreements = [it for it in blind if (jev_in_scope_p(it) >= 0.5) != it["truth"]["in_scope"]]
print(f"  n disagreements: {len(disagreements)}/45")
for it in disagreements:
    print(f"    {it['id']} truth={it['truth']['in_scope']!s:5s} jev_p={jev_in_scope_p(it):.2f} "
          f"marginal={it['truth']['marginal']} :: {it['truth']['title'][:60]}")
