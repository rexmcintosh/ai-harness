#!/usr/bin/env python3
"""session-gc — lifecycle hygiene for Claude Code session worktrees & branches.

The Claude Code harness owns worktree DIRECTORIES (it creates and reaps them,
sometimes mid-session). This tool owns the two layers it leaves unmanaged:

  * the WIP layer — uncommitted work in a live session worktree, which is lost
    when the harness reaps the directory. `snapshot` captures it out-of-band.
  * the BRANCH layer — `claude/*` branches that outlive their reaped worktrees.
    `sweep` deletes the safe (merged) ones and REPORTS the stranded (unmerged)
    ones, which are the only surviving artifact of an abandoned session.

Subcommands:
  snapshot   Snapshot dirty claude/* worktrees to refs/wip/* (cron, ~10 min). No-op when clean.
             Detached-HEAD session worktrees (under .claude/worktrees/) are captured too,
             under refs/wip/detached/<short-sha>-<pathdigest>/<ts>.
  sweep      Classify orphan claude/* branches; delete Tier A; report B/C. Dry-run unless --apply.
  report     Print the latest sweep report (read-only).
  restore    Recreate a deleted branch from the undo journal, or list WIP snapshots.

Safety invariants (enforced below, tagged I1..I9):
  I1  Never write inside a worktree directory or its per-worktree index/metadata.
  I2  Only ever mutate refs under refs/heads/claude/* and refs/wip/*. Remote refs read-only.
  I3  Delete a branch only if: name is claude/*, not checked out in any worktree,
      Tier A (or gated Tier B), default branch resolved unambiguously, age > grace.
  I4  Verify merged-ness against the DEFAULT branch ourselves (classify), then delete
      with `-D`; git's own `-d` merge check is upstream-based and both over- and
      under-approximates merged-into-default, so we supersede it. `-D` still refuses a
      branch checked out in any worktree, which is the live-session guard we rely on.
  I5  Creation-race grace: never delete a branch whose ref is younger than GRACE.
  I6  Journal every deletion before it happens ("recoverable via reflog" is FALSE
      because `git branch -d` deletes the branch's reflog with it). If the delete is
      then refused, a `<tier>-refused` marker row is appended so the journal never
      claims a deletion that did not happen; `restore` treats such rows as no-ops.
  I7  Fail closed per repo: any ambiguity (default branch, parse, git error) -> skip + report.
  I8  Tier C (genuinely unmerged) is structurally report-only. No flag deletes it.
  I9  One run at a time (lockfile); address git via the main checkout, never cwd.
"""
from __future__ import annotations

import argparse
import fcntl
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, "projects")
STATE_DIR = os.path.join(PROJECTS, ".session-gc")
REPORT_PATH = os.path.join(STATE_DIR, "report.md")
JOURNAL_PATH = os.path.join(STATE_DIR, "journal.log")
LOCK_PATH = os.path.join(STATE_DIR, "lock")
EXCLUDE_PATH = os.path.join(STATE_DIR, "exclude")  # optional: one repo dir-name per line

BRANCH_PREFIX = "claude/"
WIP_PREFIX = "refs/wip/"
GRACE_SECONDS = 60 * 60           # I5: don't touch branches younger than 1h
TIER_B_MIN_AGE = 14 * 24 * 3600   # content-merged auto-delete only past 14 days
WIP_EXPIRE_SECONDS = 30 * 24 * 3600
TELEGRAM_MESSAGE_LIMIT = 4096

def _send_telegram(chat_id: str, message: str) -> bool:
    """Send one plain-text Telegram message through the configured tg-send."""
    sender = os.environ.get("SESSION_GC_TG_SEND") or shutil.which("tg-send")
    if not sender:
        print("session-gc: tg-send not found; skipping Telegram notification",
              file=sys.stderr)
        return False
    p = subprocess.run(
        [sender, chat_id, "-"],
        input=message, capture_output=True, text=True, timeout=30,
    )
    if p.returncode != 0:
        detail = p.stderr.strip() or f"exit status {p.returncode}"
        raise RuntimeError(detail)
    return True


