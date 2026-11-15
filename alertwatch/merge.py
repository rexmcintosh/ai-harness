"""The only way an alert-watch run merges or reverts: trusted code, not the model.

    python3 -m alertwatch.merge merge <repo> <worktree>   fast-forward main to the fix, push
    python3 -m alertwatch.merge revert <repo> <to_sha>    revert an alert-watch merge, push

Every rule is checked here, at the moment of the merge: act mode, a listed
repository, a fix worktree under <state>/worktrees/ on an `alertwatch/` branch,
both trees clean, the main checkout on `main` and equal to `origin/main`, a fast
forward, no denied path, and the daily merge cap from the actions ledger. The
push is a plain push (never forced) and happens before the main checkout moves.
Exit 0 done, 2 refused, 1 git failed. The run's deny rules block direct pushes,
merges and reverts, so this helper is the path for all three.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from alertwatch import ledger
from alertwatch.config import Config, load_config


class Refused(Exception):
    pass


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, ["git", *args], proc.stdout, proc.stderr)
    return proc.stdout.strip()


def denied(paths: list[str], rules: tuple[str, ...]) -> list[str]:
    """Paths a merge may not touch: an entry ending in "/" is a path prefix, any
    other entry matches anywhere in the path."""
    out = []
    for path in paths:
        for rule in rules:
            if (path.startswith(rule) if rule.endswith("/") else rule in path):
                out.append(path)
                break
    return out


def _check_main(repo: Path) -> str:
    """The main checkout is on main, clean (tracked files) and equal to origin/main."""
    _git(repo, "fetch", "-q", "origin")
    if _git(repo, "rev-parse", "--abbrev-ref", "HEAD") != "main":
        raise Refused(f"{repo} is not on main")
    if _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise Refused(f"{repo} has uncommitted changes")
    local, remote = _git(repo, "rev-parse", "main"), _git(repo, "rev-parse", "origin/main")
    if local != remote:
        raise Refused(f"{repo} main {local[:10]} is not origin/main {remote[:10]}")
    return local


def _repo(cfg: Config, name: str) -> Path:
    if cfg.mode != "act":
        raise Refused(f"mode is {cfg.mode}; merges need mode = act")
    if name not in cfg.repos:
        raise Refused(f"{name} is not a repository alert-watch may change ({', '.join(cfg.repos)})")
    return cfg.projects_dir / name


def do_merge(cfg: Config, name: str, worktree: str, now: float) -> dict:
    repo = _repo(cfg, name)
    wt = Path(worktree).resolve()
    root = cfg.worktrees_dir.resolve()
    if root not in wt.parents:
        raise Refused(f"{wt} is not under {root}")
    common = Path(_git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if common != Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve():
        raise Refused(f"{wt} is not a worktree of {repo}")
    branch = _git(wt, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch.startswith("alertwatch/"):
        raise Refused(f"branch {branch!r} is not an alertwatch/ branch")
    if _git(wt, "status", "--porcelain"):
        raise Refused(f"{wt} has uncommitted changes")
    base = _check_main(repo)
    head = _git(wt, "rev-parse", "HEAD")
    if head == base:
        raise Refused("the branch has nothing to merge")
    try:
        _git(repo, "merge-base", "--is-ancestor", base, head)
    except subprocess.CalledProcessError:
        raise Refused("the branch is not a fast forward of origin/main; rebase it first") from None
    files = [f for f in _git(repo, "diff", "--name-only", f"{base}...{head}").splitlines() if f]
    bad = denied(files, cfg.denied_paths)
    if bad:
        raise Refused(f"the diff touches denied paths: {', '.join(bad)}")
    made = ledger.merges_in_window(cfg.state_dir, now)
    if made >= cfg.max_merges_day:
        raise Refused(f"{made} merges in the last 24 hours (limit {cfg.max_merges_day})")
    _git(repo, "push", "-q", "origin", f"{head}:refs/heads/main")
    _git(repo, "merge", "-q", "--ff-only", head)
    if _check_main(repo) != head:
        raise subprocess.CalledProcessError(1, ["check"], "", "main did not land on the fix")
    action = {"kind": "merge", "at": now, "run_id": os.environ.get("ALERT_WATCH_RUN_ID", ""),
              "repo": name, "branch": branch, "from_sha": base, "to_sha": head, "files": files}
    ledger.append_action(cfg.state_dir, action)
    return {"status": "merged", **action}


def do_revert(cfg: Config, name: str, sha: str, now: float) -> dict:
    repo = _repo(cfg, name)
    acts = ledger.actions(cfg.state_dir)
    if len(sha) < 7:
        raise Refused("give at least 7 characters of the merged commit")
    merged = [a for a in acts if a.get("kind") == "merge" and a.get("repo") == name
              and str(a.get("to_sha", "")).startswith(sha)]
    if len(merged) != 1:
        raise Refused(f"{sha} is not one alert-watch merge in {name}")
    target = merged[0]
    if any(a.get("kind") == "revert" and a.get("reverts") == target["to_sha"] for a in acts):
        raise Refused(f"{target['to_sha'][:10]} was already reverted")
    before = _check_main(repo)
    try:
        _git(repo, "revert", "--no-edit", f"{target['from_sha']}..{target['to_sha']}")
    except subprocess.CalledProcessError:
        subprocess.run(["git", "revert", "--abort"], cwd=str(repo), capture_output=True)
        raise Refused("the revert does not apply cleanly; it needs Rex") from None
    after = _git(repo, "rev-parse", "HEAD")
    try:
        _git(repo, "push", "-q", "origin", "main")
    except subprocess.CalledProcessError:
        _git(repo, "reset", "-q", "--keep", before)
        raise
    action = {"kind": "revert", "at": now, "run_id": os.environ.get("ALERT_WATCH_RUN_ID", ""),
              "repo": name, "reverts": target["to_sha"], "from_sha": before, "to_sha": after}
    ledger.append_action(cfg.state_dir, action)
    return {"status": "reverted", **action}


def main(argv: list[str] | None = None, *, cfg: Config | None = None,
         now: Callable[[], float] = time.time) -> int:
    ap = argparse.ArgumentParser(prog="alertwatch.merge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge")
    m.add_argument("repo")
    m.add_argument("worktree")
    r = sub.add_parser("revert")
    r.add_argument("repo")
    r.add_argument("sha")
    args = ap.parse_args(argv)
    cfg = cfg or load_config()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.state_dir / "merge.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if args.cmd == "merge":
                out = do_merge(cfg, args.repo, args.worktree, now())
            else:
                out = do_revert(cfg, args.repo, args.sha, now())
        except Refused as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
        except subprocess.CalledProcessError as e:
            print(f"git failed: {' '.join(e.cmd)}: {(e.stderr or '').strip()[:500]}", file=sys.stderr)
            return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
