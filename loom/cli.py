# loom/cli.py
"""`python -m loom.cli <cmd>`:
  absorb [--live] [--max-targets N] [--deadline-seconds S] [--weave-reserve F]
                                         nightly distill (+weave if --live), backend=claude
  backfill [--max-targets N] [--all] [--distill N]
                                         backlog weave, backend=venice (DIEM);
                                         --distill N also distills up to N pending
                                         sessions on Venice first (default 0: weave only)
  promote [--auto]                       apply staged .claude + merge loom-shadow -> master
                                         (--auto: only if the unattended gate allows)
  hold [--clear]                         veto tonight's auto-promote (self-expires next day)
  pending                                JSON: what's landing + what needs a decision
  resolve <sid>#<idx> --accept|--reject  record a HUMAN decision on a quarantined
                                         learning (accept = landed/landing by hand ->
                                         committed; reject -> rejected). No model call,
                                         no wiki write.
  requeue <session_id>                   return a quarantined/stuck session to pending
  fixup-triage [--apply]                 one-time 2026-08 triage repairs (dry-run default):
                                         skip self-generated transcripts, recover sessions
                                         lost to fenced distill output, quarantine still-
                                         unreadable artifacts, clear stale people/ routes
  rollback --ts <stamp>                  restore ~/.claude from a promote backup
  reconcile-phantom [--apply] [--folds-dir D]
                                         one-time cleanup of the phantom ~/wiki/wiki/ tree
                                         (dry-run default): fold curated text into the
                                         real articles, merge markers, git rm the phantoms,
                                         commit on master. Never pushes.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


class _PathEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Path):
            return str(o)
        return super().default(o)

from .autopromote import auto_promote_check, clear_hold, next_promote_date, set_hold
from .ledger import WeaveLedger
from .pending import _learning_block, cluster_blocked, pending_summary
from .promote import promote, rollback
from .run import Config, absorb
from .state import LoomState

_HOME = Path.home()
_REPO = _HOME / "projects" / "build-ai-automation-workflow"
_LOOM = _REPO / "loom"


def pending_payload(cfg: Config, today: str) -> dict:
    return pending_summary(wiki_root=cfg.wiki_master, ledger_path=cfg.ledger_path,
                           learnings_dir=cfg.loom_dir / "learnings",
                           loom_dir=cfg.loom_dir, today=today)


def default_config() -> Config:
    return Config(
        projects_dir=_HOME / ".claude" / "projects",
        loom_dir=_LOOM,
        state_path=_LOOM / "state.json",
        wiki_worktree=_HOME / "wiki-loom-shadow",
        wiki_master=_HOME / "wiki",
        claude_dir=_HOME / ".claude",
        ledger_path=_LOOM / "weave_ledger.json",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="loom")
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("absorb")
    a.add_argument("--live", action="store_true")
    a.add_argument("--max-targets", type=int, default=10)
    a.add_argument("--max-per-target", type=int, default=4)
    a.add_argument("--deadline-seconds", type=float, default=3600.0)
    # Fraction of the deadline held back for the weave so distill cannot starve it
    # (it did, silently, for 13 nights ending 2026-07-23). Tunable from cron.
    a.add_argument("--weave-reserve", type=float, default=0.4)

    b = sub.add_parser("backfill")
    b.add_argument("--max-targets", type=int, default=10)
    b.add_argument("--max-per-target", type=int, default=4)
    b.add_argument("--all", action="store_true")
    b.add_argument("--distill", type=int, default=0, metavar="N",
                   help="also distill up to N pending sessions on the Venice backend")

    pr = sub.add_parser("promote")
    pr.add_argument("--auto", action="store_true")
    hd = sub.add_parser("hold"); hd.add_argument("--clear", action="store_true")
    sub.add_parser("pending")
    rs = sub.add_parser("resolve"); rs.add_argument("learning_id",
        help="ledger key <session_id>#<idx>, as shown in `loom pending` decisions")
    rsg = rs.add_mutually_exclusive_group(required=True)
    rsg.add_argument("--accept", action="store_true",
                     help="the fact was (or will be) landed by hand -> committed")
    rsg.add_argument("--reject", action="store_true",
                     help="the fact should never land -> rejected")
    rq = sub.add_parser("requeue"); rq.add_argument("session_id")
    fx = sub.add_parser("fixup-triage"); fx.add_argument("--apply", action="store_true")
    rb = sub.add_parser("rollback"); rb.add_argument("--ts", required=True)
    rp = sub.add_parser("reconcile-phantom")
    rp.add_argument("--apply", action="store_true")
    rp.add_argument("--folds-dir", type=Path, default=_REPO / "docs" / "loom-phantom-wiki-folds",
                    help="dir of <real-rel-path>.md fold files appended to the real articles")

    args = parser.parse_args(argv)
    cfg = default_config()
    today = time.strftime("%Y-%m-%d")

    if args.cmd == "absorb":
        summary = absorb(cfg, shadow=not args.live, backend="claude",
                         max_targets=args.max_targets, max_per_target=args.max_per_target,
                         today=today, deadline_seconds=args.deadline_seconds,
                         weave_reserve=args.weave_reserve)
        print(json.dumps(summary, cls=_PathEncoder)); return 0
    if args.cmd == "backfill":
        cap = 10 ** 9 if args.all else args.max_targets
        # By default backfill only weaves the already-distilled backlog on Venice/DIEM.
        # --distill N additionally distills up to N pending sessions on Venice
        # (deepseek-v4-flash, 1M ctx) so backlog distillation rides DIEM instead of
        # waiting on the nightly Claude window.
        summary = absorb(cfg, shadow=False, backend="venice", max_targets=cap,
                         max_per_target=args.max_per_target, today=today,
                         distill=args.distill > 0, max_distill=args.distill or None)
        print(json.dumps(summary, cls=_PathEncoder)); return 0
    if args.cmd == "promote":
        landed = {}
        if getattr(args, "auto", False):
            # Unattended: the gate decides. A refusal is a normal outcome, not an
            # error — the nightly run must not fail just because it stood down.
            check = auto_promote_check(wiki_root=cfg.wiki_master,
                                       loom_dir=cfg.loom_dir, today=today)
            if not check["go"]:
                print(json.dumps({"promoted": False, **check}, cls=_PathEncoder)); return 0
            # Carry what's about to land: once promote() merges, the pending list is
            # gone, and the morning briefing line is built from this JSON.
            landed = {"articles": check["articles"], "commits": check["commits"]}
        res = promote(wiki_root=cfg.wiki_master, shadow_root=cfg.wiki_worktree,
                      claude_root=cfg.claude_dir,
                      backups_dir=cfg.loom_dir / "promote-backups", expect_unmodified=True)
        print(json.dumps({"promoted": True, **landed, **res}, cls=_PathEncoder)); return 0
    if args.cmd == "hold":
        if args.clear:
            clear_hold(cfg.loom_dir); print(json.dumps({"hold": None})); return 0
        # Store the date of the promote this veto must stop (the next 02:00-UTC
        # run), NOT today's date — a 07:00 veto targets tomorrow's `today`.
        target = next_promote_date()
        set_hold(cfg.loom_dir, target); print(json.dumps({"hold": target})); return 0
    if args.cmd == "pending":
        print(json.dumps(pending_payload(cfg, today), cls=_PathEncoder)); return 0
    if args.cmd == "resolve":
        # Record a human decision on a quarantined learning. Deliberately dumb:
        # no model call, no wiki write — the human already landed (or vetoed)
        # the fact; this only makes the ledger agree so `pending` stops asking.
        led = WeaveLedger(cfg.ledger_path)
        entry = led.entry(args.learning_id)
        if not entry:
            print(json.dumps({"error": f"unknown learning id: {args.learning_id}"}))
            return 1
        was = entry.get("status", "")
        if was != "quarantined":
            # Typo protection: never silently rewrite an already-settled (or
            # in-flight) entry — resolve exists for quarantined learnings only.
            print(json.dumps({"error": f"{args.learning_id} is '{was}', not "
                              f"quarantined — resolve only clears quarantined "
                              f"learnings", "status": was}))
            return 1
        block = _learning_block(cfg.loom_dir / "learnings", args.learning_id)
        if args.accept:
            status, reason = "committed", "hand-resolved"
        else:
            status, reason = "rejected", "hand-rejected"
        led.mark(args.learning_id, status, reason=reason)
        print(json.dumps({"resolved": args.learning_id, "status": status,
                          "reason": reason, "target": entry.get("target", ""),
                          "was": was, "text": block},
                         cls=_PathEncoder))
        return 0
    if args.cmd == "requeue":
        # Re-queue a quarantined/stuck session -> next `absorb` re-runs Stage-0 from scratch.
        # (A committed session won't re-weave: git trailers reconcile it back to committed.)
        LoomState(cfg.state_path).advance(args.session_id, "pending")
        print(json.dumps({"requeued": args.session_id})); return 0
    if args.cmd == "fixup-triage":
        from .fixup import triage_fixup
        print(json.dumps(triage_fixup(cfg, apply=args.apply), cls=_PathEncoder)); return 0
    if args.cmd == "rollback":
        res = rollback(backups_dir=cfg.loom_dir / "promote-backups", ts=args.ts)
        print(json.dumps(res, cls=_PathEncoder)); return 0
    if args.cmd == "reconcile-phantom":
        from .phantom import PhantomError, apply as phantom_apply, plan as phantom_plan
        if not args.apply:
            print(json.dumps(phantom_plan(cfg.wiki_master, folds_dir=args.folds_dir),
                             cls=_PathEncoder)); return 0
        try:
            res = phantom_apply(cfg.wiki_master, folds_dir=args.folds_dir,
                                lock_path=cfg.loom_dir / ".run.lock",
                                shadow_root=cfg.wiki_worktree)
        except PhantomError as e:
            print(json.dumps({"applied": False, "error": str(e)})); return 1
        print(json.dumps(res, cls=_PathEncoder)); return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
