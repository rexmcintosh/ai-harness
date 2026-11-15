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


class PushUnknown(Exception):
    """The push failed and origin could not be read: it may or may not have landed."""


def _origin_has(repo: Path, sha: str) -> bool | None:
    """True or False when origin was read; None when it could not be (a network or
    fetch failure). Only a False may ever settle an intent as abandoned."""
    try:
        _git(repo, "fetch", "-q", "origin")
    except subprocess.CalledProcessError:
        return None
    proc = subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], cwd=str(repo),
                          capture_output=True, text=True, timeout=300)
    if proc.returncode in (0, 1):
        return proc.returncode == 0
    # After a good fetch, a commit this repo does not even have is not on origin/main.
    known = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=str(repo),
                           capture_output=True, timeout=60)
    return False if known.returncode != 0 else None


def _push(repo: Path, refspec: str, sha: str) -> None:
    """Push without force. A failed push that still landed (a lost reply) counts as
    done; one whose outcome cannot be read raises PushUnknown."""
    try:
        _git(repo, "push", "-q", "origin", refspec)
    except subprocess.CalledProcessError:
        landed = _origin_has(repo, sha)
        if landed is None:
            raise PushUnknown(f"the push of {sha[:10]} failed and origin cannot be read") from None
        if not landed:
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


def reconcile_all(cfg: Config, now: float) -> None:
    """Settle intents a crash left without an outcome, in every listed repository
    (the merge cap is shared). One that never reached origin is abandoned; one that
    did keeps counting, since callers already treat it as done. Also removes revert
    worktrees a killed helper left behind. Runs before any other check, so a crash
    can never block its own repair."""
    acts = ledger.actions(cfg.state_dir)
    settled = {str(a.get("intent")) for a in acts if a.get("intent")}
    for name in cfg.repos:
        repo = cfg.projects_dir / name
        if not (repo / ".git").exists():
            continue
        for a in acts:
            if a.get("repo") != name or a.get("id") in settled:
                continue
            if a.get("kind") in ("merge_intent", "revert_intent") and _origin_has(repo, a["to_sha"]) is False:
                kind = "merge_abandoned" if a["kind"] == "merge_intent" else "revert_abandoned"
                ledger.append_action(cfg.state_dir, {"kind": kind, "intent": a["id"], "at": now,
                                                     "why": "never reached origin"})
        for wt in cfg.worktrees_dir.glob("revert-*"):
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=str(repo),
                           capture_output=True)
        subprocess.run(["git", "worktree", "prune"], cwd=str(repo), capture_output=True)


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
    # --no-renames: a moved file shows as its old path and its new path, so a file
    # moved out of a denied folder is still caught. -z: any file name, unquoted.
    proc = subprocess.run(["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}"],
                          cwd=str(repo), capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, ["git", "diff"], proc.stdout, proc.stderr)
    files = [f for f in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if f]
    # Every commit that will be pushed, not only the net diff: a denied file changed in
    # one commit and restored in a later one is still published by the push. Merge
    # commits are compared with each parent, so a change made in a merge resolution
    # shows too.
    log = subprocess.run(["git", "log", "--diff-merges=separate", "--pretty=format:", "--name-only",
                          "--no-renames", "-z", f"{base}..{head}"], cwd=str(repo), capture_output=True,
                         timeout=300)
    if log.returncode != 0:
        raise subprocess.CalledProcessError(log.returncode, ["git", "log"], log.stdout, log.stderr)
    touched = {f.strip("\n") for f in log.stdout.decode("utf-8", "surrogateescape").split("\0")}
    touched = {f for f in touched if f}
    status = subprocess.run(["git", "status", "--porcelain", "-z", "--untracked-files=all"], cwd=str(repo),
                            capture_output=True, timeout=300)
    untracked = {e[3:] for e in status.stdout.decode("utf-8", "surrogateescape").split("\0")
                 if e.startswith("?? ")}
    blocking = sorted({u for u in untracked for f in files
                       if u == f or f.startswith(u.rstrip("/") + "/") or u.startswith(f + "/")})
    if blocking:  # they would stop the local fast forward after the push
        raise Refused(f"untracked files in {repo} are in the way: {', '.join(blocking)}")
    bad = denied(sorted(set(files) | touched), cfg.denied_paths)
    if bad:
        raise Refused(f"the diff touches denied paths: {', '.join(bad)}")
    if _git(repo, "rev-list", "--merges", f"{base}..{head}"):
        raise Refused("the branch has merge commits; rebase it to a straight line, so a revert always applies")
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
    """Like a merge: the revert is made in a temporary worktree, journaled, pushed,
    and only then does the main checkout move. A crash at any point leaves the main
    checkout untouched and the intent settled by reconcile_all."""
    repo = _repo(cfg, name)
    if len(sha) < 7:
        raise Refused("give at least 7 characters of the merged commit")
    candidates = [m for m in ledger.merged(cfg.state_dir) if m.get("repo") == name
                  and str(m.get("to_sha", "")).startswith(sha)]
    # An intent with no outcome (a crash after its push) counts only if origin has it.
    candidates = [m for m in candidates if m["kind"] == "merge" or _origin_has(repo, m["to_sha"]) is True]
    if len(candidates) != 1:
        raise Refused(f"{sha} is not one alert-watch merge in {name}")
    target = candidates[0]
    if target["to_sha"] in ledger.reverted(cfg.state_dir):
        raise Refused(f"{target['to_sha'][:10]} was already reverted")
    before = _check_main(repo)
    intent_id = _intent_id()
    tmp = cfg.worktrees_dir / f"revert-{intent_id}"
    cfg.worktrees_dir.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-q", "--detach", str(tmp), before)
    try:
        try:
            _git(tmp, "revert", "--no-edit", f"{target['from_sha']}..{target['to_sha']}")
        except subprocess.CalledProcessError:
            raise Refused("the revert does not apply cleanly; it needs Rex") from None
        after = _git(tmp, "rev-parse", "HEAD")
        intent = {"kind": "revert_intent", "id": intent_id, "at": now,
                  "run_id": os.environ.get("ALERT_WATCH_RUN_ID", ""), "repo": name,
                  "reverts": target["to_sha"], "from_sha": before, "to_sha": after}
        ledger.append_action(cfg.state_dir, intent)
        try:
            _push(repo, f"{after}:refs/heads/main", after)
        except subprocess.CalledProcessError:
            ledger.append_action(cfg.state_dir, {"kind": "revert_abandoned", "intent": intent_id, "at": now})
            raise
        action = {**intent, "kind": "revert", "intent": intent_id}
        ledger.append_action(cfg.state_dir, action)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tmp)], cwd=str(repo), capture_output=True)
    try:
        _git(repo, "merge", "-q", "--ff-only", after)
        landed = _check_main(repo) == after
    except (subprocess.CalledProcessError, Refused):
        landed = False
    if not landed:
        raise PushedNotLocal(f"the revert {after[:10]} is on origin/main, but {repo} did not move to it")
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
            reconcile_all(cfg, now())
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
        except PushUnknown as e:
            print(f"unknown, needs Rex: {e}; the intent stays open and counts", file=sys.stderr)
            return 1
        except subprocess.CalledProcessError as e:
            print(f"git failed: {' '.join(e.cmd)}: {(e.stderr or '').strip()[:500]}", file=sys.stderr)
            return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
