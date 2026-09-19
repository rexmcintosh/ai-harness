#!/usr/bin/env python3
"""Score both Jev gate designs against the rebuilt clarity-scan truth. Writes results.json."""
from __future__ import annotations

import json
import random
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
THRESHOLDS = [0.05, 0.1, 0.2, 0.3, 0.5]


def spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else float("nan")


def auc(pos, neg):
    """P(score of a positive > score of a negative), ties at 0.5."""
    if not pos or not neg:
        return float("nan")
    wins = 0.0
    for p in pos:
        for n in neg:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(pos) * len(neg))


def main() -> int:
    ds = json.load(open(HERE / "dataset.json"))
    adj = json.load(open(HERE / "flag_adjudication.json"))
    ja = json.load(open(HERE / "jev_a.json"))
    jb = json.load(open(HERE / "jev_b.json"))

    strict = {tuple(k.split("|")) for k in adj["real_chapters_strict"]}
    liberal = {tuple(k.split("|")) for k in adj["real_chapters_liberal"]}

    bwin = defaultdict(list)
    for wid, w in jb.items():
        bwin[w["row_id"]].append(w)
    for v in bwin.values():
        v.sort(key=lambda w: w["i"])

    rows = []
    for d in ds:
        rid = d["row_id"]
        a = ja[rid]["answers"]
        ws = bwin[rid]
        probs = [w["answers"]["breaks"]["noul"] for w in ws]
        key = (d["book"], d["round"], str(d["chapter"]))
        rows.append({
            "row_id": rid, "book": d["book"], "round": d["round"], "chapter": d["chapter"],
            "words": d["words"], "prompt_tokens": d["prompt_tokens"], "cost_usd": d["cost_usd"],
            "n_flags": d["n_flags"],
            "level1": d["n_flags"] > 0,
            "level2_strict": key in strict,
            "level2_liberal": key in liberal,
            "jev_a_noul": a["breaks"]["noul"],
            "jev_a_score": a["clean"]["score"],
            "jev_a_score_conf": a["clean"].get("confidence"),
            "jev_b_max": max(probs), "jev_b_mean": round(sum(probs) / len(probs), 4),
            "jev_b_windows": len(probs),
            "jev_b_window_probs": [round(p, 3) for p in probs],
        })

    out = {"n_rows": len(rows), "rows": rows}
    R = rows

    # ---- distribution -------------------------------------------------------------
    buckets = Counter("0" if r["n_flags"] == 0 else "1" if r["n_flags"] == 1
                      else "2-3" if r["n_flags"] <= 3 else "4+" for r in R)
    out["flag_distribution"] = dict(buckets)
    out["flag_counts"] = dict(sorted(Counter(r["n_flags"] for r in R).items()))
    print("flag buckets:", dict(buckets), "| total flags", sum(r["n_flags"] for r in R))
    print("level1 chapters:", sum(r["level1"] for r in R),
          "| level2 strict:", sum(r["level2_strict"] for r in R),
          "| level2 liberal:", sum(r["level2_liberal"] for r in R))

    # ---- separation ---------------------------------------------------------------
    sep = {}
    for name, key in (("design_a_noul", "jev_a_noul"), ("design_a_score", "jev_a_score"),
                      ("design_b_max", "jev_b_max"), ("design_b_mean", "jev_b_mean")):
        by_bucket = defaultdict(list)
        for r in R:
            b = "0" if r["n_flags"] == 0 else "1" if r["n_flags"] == 1 else "2-3"
            by_bucket[b].append(r[key])
        sep[name] = {
            "mean_by_flag_bucket": {b: round(st.mean(v), 3) for b, v in sorted(by_bucket.items())},
            "spearman_vs_flag_count": round(spearman([r[key] for r in R],
                                                     [r["n_flags"] for r in R]), 3),
            "auc_level1": round(auc([r[key] for r in R if r["level1"]],
                                    [r[key] for r in R if not r["level1"]]), 3),
            "auc_level2_strict": round(auc([r[key] for r in R if r["level2_strict"]],
                                           [r[key] for r in R if not r["level2_strict"]]), 3),
            "range": [round(min(r[key] for r in R), 3), round(max(r[key] for r in R), 3)],
            "median": round(st.median([r[key] for r in R]), 3),
        }
        print(f"{name:16s} means {sep[name]['mean_by_flag_bucket']} "
              f"rho={sep[name]['spearman_vs_flag_count']} "
              f"AUC L1={sep[name]['auc_level1']} L2={sep[name]['auc_level2_strict']} "
              f"median={sep[name]['median']} range={sep[name]['range']}")
    out["separation"] = sep

    # ---- the gate at each threshold -------------------------------------------------
    def gate(rows_, key, t):
        skipped = [r for r in rows_ if r[key] < t]
        return {
            "threshold": t, "n": len(rows_), "skipped": len(skipped),
            "skipped_share": round(len(skipped) / len(rows_), 3),
            "missed_level1": sum(r["level1"] for r in skipped),
            "missed_level1_share_of_level1": round(
                sum(r["level1"] for r in skipped) / max(1, sum(r["level1"] for r in rows_)), 3),
            "missed_level2_strict": sum(r["level2_strict"] for r in skipped),
            "missed_level2_strict_share": round(
                sum(r["level2_strict"] for r in skipped)
                / max(1, sum(r["level2_strict"] for r in rows_)), 3),
            "missed_level2_liberal": sum(r["level2_liberal"] for r in skipped),
            "flags_lost": sum(r["n_flags"] for r in skipped),
            "flags_lost_share": round(sum(r["n_flags"] for r in skipped)
                                      / max(1, sum(r["n_flags"] for r in rows_)), 3),
            "cost_saved_usd": round(sum(r["cost_usd"] for r in skipped), 2),
        }

    out["gate"] = {}
    for name, key in (("design_a_noul", "jev_a_noul"), ("design_b_max", "jev_b_max")):
        out["gate"][name] = [gate(R, key, t) for t in THRESHOLDS]
        print(f"\n--- {name} ---")
        print("  thr  skip-pct  L1miss (of {})  L2miss (of {})  flagslost  $saved".format(
            sum(r["level1"] for r in R), sum(r["level2_strict"] for r in R)))
        for g in out["gate"][name]:
            print("  %.2f  %5.1f%%   %3d (%4.1f%%)     %3d (%5.1f%%)     %3d (%4.1f%%)  $%.2f"
                  % (g["threshold"], 100 * g["skipped_share"], g["missed_level1"],
                     100 * g["missed_level1_share_of_level1"], g["missed_level2_strict"],
                     100 * g["missed_level2_strict_share"], g["flags_lost"],
                     100 * g["flags_lost_share"], g["cost_saved_usd"]))

    # the score as a gate too (skip when the chapter scores CLEANER than s)
    out["gate"]["design_a_score"] = []
    for s in (3.0, 2.75, 2.5, 2.25, 2.0):
        sk = [r for r in R if r["jev_a_score"] >= s]
        out["gate"]["design_a_score"].append({
            "threshold": s, "skipped": len(sk),
            "skipped_share": round(len(sk) / len(R), 3),
            "missed_level1": sum(r["level1"] for r in sk),
            "missed_level2_strict": sum(r["level2_strict"] for r in sk),
            "flags_lost": sum(r["n_flags"] for r in sk)})

    # ---- per book -------------------------------------------------------------------
    out["per_book"] = {}
    print("\n--- per book (design B max) ---")
    for b in sorted({r["book"] for r in R}):
        br = [r for r in R if r["book"] == b]
        out["per_book"][b] = {
            "n": len(br), "level1": sum(r["level1"] for r in br),
            "level2_strict": sum(r["level2_strict"] for r in br),
            "flags": sum(r["n_flags"] for r in br),
            "mean_jev_a_noul": round(st.mean(r["jev_a_noul"] for r in br), 3),
            "mean_jev_b_max": round(st.mean(r["jev_b_max"] for r in br), 3),
            "min_jev_b_max": round(min(r["jev_b_max"] for r in br), 3),
            "auc_level1_b": round(auc([r["jev_b_max"] for r in br if r["level1"]],
                                      [r["jev_b_max"] for r in br if not r["level1"]]), 3),
            "gate_0.2": gate(br, "jev_b_max", 0.2),
        }
        v = out["per_book"][b]
        print("  %-15s n=%3d L1=%3d L2=%2d  meanA=%.2f meanBmax=%.2f minBmax=%.2f AUC_L1=%.2f "
              "| @0.2 skip %d, L1 miss %d, L2 miss %d"
              % (b, v["n"], v["level1"], v["level2_strict"], v["mean_jev_a_noul"],
                 v["mean_jev_b_max"], v["min_jev_b_max"], v["auc_level1_b"],
                 v["gate_0.2"]["skipped"], v["gate_0.2"]["missed_level1"],
                 v["gate_0.2"]["missed_level2_strict"]))

    # ---- split-half operating threshold ---------------------------------------------
    random.seed(20260919)
    idx = list(range(len(R)))
    random.shuffle(idx)
    dev = [R[i] for i in idx[: len(R) // 2]]
    hold = [R[i] for i in idx[len(R) // 2:]]
    split = {}
    for name, key in (("design_a_noul", "jev_a_noul"), ("design_b_max", "jev_b_max")):
        # the largest threshold on the dev half that misses no real defect (level 2 strict)
        cands = sorted({round(r[key], 4) for r in dev})
        chosen, best = 0.0, None
        for t in cands:
            g = gate(dev, key, t)
            if g["missed_level2_strict"] == 0:
                chosen, best = t, g
        split[name] = {"chosen_threshold": chosen, "dev": best,
                       "holdout": gate(hold, key, chosen) if chosen else None}
        print(f"\nsplit-half {name}: largest dev threshold with 0 real-defect misses = {chosen}")
        if best:
            print("   dev    ", {k: best[k] for k in
                                 ("skipped_share", "missed_level1", "missed_level2_strict")})
            h = split[name]["holdout"]
            print("   holdout", {k: h[k] for k in
                                 ("skipped_share", "missed_level1", "missed_level2_strict",
                                  "flags_lost")})
    out["split_half"] = split

    # ---- design B localisation --------------------------------------------------------
    loc = {"flags_with_locatable_quote": 0, "top1": 0, "top2": 0, "ranks": [],
           "percentiles": [], "single_window_chapters": 0}
    ds_by = {d["row_id"]: d for d in ds}
    for r in R:
        d = ds_by[r["row_id"]]
        ws = bwin[r["row_id"]]
        if len(ws) < 2:
            loc["single_window_chapters"] += 1
            continue
        order = sorted(range(len(ws)), key=lambda i: -ws[i]["answers"]["breaks"]["noul"])
        rank_of = {w: k + 1 for k, w in enumerate(order)}
        for f in d["flags"]:
            q = str(f.get("quote", "")).strip()
            if not q:
                continue
            pos = d["text"].find(q[:70])
            if pos < 0:
                continue
            hit = [i for i, w in enumerate(ws) if w["start"] <= pos <= w["end"]]
            if not hit:
                continue
            k = rank_of[hit[0]]
            loc["flags_with_locatable_quote"] += 1
            loc["ranks"].append(k)
            loc["percentiles"].append(1 - (k - 1) / (len(ws) - 1))
            loc["top1"] += k == 1
            loc["top2"] += k <= 2
    n = max(1, loc["flags_with_locatable_quote"])
    loc["top1_share"] = round(loc["top1"] / n, 3)
    loc["top2_share"] = round(loc["top2"] / n, 3)
    loc["mean_rank"] = round(st.mean(loc["ranks"]), 2) if loc["ranks"] else None
    loc["mean_percentile"] = round(st.mean(loc["percentiles"]), 3) if loc["percentiles"] else None
    loc["mean_windows_per_chapter"] = round(st.mean(len(bwin[r["row_id"]]) for r in R), 2)
    loc["chance_top1"] = round(st.mean(1 / len(bwin[r["row_id"]]) for r in R), 3)
    loc.pop("ranks"), loc.pop("percentiles")
    out["localisation_design_b"] = loc
    print("\nlocalisation (design B):", json.dumps(loc))

    # ---- money -------------------------------------------------------------------------
    ledger_total = 46.2801
    out["money"] = {
        "ledger_total_usd": ledger_total,
        "eval_rows_cost_usd": round(sum(r["cost_usd"] for r in R), 2),
        "jev_cost_this_experiment_usd": 0.1589,
    }
    (HERE / "results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\nwrote results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
