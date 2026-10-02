# diem/discover.py
"""Checkpoint-time discovery: hygiene (unreviewed commits / dirty trees) and
publishing feedstock (standing-order stock shortfalls). Discovery only ADDS
queue items; QueueDir.add's dedupe makes it idempotent within a night."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

from .queue import Item, QueueDir, new_item
from .state import Reviewed


def _git(repo: Path, *args, run) -> str | None:
    try:
        p = run(["git", "-C", str(repo), *args],
                capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001 — a broken repo must never kill the drain
        return None
    if p.returncode != 0:
        return None
    return p.stdout.strip()


def discover(cfg, queue: QueueDir, reviewed: Reviewed, now_iso: str,
             *, day_start_iso: str | None = None,
             run=subprocess.run) -> list[Item]:
    added: list[Item] = []
    done_tonight = (queue.archived_keys_since(day_start_iso)
                    if day_start_iso else set())

    def _add(item: Item):
        if item.dedupe_key() in done_tonight:
            return  # already ran (or failed out) tonight — once per night
        if queue.add(item):
            added.append(item)

    for repo in cfg.repos:
        repo = Path(repo)
        repo_key = str(repo)
        head = _git(repo, "rev-parse", "HEAD", run=run)
        if head:
            old = reviewed.get(repo_key)
            status = _git(repo, "status", "--porcelain", run=run)
            if old is None:
                reviewed.set(repo_key, head)  # baseline, nothing to review yet
                moved = False
            else:
                moved = old != head
            # at most one review per repo per night: a fresh range key on a
            # later HEAD move wouldn't be caught by archived_keys_since, so
            # also check by prefix (any outcome tonight) and by pending items
            has_review_tonight = (
                any(k.startswith(f"review:{repo_key}:") for k in done_tonight)
                or any(it.type == "review" and it.payload.get("repo") == repo_key
                       for it in queue.pending(now_iso))
            )
            if not has_review_tonight:
                if moved:
                    _add(new_item("review", {"repo": repo_key,
                                             "range": f"{old}..{head}",
                                             "head": head}, created=now_iso))
                elif status:  # HEAD move (committed work) takes priority over
                              # a still-dirty tree, which will be caught later
                    _add(new_item("review", {"repo": repo_key, "diff": True},
                                  created=now_iso))

        so_path = repo / ".diem" / "standing-order.json"
        try:
            if so_path.exists():
                so = json.loads(so_path.read_text())
                target = int(so["target"])
                cand_dir = so["candidates_dir"]
                if Path(cand_dir).is_absolute():
                    raise ValueError("candidates_dir must be relative to the repo")
                # Validate the command here too — even though it is never
                # embedded in the payload (the runner re-reads the standing
                # order at run time) — so a doomed item never enqueues.
                if not (isinstance(so["command"], list) and so["command"]):
                    raise ValueError("command must be a non-empty list")
                cand = repo / cand_dir
                stock = (sum(1 for f in cand.glob("*") if f.is_file())
                         if cand.is_dir() else 0)
                if stock < target:
                    _add(new_item("images", {"repo": repo_key,
                                             "count": target - stock}, created=now_iso))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue  # malformed/unreadable standing order: skip, never invent
    return added