def _telegram_chunks(entries: list[str]) -> list[str]:
    """Join entries into Telegram-sized messages, preferring line boundaries."""
    remaining = "\n".join(entries)
    chunks = []
    while len(remaining) > TELEGRAM_MESSAGE_LIMIT:
        split = remaining.rfind("\n", 0, TELEGRAM_MESSAGE_LIMIT + 1)
        if split <= 0:
            split = TELEGRAM_MESSAGE_LIMIT
        chunks.append(remaining[:split])
        remaining = remaining[split:]
        if remaining.startswith("\n"):
            remaining = remaining[1:]
    if remaining:
        chunks.append(remaining)
    return chunks


# ----------------------------------------------------------------------------- git helpers

class GitError(Exception):
    pass


def git(repo: str, *args: str, check: bool = True, env: dict | None = None) -> str:
    """Run `git -C <repo> <args>` and return stdout. All ref/branch ops target the
    main checkout so they hit the shared object store even if invoked from a worktree (I9)."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    p = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, env=full_env,
    )
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} (in {repo}): {p.stderr.strip()}")
    return p.stdout


def git_ok(repo: str, *args: str) -> bool:
    return subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True).returncode == 0


def discover_repos() -> list[str]:
    """Main checkouts directly under ~/projects (nested .claude/worktrees are skipped
    because they are not at ~/projects/<name>)."""
    excludes = set()
    if os.path.exists(EXCLUDE_PATH):
        with open(EXCLUDE_PATH) as fh:
            excludes = {ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")}
    repos = []
    for path in sorted(glob.glob(os.path.join(PROJECTS, "*"))):
        if not os.path.isdir(os.path.join(path, ".git")):
            continue
        if os.path.basename(path) in excludes:
            continue
        repos.append(path)
    return repos


def default_branch_ref(repo: str) -> str | None:
    """Resolve the default branch to a concrete ref to compare against, or None (I7)."""
    out = git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False).strip()
    prefix = "refs/remotes/origin/"
    name = out[len(prefix):] if out.startswith(prefix) else ""  # keeps slash-named defaults
    if not name:
        locals_ = [b for b in ("main", "master")
                   if git_ok(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{b}")]
        if len(locals_) != 1:
            return None
        name = locals_[0]
    # Prefer a local ref; fall back to the remote-tracking ref.
    if git_ok(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{name}"):
        return name
    if git_ok(repo, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{name}"):
        return f"origin/{name}"
    return None


def parse_worktrees(repo: str) -> list[dict]:
    """Parse `git worktree list --porcelain` into dicts with path/branch/locked flags."""
    out = git(repo, "worktree", "list", "--porcelain")
    entries, cur = [], {}
    for line in out.splitlines():
        if not line.strip():
            if cur:
                entries.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):]}
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):].replace("refs/heads/", "", 1)
        elif line == "detached":
            cur["detached"] = True
        elif line == "locked":
            cur["locked"] = True
        elif line.startswith("locked"):
            cur["locked"] = True
    if cur:
        entries.append(cur)
    return entries


def live_branches(worktrees: list[dict]) -> set[str]:
    """Branches checked out in (or locked by) any registered worktree — never touch these."""
    return {wt["branch"] for wt in worktrees if wt.get("branch")}


def claude_branches(repo: str) -> list[str]:
    out = git(repo, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{BRANCH_PREFIX}")
    return [b for b in out.splitlines() if b.startswith(BRANCH_PREFIX)]


def branch_age_seconds(repo: str, branch: str) -> float:
    """Age since the branch REF was created, from its oldest reflog entry (I5).

    Fails CLOSED (returns 0.0 == "brand new, protected") whenever age is genuinely
    unknowable, so absence-of-evidence never counts as evidence-of-old-age:
      * reflog HAS entries      -> real age from the oldest entry.
      * reflog empty + enabled  -> entries EXPIRED (default 90d) => branch is old => inf (eligible).
      * reflog empty + DISABLED  -> cannot tell fresh from old => 0.0 (protect; report-only).
      * any parse error          -> 0.0 (protect).
    A fresh branch created with reflogs enabled always has an entry, so the only
    fail-open path (empty+enabled) provably cannot be a brand-new branch."""
    out = git(repo, "reflog", "show", "--date=raw", branch, check=False)
    lines = [ln for ln in out.splitlines() if "@{" in ln]
    if lines:
        # oldest entry is the last line: "<sha> <ref>@{<unixts> <tz>}: <msg>"
        try:
            stamp = lines[-1].split("@{", 1)[1].split(" ", 1)[0]
            return time.time() - int(stamp)
        except (IndexError, ValueError):
            return 0.0  # unparseable => protect
    # no reflog entries: distinguish "expired (old)" from "disabled (unknown)"
    val = git(repo, "config", "--get", "core.logAllRefUpdates", check=False).strip().lower()
    reflogs_enabled = val != "false"  # default is true for repos with a work tree
    return float("inf") if reflogs_enabled else 0.0


def classify(repo: str, branch: str, base: str) -> str:
    """Return 'A' (ancestry-merged), 'B' (content-merged: rebase/squash), or 'C' (unmerged)."""
    ahead = git(repo, "rev-list", "--count", f"{base}..{branch}").strip()
    if ahead == "0":
        return "A"  # tip is an ancestor of the default branch
    # Tier B, rebase: every commit is patch-equivalent to one already on base.
    cherry = [ln for ln in git(repo, "cherry", base, branch).splitlines() if ln.strip()]
    if cherry and all(ln.startswith("-") for ln in cherry):
        return "B"
    # Tier B, squash: merging the branch into base yields base's own tree (no net change).
    mt = git(repo, "merge-tree", "--write-tree", base, branch, check=False).splitlines()
    if mt:
        merged_tree = mt[0].strip()
        base_tree = git(repo, "rev-parse", f"{base}^{{tree}}", check=False).strip()
        if merged_tree and merged_tree == base_tree:
            return "B"
    return "C"


# ----------------------------------------------------------------------------- state/io

def ensure_state() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)


def journal(repo: str, branch: str, sha: str, tier: str) -> None:
    """Append-only undo record, written BEFORE deletion (I6).

    When a journaled delete is subsequently REFUSED by git, the caller appends a
    second row with tier `<tier>-refused` (see cmd_sweep). The record-before-act
    property of I6 is preserved, while the journal's latest row for a branch
    always reflects what actually happened."""
    ensure_state()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(JOURNAL_PATH, "a") as fh:
        fh.write(f"{ts}\t{repo}\t{branch}\t{sha}\t{tier}\n")


class RunLock:
    """I9: only one session-gc run at a time."""
    def __enter__(self):
        ensure_state()
        self._fh = open(LOCK_PATH, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("session-gc: another run holds the lock; exiting.", file=sys.stderr)
            sys.exit(0)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()


def now_utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------- snapshot

def _is_session_worktree(path: str) -> bool:
    """True when the worktree lives under some repo's .claude/worktrees/ — the
    harness's session-worktree home. Used to scope detached-HEAD snapshots to
    session worktrees only."""
    return f"{os.sep}.claude{os.sep}worktrees{os.sep}" in path


def cmd_snapshot(args) -> int:
    """Snapshot every dirty claude/* worktree to refs/wip/<branch>/<ts> using a TEMP
    index so the session's own index/HEAD/files are never touched (I1). Dirty
    DETACHED-HEAD session worktrees (no branch to key on) are snapshotted under
    refs/wip/detached/<short-sha>-<pathdigest>/<ts>."""
    with RunLock():
        ensure_state()
        snapped = 0
        for repo in discover_repos():
            try:
                worktrees = parse_worktrees(repo)
            except GitError:
                continue  # I7
            for wt in worktrees:
                branch = wt.get("branch")
                path = wt.get("path")
                if not path or not os.path.isdir(path):
                    continue
                if branch:
                    if not branch.startswith(BRANCH_PREFIX):
                        continue
                elif not (wt.get("detached") and _is_session_worktree(path)):
                    # Detached HEAD outside .claude/worktrees/ is a human experiment,
                    # not a session — leave it alone.
                    continue
                try:
                    dirty = bool(git(path, "status", "--porcelain").strip())
                    if not dirty:
                        continue
                    head = git(path, "rev-parse", "HEAD").strip()
                    # Detached session worktrees have no branch name; key their WIP
                    # refs by parked sha + a path digest, so two detached sessions
                    # on the SAME commit can never collide on one ref.
                    if branch:
                        label = branch
                    else:
                        pdig = hashlib.sha1(path.encode()).hexdigest()[:8]
                        label = f"detached/{head[:12]}-{pdig}"
                    # I1: a private, NON-EXISTENT index path (git rejects an existing
                    # 0-byte index). git creates it fresh; the session's real index is
                    # never touched. Clean it up with the temp dir afterwards.
                    tmp_dir = tempfile.mkdtemp(prefix="sgc-idx-")
                    tmp_index = os.path.join(tmp_dir, "index")
                    try:
                        env = {"GIT_INDEX_FILE": tmp_index}
                        # Seed the temp index from HEAD so uncommitted edits to
                        # tracked-but-gitignored files (.env, config) are captured;
                        # `add -A` alone starts empty and honors .gitignore, dropping them.
                        git(path, "read-tree", "HEAD", env=env)
                        git(path, "add", "-A", env=env)
                        tree = git(path, "write-tree", env=env).strip()
                        stamp = now_utc_stamp()
                        commit = git(
                            path, "commit-tree", tree, "-p", head,
                            "-m", f"session-gc wip snapshot {stamp}", env=env,
                        ).strip()
                        ref = f"{WIP_PREFIX}{label}/{stamp.replace(':', '').replace('-', '')}"
                        git(repo, "update-ref", ref, commit)  # I2
                        snapped += 1
                    finally:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                except GitError as e:
                    # I7: never let one worktree abort the sweep — but surface it
                    # (cron logs) so a silent-swallow can't hide a real failure again.
                    print(f"session-gc snapshot: skipped {branch or path} in "
                          f"{os.path.basename(repo)}: {e}", file=sys.stderr)
                    continue
            _expire_wip(repo)
        if snapped and not args.quiet:
            print(f"session-gc: snapshotted {snapped} dirty worktree(s)")
        return 0


def _expire_wip(repo: str) -> None:
    try:
        out = git(repo, "for-each-ref", "--format=%(refname) %(committerdate:unix)", WIP_PREFIX)
    except GitError:
        return
    cutoff = time.time() - WIP_EXPIRE_SECONDS
    for line in out.splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        ref, ts = parts
        try:
            if int(ts) < cutoff:
                git(repo, "update-ref", "-d", ref, check=False)  # I2: only refs/wip/*
        except ValueError:
            continue


# ----------------------------------------------------------------------------- sweep

def cmd_sweep(args) -> int:
    with RunLock():
        ensure_state()
        report = [f"# session-gc sweep — {now_utc_stamp()}",
                  f"mode: {'APPLY' if args.apply else 'dry-run'}"
                  f"{' +tier-b' if args.delete_tier_b else ''}", ""]
        totals = {"A_deleted": 0, "A_candidate": 0, "B": 0, "C": 0, "skipped_repos": 0}
        stranded = []  # Tier C across all repos -> drives notification

        repos = discover_repos()
        if args.repo:
            repos = [r for r in repos if os.path.basename(r) == args.repo]

        for repo in repos:
            name = os.path.basename(repo)
            try:
                worktrees = parse_worktrees(repo)
            except GitError as e:
                report.append(f"## {name}\n- SKIPPED (worktree list failed: {e})")
                totals["skipped_repos"] += 1
                continue
            base = default_branch_ref(repo)
            if base is None:  # I7
                report.append(f"## {name}\n- SKIPPED (could not resolve default branch)")
                totals["skipped_repos"] += 1
                continue
            live = live_branches(worktrees)
            orphans = [b for b in claude_branches(repo) if b not in live]
            if not orphans:
                continue
            lines = []
            for b in sorted(orphans):
                try:
                    age = branch_age_seconds(repo, b)
                    if age < GRACE_SECONDS:  # I5
                        lines.append(f"- `{b}` — SKIP (younger than grace period)")
                        continue
                    tier = classify(repo, b, base)
                    sha = git(repo, "rev-parse", b).strip()
                except GitError as e:
                    lines.append(f"- `{b}` — SKIP (git error: {e})")
                    continue

                if tier == "A":
                    if args.apply:
                        journal(repo, b, sha, "A")           # I6 before delete
                        # I4: we PROVED ancestry into the default branch ourselves
                        # (classify), which is stronger and correct — `git branch -d`'s
                        # own check is merged-into-UPSTREAM, which both misses truly-merged
                        # branches (stale remote ref) and would accept merged-into-upstream-
                        # but-not-main ones. So use -D (honors our proof) — it STILL refuses
                        # a branch checked out in any worktree, preserving the live guard.
                        if git_ok(repo, "branch", "-D", b):
                            lines.append(f"- `{b}` — DELETED (Tier A, merged)")
                            totals["A_deleted"] += 1
                        else:
                            journal(repo, b, sha, "A-refused")  # I6: journal must not claim a delete that never happened
                            lines.append(f"- `{b}` — KEPT (Tier A; `-D` refused — now checked out by a live worktree)")
                    else:
                        lines.append(f"- `{b}` — would delete (Tier A, merged)")
                        totals["A_candidate"] += 1
                elif tier == "B":
                    totals["B"] += 1
                    old_enough = age >= TIER_B_MIN_AGE
                    if args.apply and args.delete_tier_b and old_enough:
                        journal(repo, b, sha, "B")           # I6
                        if git_ok(repo, "branch", "-D", b):  # content-merge proven above
                            lines.append(f"- `{b}` — DELETED (Tier B, content-merged)")
                        else:
                            journal(repo, b, sha, "B-refused")  # I6: mark the non-delete
                            lines.append(f"- `{b}` — KEPT (Tier B; `-D` refused; review)")
                    else:
                        gate = "" if old_enough else " (<14d, held)"
                        lines.append(f"- `{b}` — safe-delete candidate (Tier B, content-merged){gate}")
                else:  # Tier C — I8: report only, always
                    totals["C"] += 1
                    stranded.append((repo, name, b, sha))
                    lines.append(f"- `{b}` — **STRANDED WIP** (Tier C, unmerged; kept)")
            if lines:
                report.append(f"## {name}")
                report.extend(lines)
                report.append("")

        report.append("---")
        report.append(
            f"Tier A: {totals['A_deleted']} deleted / {totals['A_candidate']} candidate  |  "
            f"Tier B: {totals['B']}  |  Tier C stranded: {totals['C']}  |  "
            f"repos skipped: {totals['skipped_repos']}"
        )
        text = "\n".join(report)
        with open(REPORT_PATH, "w") as fh:
            fh.write(text + "\n")
        print(text)
        if stranded:
            print(f"\nsession-gc: {len(stranded)} stranded WIP branch(es) need attention "
                  f"(see {REPORT_PATH})", file=sys.stderr)
            notify_strict = getattr(args, "notify_strict", False)
            if args.notify or notify_strict:
                delivery_failed = False
                chat_id = os.environ.get("SESSION_GC_TG_CHAT_ID")
                if not chat_id:
                    print("--notify: SESSION_GC_TG_CHAT_ID not set; skipping send",
                          file=sys.stderr)
                    delivery_failed = True
                else:
                    entries = []
                    for repo, name, branch, sha in stranded:
                        try:
                            subject = git(
                                repo, "show", "-s", "--format=%s", sha,
                            ).strip()
                        except GitError:
                            subject = ""
                        entries.append(
                            f"{name} {branch}: {subject or sha[:12]}"
                        )
                    sender = getattr(args, "_sender", None) or _send_telegram
                    for message in _telegram_chunks(entries):
                        try:
                            if sender(chat_id, message) is False:
                                delivery_failed = True
                                break
                        except Exception as e:
                            delivery_failed = True
                            print(f"session-gc: Telegram notification failed: {e}",
                                  file=sys.stderr)
                if notify_strict and delivery_failed:
                    return 1
        return 0


# ----------------------------------------------------------------------------- report / restore

def cmd_report(args) -> int:
    if not os.path.exists(REPORT_PATH):
        print("session-gc: no report yet — run `session-gc sweep` first.")
        return 0
    with open(REPORT_PATH) as fh:
        sys.stdout.write(fh.read())
    return 0


def cmd_restore(args) -> int:
    if not os.path.exists(JOURNAL_PATH):
        print("session-gc: no journal yet.")
        return 0
    rows = []
    with open(JOURNAL_PATH) as fh:
        for ln in fh:
            parts = ln.rstrip("\n").split("\t")
            if len(parts) == 5:
                rows.append(parts)  # ts, repo, branch, sha, tier
    if not args.branch:  # list mode
        for ts, repo, branch, sha, tier in rows:
            print(f"{ts}  {os.path.basename(repo):30}  {branch:40}  {sha[:10]}  {tier}")
        return 0
    matches = [r for r in rows if r[2] == args.branch]
    if not matches:
        print(f"session-gc: no journal entry for {args.branch}")
        return 1
    ts, repo, branch, sha, tier = matches[-1]  # most recent
    if tier.endswith("-refused"):
        if git_ok(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"):
            print(f"session-gc: last journal entry for {branch} is a refused delete "
                  f"(the branch was never deleted); nothing to restore.")
            return 1
        # Refused by session-gc but gone anyway (deleted later by hand/another
        # tool): fall back to the most recent real deletion record.
        real = [r for r in matches if not r[4].endswith("-refused")]
        if not real:
            print(f"session-gc: {branch} is gone but the journal has only "
                  f"refused-delete rows for it; cannot restore.")
            return 1
        ts, repo, branch, sha, tier = real[-1]
    if git_ok(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"):
        print(f"session-gc: {branch} already exists in {os.path.basename(repo)}; "
              f"nothing to restore.")
        return 1
    if not git_ok(repo, "cat-file", "-e", sha):
        print(f"session-gc: object {sha} is gone from {repo}; cannot restore.")
        return 1
    git(repo, "branch", branch, sha)
    print(f"session-gc: restored {branch} -> {sha[:10]} in {os.path.basename(repo)}")
    return 0


# ----------------------------------------------------------------------------- entry

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="session-gc", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("snapshot", help="snapshot dirty claude/* worktrees to refs/wip/*")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_snapshot)

    sw = sub.add_parser("sweep", help="classify orphan claude/* branches; delete Tier A; report B/C")
    sw.add_argument("--apply", action="store_true", help="perform Tier A deletions (default: dry-run)")
    sw.add_argument("--delete-tier-b", action="store_true",
                    help="also delete content-merged (Tier B) branches older than 14d")
    sw.add_argument("--repo", help="limit to a single repo (dir name)")
    sw.add_argument("--notify", action="store_true",
                    help="notify Telegram when Tier C stranded WIP is found")
    sw.add_argument("--notify-strict", action="store_true",
                    help="notify Telegram and return non-zero if delivery fails")
    sw.set_defaults(func=cmd_sweep)

    rp = sub.add_parser("report", help="print the latest sweep report")
    rp.set_defaults(func=cmd_report)

    rs = sub.add_parser("restore", help="restore a deleted branch, or list the journal")
    rs.add_argument("branch", nargs="?", help="branch to restore (omit to list journal)")
    rs.set_defaults(func=cmd_restore)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
