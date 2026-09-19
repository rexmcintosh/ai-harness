"""Run the offline Jev-on-council experiments over saved reviews.

    python3 -m tools.jev_council.run --list                      # what is in scope; no network
    python3 -m tools.jev_council.run verdict --dry-run           # request count only
    python3 -m tools.jev_council.run verdict --out DIR           # calls TypeSafe, writes DIR/verdict.json

Reads saved reviews from the backlog runner's state and the backlog repo's evidence folders.
Writes raw answers OUTSIDE this repo (they quote private review text). Changes nothing in
the council, the gate or the backlog.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import experiments as ex
from . import jev
from .reviews import Review, backlog_repo_map, load_reviews, unique_reviews

RUNNER_REVIEWS = Path("~/projects/.backlog-run/reviews")
EVIDENCE = {   # folder -> (repo of each named review, repo of every other review in it)
    "~/projects/backlog/reports/evidence/2026-09-18-ai-harness-session":
        ({"swimtrack": "swimtrack", "swimweb": "swimtrack-website"}, "ai-harness"),
    "~/projects/backlog/reports/evidence/2026-09-19-council-empty-seat": ({}, "ai-harness"),
}
BACKLOG = ("~/projects/backlog/backlog.yaml", "~/projects/backlog/archive.yaml")


def dataset() -> list[Review]:
    reviews = load_reviews(RUNNER_REVIEWS, repo_of=backlog_repo_map(*BACKLOG))
    for folder, (named, default) in EVIDENCE.items():
        reviews += load_reviews(Path(folder), repo_of=named, default_repo=default)
    return unique_reviews(reviews)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("experiment", nargs="?", choices=("verdict", "finding_type", "duplicates"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="first N reviews only")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    reviews = dataset()
    if args.limit:
        reviews = reviews[: args.limit]
    if args.list or not args.experiment:
        for n, r in enumerate(reviews):
            print(f"{n:2} {r.repo:24} {r.rid[-44:]:44} findings={len(r.findings):2} "
                  f"pairs={len(ex.cross_seat_pairs(r)):3} blocks={len(r.chair_blocks)}")
        print(f"{len(reviews)} reviews in scope, {sum(len(r.findings) for r in reviews)} findings")
        return 0
    if args.dry_run:
        print(json.dumps(ex.run(args.experiment, reviews, key=None, dry_run=True)))
        return 0
    # Every refusal comes BEFORE the first call: nothing is sent for a result we cannot keep.
    if not args.out:
        print("--out DIR is required for a real run (raw answers quote private text)", file=sys.stderr)
        return 2
    key = jev.load_key()
    if not key:
        print("TYPESAFE_API_KEY is not set", file=sys.stderr)
        return 2
    result = ex.run(args.experiment, reviews, key=key, log=print)
    args.out.expanduser().mkdir(parents=True, exist_ok=True)
    path = args.out.expanduser() / f"{args.experiment}.json"
    path.write_text(json.dumps(result, indent=1))
    print(f"{args.experiment}: {len(result['rows'])} rows, {result['errors']} errors, "
          f"{result['input_tokens']} input tokens -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
