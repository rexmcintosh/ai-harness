#!/usr/bin/env python3
"""Operator step for backlog item 2026-08-25-loom-triage-two-malformed-bm2-artifacts.

Run this from the MAIN checkout (~/projects/build-ai-automation-workflow), not a
worktree: loom/cli.py hardcodes _REPO to that path, so loom's runtime state
(loom/state.json, loom/learnings/, loom/quarantine/) only ever lives there. It
is gitignored (loom/.gitignore: state.json, learnings/, quarantine/), so none
of this can be done as a branch commit -- it has to be applied directly and is
not captured by the PR/branch review.

What it does, idempotently:
  1. Writes the two salvage artifacts (uncovered facts from the malformed bm2-*
     quarantine files, not present in either original session's own learnings)
     into loom/learnings/ under new ids, and marks those ids 'distilled' so the
     next backfill/absorb weaves them.
  2. Marks the two bm2-* session ids 'committed' in loom/state.json (their
     quarantine is resolved: fully superseded either by the original's
     committed learnings, or by the new salvage artifact above).
  3. Deletes the two malformed bm2-* files from loom/quarantine/.

Safe to re-run: each step checks current state first.

Does NOT touch the original session c2b2fe92-aca1-4140-9f0f-105efcbec759's own
quarantine entry (loom/quarantine/c2b2fe92-....md) -- that predates this backlog
item, is one of the pre-existing quarantine-review-queue entries described in
docs/loom-quarantine-disposition.md, and triaging it is out of scope here.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO = Path.home() / "projects" / "build-ai-automation-workflow"
LOOM = REPO / "loom"
STATE_PATH = LOOM / "state.json"
LEARNINGS_DIR = LOOM / "learnings"
QUARANTINE_DIR = LOOM / "quarantine"
HERE = Path(__file__).parent

SALVAGE = [
    {
        "bm2_id": "bm2-363556c5-bf8f-4ada-95ee-79e435d75d34",
        "new_id": "bm2-363556c5-bf8f-4ada-95ee-79e435d75d34-salvage",
        "source": HERE / "salvage-bm2-363556c5-bf8f-4ada-95ee-79e435d75d34.md",
    },
    {
        "bm2_id": "bm2-c2b2fe92-aca1-4140-9f0f-105efcbec759",
        "new_id": "bm2-c2b2fe92-aca1-4140-9f0f-105efcbec759-salvage",
        "source": HERE / "salvage-bm2-c2b2fe92-aca1-4140-9f0f-105efcbec759.md",
    },
]


def load_state() -> dict:
    return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}


def save_state(data: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(STATE_PATH)


def main() -> int:
    if REPO.name == "worktrees" or ".claude" in REPO.parts:
        print("refusing to run: resolve to the main checkout, not a worktree", file=sys.stderr)
        return 1

    state = load_state()
    LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)

    for item in SALVAGE:
        new_artifact = LEARNINGS_DIR / f"{item['new_id']}.md"
        if not new_artifact.exists():
            new_artifact.write_text(item["source"].read_text(), encoding="utf-8")
            print(f"wrote {new_artifact}")
        else:
            print(f"skip (exists) {new_artifact}")

        if state.get(item["new_id"], {}).get("state") != "distilled":
            state.setdefault(item["new_id"], {})["state"] = "distilled"
            print(f"state[{item['new_id']}] -> distilled")

        if state.get(item["bm2_id"], {}).get("state") != "committed":
            state.setdefault(item["bm2_id"], {})["state"] = "committed"
            print(f"state[{item['bm2_id']}] -> committed (quarantine settled)")

        stale = QUARANTINE_DIR / f"{item['bm2_id']}.md"
        if stale.exists():
            stale.unlink()
            print(f"deleted {stale}")
        else:
            print(f"skip (already gone) {stale}")

    save_state(state)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
