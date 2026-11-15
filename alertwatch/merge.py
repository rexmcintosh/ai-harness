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
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from alertwatch import ledger
from alertwatch.config import Config, load_config


class Refused(Exception):
    pass


class PushedNotLocal(Exception):
    """origin/main has the change, but the main checkout did not move."""


def _intent_id() -> str:
    return f"{os.environ.get('ALERT_WATCH_RUN_ID') or 'manual'}-{secrets.token_hex(4)}"


def _on_origin(repo: Path, sha: str) -> bool:
    try:
        _git(repo, "fetch", "-q", "origin")
        _git(repo, "merge-base", "--is-ancestor", sha, "origin/main")
        return True
    except subprocess.CalledProcessError:
        return False


def _push(repo: Path, refspec: str, sha: str) -> None:
    """Push without force. A failed push that still landed (a lost reply) counts as done."""
    try:
        _git(repo, "push", "-q", "origin", refspec)
    except subprocess.CalledProcessError:
        if not _on_origin(repo, sha):
            raise


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


def _reconcile(cfg: Config, repo: Path, name: str, now: float) -> None:
    """Settle intents a crash left without an outcome. A merge or revert that never
    reached origin is abandoned (a revert's local commit is undone). One that reached
    origin keeps counting: callers already treat it as done."""
    acts = ledger.actions(cfg.state_dir)
    settled = {str(a.get("intent")) for a in acts if a.get("intent")}
    for a in acts:
        if a.get("repo") != name or a.get("id") in settled:
            continue
        if a.get("kind") == "merge_intent" and not _on_origin(repo, a["to_sha"]):
            ledger.append_action(cfg.state_dir, {"kind": "merge_abandoned", "intent": a["id"], "at": now,
                                                 "why": "never reached origin"})
        elif a.get("kind") == "revert_intent" and not _on_origin(repo, a["to_sha"]):
            if _git(repo, "rev-parse", "HEAD") == a["to_sha"] and \
                    _git(repo, "rev-parse", "origin/main") == a["from_sha"]:
                _git(repo, "reset", "-q", "--keep", a["from_sha"])
            ledger.append_action(cfg.state_dir, {"kind": "revert_abandoned", "intent": a["id"], "at": now,
                                                 "why": "never reached origin"})


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
    untracked = {line[3:] for line in _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
                 if line.startswith("?? ")}
    blocking = sorted(set(files) & untracked)
    if blocking:  # they would stop the local fast forward after the push
        raise Refused(f"untracked files in {repo} are in the way: {', '.join(blocking)}")
    bad = denied(files, cfg.denied_paths)
    if bad:
        raise Refused(f"the diff touches denied paths: {', '.join(bad)}")
    _reconcile(cfg, repo, name, now)
    made = ledger.merges_in_window(cfg.state_dir, now)
    if made >= cfg.max_merges_day:
        raise Refused(f"{made} merges in the last 24 hours (limit {cfg.max_merges_day})")
    # Journal first: the intent is on disk before origin can change, so a crash at
    # any later point still counts toward the cap and stays revertable.
    run_id = os.environ.get("ALERT_WATCH_RUN_ID", "")
    intent = {"kind": "merge_intent", "id": _intent_id(), "at": now, "run_id": run_id, "repo": name,
              "branch": branch, "from_sha": base, "to_sha": head, "files": files}
    ledger.append_action(cfg.state_dir, intent)
    try:
        _push(repo, f"{head}:refs/heads/main", head)
    except subprocess.CalledProcessError:
        ledger.append_action(cfg.state_dir, {"kind": "merge_abandoned", "intent": intent["id"], "at": now})
        raise
    action = {**intent, "kind": "merge", "intent": intent["id"]}
    ledger.append_action(cfg.state_dir, action)
    try:
        _git(repo, "merge", "-q", "--ff-only", head)
        landed = _check_main(repo) == head
    except (subprocess.CalledProcessError, Refused):
        landed = False
    if not landed:
        raise PushedNotLocal(f"{head[:10]} is on origin/main, but {repo} did not move to it")
    return {"status": "merged", **action}


def do_revert(cfg: Config, name: str, sha: str, now: float) -> dict:
    repo = _repo(cfg, name)
    if len(sha) < 7:
        raise Refused("give at least 7 characters of the merged commit")
    candidates = [m for m in ledger.merged(cfg.state_dir) if m.get("repo") == name
                  and str(m.get("to_sha", "")).startswith(sha)]
    # An intent with no outcome (a crash after its push) counts only if origin has it.
    candidates = [m for m in candidates if m["kind"] == "merge" or _on_origin(repo, m["to_sha"])]
    if len(candidates) != 1:
        raise Refused(f"{sha} is not one alert-watch merge in {name}")
    target = candidates[0]
    _reconcile(cfg, repo, name, now)
    if target["to_sha"] in ledger.reverted(cfg.state_dir):
        raise Refused(f"{target['to_sha'][:10]} was already reverted")
    before = _check_main(repo)
    try:
        _git(repo, "revert", "--no-edit", f"{target['from_sha']}..{target['to_sha']}")
    except subprocess.CalledProcessError:
        subprocess.run(["git", "revert", "--abort"], cwd=str(repo), capture_output=True)
        raise Refused("the revert does not apply cleanly; it needs Rex") from None
    after = _git(repo, "rev-parse", "HEAD")
    run_id = os.environ.get("ALERT_WATCH_RUN_ID", "")
    intent = {"kind": "revert_intent", "id": _intent_id(), "at": now, "run_id": run_id, "repo": name,
              "reverts": target["to_sha"], "from_sha": before, "to_sha": after}
    ledger.append_action(cfg.state_dir, intent)
    try:
        _push(repo, "main", after)
    except subprocess.CalledProcessError:
        _git(repo, "reset", "-q", "--keep", before)
        ledger.append_action(cfg.state_dir, {"kind": "revert_abandoned", "intent": intent["id"], "at": now})
        raise
    action = {**intent, "kind": "revert", "intent": intent["id"]}
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
        except PushedNotLocal as e:
            print(f"pushed, needs Rex: {e}", file=sys.stderr)
            return 1
        except subprocess.CalledProcessError as e:
            print(f"git failed: {' '.join(e.cmd)}: {(e.stderr or '').strip()[:500]}", file=sys.stderr)
            return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
