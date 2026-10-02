"""One runner per item type. diem never implements a workload — it shells
out to council / loom / repo-declared commands, with the 00:50 deadline as
a subprocess hard timeout. Failures return RunResult(ok=False), never raise."""
from __future__ import annotations
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    ok: bool
    duration_s: float
    output_path: str | None = None
    error: str | None = None
    note: str | None = None  # e.g. a healed review baseline — surfaced in the run summary


def _save(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "")
    return str(path)


def _heal_range(repo: str, range_: str, _exec) -> tuple[str | None, str | None]:
    """Verify the recorded base SHA of an `old..head` review range still
    exists. A rebase/squash/force-push can rewrite it out of history, after
    which every `git diff old..head` fails forever ("Invalid revision range")
    and the repo is silently never reviewed. When only the base has vanished
    and the repo itself is healthy, heal instead of erroring: re-anchor the
    range to the merge-base with the repo's default branch, falling back to
    the range head (empty diff -> ok -> the drain records head as the new
    baseline). Genuinely unreachable/broken repos stay fail-closed.

    Returns (range_to_diff, note): note carries the heal description for the
    run summary; range_to_diff is None on fail-closed with the error in note.
    """
    base, _, head = range_.partition("..")
    if not base or not head:
        return range_, None  # unexpected shape — let git diff report it
    if _exec(["git", "-C", repo, "cat-file", "-e",
              f"{base}^{{commit}}"]).returncode == 0:
        return range_, None  # baseline intact — normal path
    # base is gone OR the repo is broken: fail closed unless HEAD resolves
    if _exec(["git", "-C", repo, "rev-parse", "--verify",
              "HEAD"]).returncode != 0:
        return None, f"git repo unreachable: cannot resolve HEAD in {repo}"
    if _exec(["git", "-C", repo, "cat-file", "-e",
              f"{head}^{{commit}}"]).returncode != 0:
        # head vanished too — fail this item; next discovery re-mints the
        # range from the CURRENT head and this heal fixes the base side
        return None, (f"range head {head[:12]} rewritten out of history; "
                      "will re-discover from current HEAD")
    healed = None
    ref = _exec(["git", "-C", repo, "symbolic-ref", "--quiet",
                 "refs/remotes/origin/HEAD"])
    candidates = ([ref.stdout.strip()]
                  if ref.returncode == 0 and ref.stdout.strip() else [])
    candidates += ["refs/heads/main", "refs/heads/master"]
    for cand in candidates:
        mb = _exec(["git", "-C", repo, "merge-base", cand, head])
        if mb.returncode == 0 and mb.stdout.strip():
            healed = mb.stdout.strip()
            break
    if healed is None:
        healed = head  # last resort: empty range — baseline resets to head
    return (f"{healed}..{head}",
            f"healed stale review baseline {base[:12]} -> {healed[:12]} "
            "(base SHA rewritten out of history)")


def run_item(item, cfg, env: dict, *, deadline_epoch: float,
             run=subprocess.run, clock=time.monotonic) -> RunResult:
    start = clock()
    timeout = max(30.0, deadline_epoch - start)
    p = item.payload

    def _exec(argv, *, cwd=None, input=None):
        return run(argv, capture_output=True, text=True, timeout=timeout,
                   env=env, cwd=cwd, input=input)

    def _done(proc, out_path: Path | None, log_stdout: bool, note=None):
        dur = clock() - start
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[-500:]
            return RunResult(False, dur, error=f"exit {proc.returncode}: {err}",
                             note=note)
        saved = _save(out_path, proc.stdout) if (out_path and log_stdout) else None
        return RunResult(True, dur, output_path=saved, note=note)

    try:
        if item.type == "ask":
            proc = _exec(["council", "ask", p["question"],
                          "--panel", p.get("panel", "decision"), "--format", "md"])
            return _done(proc, Path(cfg.outputs_dir) / "asks" / f"{item.id}.md", True)

        if item.type == "review":
            repo = p["repo"]
            name = Path(repo).name
            out = Path(cfg.outputs_dir) / "reviews" / f"{name}-{item.id}.md"
            if p.get("diff"):
                proc = _exec(["council", "review", "--diff", "--format", "md"],
                             cwd=repo)
                return _done(proc, out, True)
            rng, note = _heal_range(repo, p["range"], _exec)
            if rng is None:
                return RunResult(False, clock() - start, error=note)
            gd = _exec(["git", "-C", repo, "diff", rng])
            if gd.returncode != 0:
                return RunResult(False, clock() - start,
                                 error=f"git diff failed: {gd.stderr.strip()[-300:]}")
            if not gd.stdout.strip():
                # nothing to review — ok result lets the drain advance the
                # baseline to the range head, which completes a heal
                return RunResult(True, clock() - start, note=note)
            proc = _exec(["council", "review", "-", "--format", "md"],
                         input=gd.stdout)
            return _done(proc, out, True, note=note)

        if item.type == "images":
            # Command provenance: SOLELY the target repo's standing order at
            # run time. A payload-supplied command is never honored — a
            # queue-dir writer could otherwise smuggle argv past the
            # advertised whitelist/standing-order gate.
            standing_order_path = Path(p["repo"]) / ".diem" / "standing-order.json"
            try:
                so_data = json.loads(standing_order_path.read_text())
                command = so_data.get("command")
            except (FileNotFoundError, json.JSONDecodeError, KeyError,
                    AttributeError, TypeError):
                return RunResult(False, clock() - start,
                                 error="images item has no command and no standing order")
            if not isinstance(command, list) or not command:
                return RunResult(False, clock() - start,
                                 error="images item has no command and no standing order")
            argv = list(command) + ["--count", str(p["count"])]
            proc = _exec(argv, cwd=p["repo"])
            return _done(proc, Path(cfg.outputs_dir) / "logs" / f"{item.id}.log", True)

        if item.type == "backfill":
            argv = list(cfg.loom_cmd) + ["--max-targets", str(p.get("max_targets", 2))]
            proc = _exec(argv, cwd=str(cfg.loom_repo))
            return _done(proc, Path(cfg.outputs_dir) / "logs" / f"{item.id}.log", True)

        if item.type == "cmd":
            entry = cfg.cmd_whitelist.get(p.get("name", ""))
            if not entry:
                return RunResult(False, clock() - start,
                                 error=f"'{p.get('name')}' not in cmd whitelist")
            proc = _exec(list(entry["argv"]), cwd=entry["repo"])
            return _done(proc, Path(cfg.outputs_dir) / "logs" / f"{item.id}.log", True)

        return RunResult(False, clock() - start, error=f"unknown type {item.type}")
    except subprocess.TimeoutExpired:
        return RunResult(False, clock() - start,
                         error=f"timeout after {timeout:.0f}s (deadline backstop)")
    except Exception as e:  # noqa: BLE001 — one bad job must not kill the drain
        return RunResult(False, clock() - start, error=f"{type(e).__name__}: {e}")
