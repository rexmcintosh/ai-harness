"""Recent history of a listed repository, without file contents.

    python3 -m alertwatch.gitinfo <repo> [--count N]

A diagnose run may not run git log, show or diff itself: those print file
contents (a credential committed in the past included), which the Read deny
rules cannot filter. This prints only commit lines, the names of changed files,
and the working tree's short status.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from alertwatch.config import Config, load_config


def main(argv: list[str] | None = None, *, cfg: Config | None = None) -> int:
    ap = argparse.ArgumentParser(prog="alertwatch.gitinfo")
    ap.add_argument("repo")
    ap.add_argument("--count", type=int, default=15)
    args = ap.parse_args(argv)
    cfg = cfg or load_config()
    if args.repo not in cfg.repos:
        print(f"gitinfo: {args.repo!r} is not a listed repository ({', '.join(cfg.repos)})", file=sys.stderr)
        return 2
    if not 1 <= args.count <= 50:
        print("gitinfo: --count must be 1 to 50", file=sys.stderr)
        return 2
    repo = str(cfg.projects_dir / args.repo)
    safe = ["git", "-C", repo, "-c", "core.fsmonitor=false", "-c", "diff.external=", "--no-pager"]
    for cmd in (["log", f"-n{args.count}", "--no-ext-diff", "--no-textconv", "--date=iso",
                 "--format=%h %ad %an: %s", "--name-status", "--no-renames"],
                ["status", "--short", "--branch"]):
        proc = subprocess.run([*safe, *cmd], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            print(f"gitinfo: git {cmd[0]} failed: {proc.stderr.strip()[:300]}", file=sys.stderr)
            return 1
        print(proc.stdout.rstrip())
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
