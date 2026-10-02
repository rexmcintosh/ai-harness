"""Read-only collection pass: fetch public RSS feeds listed in sources.yaml and run
them through the editorial package's pure fetch/normalize/rank functions ONLY.
Does NOT import or call agents.py (run_editorial/run_reframe/run_critique) — no
Opus/Venice calls are made, no cost is incurred, and nothing in the swimtrack repo
is written to. This mirrors editorial.pipeline.scan()'s collection half, stopping
before the LLM stage, so we get real title+excerpt+source text for candidates that
would (or would not) have reached Opus under the real select_top_n=5 ranker.
"""
from __future__ import annotations
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/dev/projects/swimtrack/editorial/src")

from editorial.config import load_config
from editorial.fetch import fetch_rss, fetch_url
from editorial.normalize import normalize_item
from editorial.rank import rank_candidates

SOURCES_YAML = "/home/dev/projects/swimtrack/editorial/sources.yaml"
OUT = "/tmp/claude-1000/-home-dev-projects/3353706c-f59c-4c56-a420-e17dc8b669a6/scratchpad/jevlab/swim/collected_candidates.json"

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
cfg = load_config(SOURCES_YAML)

by_hash = {}
report = []
for src in cfg.sources:
    entry = {"source": src.name, "type": src.type, "fetched": 0, "error": None}
    if src.type != "rss":
        entry["error"] = f"unsupported type '{src.type}' (skipped)"
        report.append(entry)
        continue
    try:
        raws = fetch_rss(fetch_url(src.url))
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {str(e)[:150]}"
        report.append(entry)
        continue
    entry["fetched"] = len(raws)
    for raw in raws:
        try:
            c = normalize_item(raw, source=src.name, source_type=src.type,
                                market=src.market, now=now)
        except Exception:
            continue
        if not c.url:
            continue
        c.source_trust = src.trust_weight
        c.source_pillars = src.pillars
        existing = by_hash.get(c.content_hash)
        if existing is None or c.source_trust > existing.source_trust:
            by_hash[c.content_hash] = c
    report.append(entry)

fresh = list(by_hash.values())
ranked = rank_candidates(fresh, now=now, top_n=cfg.select_top_n)   # real select_top_n=5
ranked_ids_top5 = {c.id for c in ranked}

all_scored = sorted(fresh, key=lambda x: x.rank_score, reverse=True)

out = {
    "generated_at": now,
    "select_top_n": cfg.select_top_n,
    "source_report": report,
    "total_fresh": len(fresh),
    "candidates": [c.to_dict() | {"would_reach_opus_top5": c.id in ranked_ids_top5} for c in all_scored],
}
with open(OUT, "w") as f:
    json.dump(out, f, indent=2)
print(f"fresh={len(fresh)} top5_ids={[c.id for c in ranked]}")
print(f"wrote {OUT}")
