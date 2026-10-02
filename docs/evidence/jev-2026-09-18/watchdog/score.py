"""Score today's regex and Jev against the blind labels."""
import collections
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
rows = {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(here, "windows.jsonl"))}
jev = {r["id"]: r for r in json.load(open(os.path.join(here, "jev_results.json")))}
labels = {}
for n in (1, 2):
    for r in json.load(open(os.path.join(here, f"labels_{n}.json"))):
        labels[r["id"]] = r
over = os.path.join(here, "label_overrides.json")     # hand-checked corrections, each with a reason
if os.path.exists(over):
    for r in json.load(open(over)):
        labels[r["id"]].update(r)
ids = [i for i in rows if i in labels and i in jev]
print(f"windows={len(ids)}  label true={sum(labels[i]['needs_human'] for i in ids)}  false={sum(not labels[i]['needs_human'] for i in ids)}")


def table(name, pred):
    tp = sum(pred[i] and labels[i]["needs_human"] for i in ids)
    fp = sum(pred[i] and not labels[i]["needs_human"] for i in ids)
    fn = sum((not pred[i]) and labels[i]["needs_human"] for i in ids)
    tn = sum((not pred[i]) and not labels[i]["needs_human"] for i in ids)
    acc = (tp + tn) / len(ids)
    print(f"{name:22s} right={tp + tn:3d}/{len(ids)} ({acc:.0%})  missed failures={fn:2d}  false alarms={fp:2d}  caught={tp:2d}  quiet-correct={tn:2d}")


table("regex today", {i: rows[i]["regex_fires"] for i in ids})
for th in (0.3, 0.5, 0.7, 0.9):
    table(f"jev >= {th}", {i: jev[i]["needs_human"] >= th for i in ids})

print("\nper source (regex right / jev@0.5 right / n):")
by = collections.defaultdict(list)
for i in ids:
    by[rows[i]["source"]].append(i)
for s, lst in sorted(by.items()):
    rr = sum(rows[i]["regex_fires"] == labels[i]["needs_human"] for i in lst)
    jj = sum((jev[i]["needs_human"] >= 0.5) == labels[i]["needs_human"] for i in lst)
    print(f"  {s:26s} {rr:2d} / {jj:2d} / {len(lst):2d}")

print("\ncalibration of jev needs_human:")
for lo, hi in ((0, .1), (.1, .3), (.3, .7), (.7, .9), (.9, 1.01)):
    b = [i for i in ids if lo <= jev[i]["needs_human"] < hi]
    if b:
        print(f"  {lo:.1f}-{min(hi, 1):.1f}: n={len(b):3d}  actually failures={sum(labels[i]['needs_human'] for i in b):3d}")

if "-v" in sys.argv:
    print("\nJEV DISAGREES WITH LABEL (at 0.5):")
    for i in ids:
        if (jev[i]["needs_human"] >= 0.5) != labels[i]["needs_human"]:
            print(f"  {i:30s} label={labels[i]['needs_human']!s:5s} ({labels[i]['confidence']}) jev={jev[i]['needs_human']:.2f} regex={rows[i]['regex_fires']!s:5s} | {labels[i]['reason']}")

lat = [i for i in ids if labels[i].get("latest_run_failed") is not None]
ok = sum((jev[i]["latest_run_failed"] >= 0.5) == labels[i]["latest_run_failed"] for i in lat)
print(f"\nlatest_run_failed: jev right {ok}/{len(lat)}")
