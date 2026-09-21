"""`diem` console entry. Cron calls `diem drain --checkpoint`; humans and
Claude sessions use queue/status/pause. Config: ~/.config/diem/config.toml."""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import venice_usage

from .balance import BalanceClient, BalanceUnavailable
from .config import DiemConfig, load_venice_key, load_venice_admin_key, _read_env
from .drain import _last_fired, floor_for, next_deadline, next_reset, run_checkpoint
from .queue import QueueDir, new_item
from .report import evening_ping, send_telegram, write_morning_report
from .runners import run_item
from .state import Estimates, Reviewed, clear_pause, set_pause
from .usage import UsageClient, UsageUnavailable
from .queue import Item  # noqa: F401 (re-export convenience for sessions)


def _now() -> datetime:
    # The DIEM epoch resets at 00:00 UTC; anchor all timing to UTC regardless of
    # the host/process timezone. Naive UTC keeps the rest of the naive datetime math.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _bits(cfg):
    q = QueueDir(cfg.state_dir)
    est = Estimates(Path(cfg.state_dir) / "estimates.json", cfg.seeds)
    rev = Reviewed(Path(cfg.state_dir) / "reviewed.json")
    return q, est, rev


def _diem_day(cfg, now: datetime) -> str:
    return (next_reset(cfg, now) - timedelta(days=1)).date().isoformat()


def _drain_env(key: str, env_path: Path = Path.home() / ".env") -> dict:
    """Env for shelled-out subprocesses (council/loom/cmd). Cron runs this
    under /bin/sh, so ~/.zshenv never fires and ~/.env is never sourced —
    os.environ has no VENICE_* at all. Read them directly and pass the
    per-project keys through, or every queued subprocess falls back to the
    shared key and bills to DEFAULT instead of its own project.

    Cron's PATH is also just /usr/bin:/bin, so a bare `council` argv is
    unresolvable unless pipx's bin dir is on it — prepend it, without
    duplicating an entry that's already there."""
    env = {**os.environ}
    for name, value in _read_env(env_path).items():
        if name.startswith("VENICE_"):
            env[name] = value
    # Generic names are the fallback tier, not an override: VENICE_KEY is
    # romance's var under the per-project map and must not be clobbered when
    # ~/.env defines it.
    env.setdefault("VENICE_API_KEY", key)
    env.setdefault("VENICE_KEY", key)
    pipx_bin = str(Path.home() / ".local" / "bin")
    path = env.get("PATH", "/usr/bin:/bin")
    if pipx_bin not in path.split(":"):
        path = f"{pipx_bin}:{path}"
    env["PATH"] = path
    return env


def _cmd_drain(cfg, now: datetime) -> int:
    key = load_venice_key()
    env = _drain_env(key)
    q, est, rev = _bits(cfg)
    balance = BalanceClient(key)

    def runner(item, *, deadline_epoch):
        return run_item(item, cfg, env,
                        deadline_epoch=time.monotonic() + deadline_epoch)

    summary = run_checkpoint(cfg, now=now, balance=balance, queue=q,
                             estimates=est, reviewed=rev, runner=runner)
    day = _diem_day(cfg, now)
    jl = Path(cfg.state_dir) / "summaries" / f"{day}.jsonl"
    jl.parent.mkdir(parents=True, exist_ok=True)
    first_of_night = not jl.exists()
    with open(jl, "a") as fh:
        fh.write(json.dumps(summary) + "\n")

    if first_of_night:
        send_telegram(cfg, evening_ping(summary, cfg))
    last_cp = max(cfg.checkpoints,
                  key=lambda c: (c.time < cfg.reset, c.time))  # a post-midnight cp sorts last
    fired = _last_fired(cfg, now)
    if fired is not None and fired[1].time == last_cp.time:
        try:
            summaries = [json.loads(l) for l in jl.read_text().splitlines() if l.strip()]
            path = write_morning_report(cfg, day, summaries)
            ran = sum(len(s.get("ran", [])) for s in summaries)
            failed = sum(1 for s in summaries for r in s.get("ran", []) if not r["ok"])
            send_telegram(cfg, f"DIEM night done: {ran} job(s), {failed} failed.\n"
                               f"Report: {path}")
        except Exception as e:  # noqa: BLE001 — reporting failures must never crash the drain
            print(f"warning: morning report failed: {e}", file=sys.stderr)
    print(json.dumps(summary, indent=1))
    return 0


def _cmd_status(cfg, now: datetime) -> int:
    q, est, _ = _bits(cfg)
    try:
        bal = BalanceClient(load_venice_key()).diem_balance()
        pct = f"{100 * bal / cfg.daily_diem:.0f}%"
    except SystemExit:
        bal, pct = None, "? (no key)"
    except BalanceUnavailable:
        bal, pct = None, "? (unavailable)"
    pend = q.pending(now.isoformat(timespec="seconds"))
    banked = sum(1 for i in pend if i.banked)
    print(f"balance:  {bal} ({pct} of {cfg.daily_diem})")
    print(f"floor:    {floor_for(cfg, now):.1f}  deadline: {next_deadline(cfg, now)}"
          f"  reset: {next_reset(cfg, now)}")
    print(f"queue:    {len(pend)} pending ({banked} banked)")
    for i in pend:
        print(f"  {i.id[:8]} {'B' if i.banked else ' '} {i.type:9} {i.created}")
    return 0


def _cmd_venice_usage(cfg, now, *, days=7, as_json=False) -> int:
    """Ledger estimate against Venice's real bills, over a real N-day window.

    Two tables, because the two sides do not share the same axes. Venice's
    invoice carries no key or project tag, so the project table can only ever be
    the ledger's own estimate. The model IS shared, and it is where the useful
    finding lives: a model Venice billed that the ledger never recorded is spend
    with no local trace at all (see `venice-usage reconcile --backfill`)."""
    start = now - timedelta(days=days)
    since, until = (start.isoformat(timespec="seconds"),
                    now.isoformat(timespec="seconds"))
    projects = [{"project": r["project"], "est_diem": round(r["usd"], 4),
                 "calls": r["calls"]}
                for r in venice_usage.query_rollup(since=since, until=until,
                                                   group_by=("project",))]
    ledger_models = {r["model"]: r for r in
                     venice_usage.query_rollup(since=since, until=until,
                                               group_by=("model",))}
    billed: dict[str, dict] = {}
    warn = None
    try:
        billed = UsageClient(load_venice_admin_key()).billed_by_model(start=start,
                                                                      end=now)
    except (UsageUnavailable, SystemExit) as e:
        warn = str(e) or "venice usage unavailable"

    models = []
    for m in sorted(set(ledger_models) | set(billed)):
        led, bill = ledger_models.get(m), billed.get(m)
        # "untracked" is the claude-fable-5-1 shape: Venice billed it, nothing
        # local recorded it. "no bill" is usually benign — an image model billed
        # per asset, or a call whose invoice has not landed yet.
        note = "" if (led and bill) else ("untracked" if led is None else "no bill")
        if warn:
            note = ""
        models.append({"model": m,
                       "calls": led["calls"] if led else 0,
                       "est_diem": round(led["usd"], 4) if led else 0.0,
                       "billed_diem": None if bill is None else round(bill["diem"], 4),
                       "billed_calls": None if bill is None else bill["calls"],
                       "note": note})
    total = round(sum(c["diem"] for c in billed.values()), 4) if billed else 0.0

    if as_json:
        print(json.dumps({"days": days, "since": since, "until": until,
                          "warning": warn, "billed_diem": total,
                          "projects": projects, "models": models}, indent=1))
        return 0
    if warn:
        print(f"warning: Venice usage unavailable ({warn}) — showing ledger only")
    print(f"venice-usage (last {days}d: {since} .. {until})")
    # est is the ledger's price-table estimate, NOT billed spend. billed comes
    # from /billing/usage-history and is what the account was actually charged.
    print("est = ledger price-table estimate (notional); "
          "billed = Venice-billed DIEM for this window")
    print(f"\n{'project':16} {'calls':>7} {'est':>10}   (bills carry no project tag, "
          f"so this side is ledger-only)")
    for r in projects:
        print(f"{r['project']:16} {r['calls']:7} {r['est_diem']:10.4f}")
    print(f"\n{'model':30} {'calls':>7} {'est':>10} {'billed':>10}  note")
    for r in models:
        b = "-" if r["billed_diem"] is None else f"{r['billed_diem']:.4f}"
        print(f"{r['model']:30} {r['calls']:7} {r['est_diem']:10.4f} {b:>10}  "
              f"{r['note']}")
    if not warn:
        print(f"{'TOTAL billed':30} {'':7} {'':10} {total:10.4f}")
        lost = [r for r in models if r["note"] == "untracked"]
        if lost:
            diem = sum(r["billed_diem"] or 0.0 for r in lost)
            print(f"\n{len(lost)} model(s) Venice billed and the ledger never "
                  f"recorded: {diem:.4f} DIEM. Recover them with "
                  f"`venice-usage reconcile --since {since[:10]} --backfill`.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="diem")
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("drain")
    d.add_argument("--checkpoint", action="store_true", required=True)
    d.add_argument("--config", default=None)

    st = sub.add_parser("status"); st.add_argument("--config", default=None)

    vu = sub.add_parser("venice-usage"); vu.add_argument("--config", default=None)
    vu.add_argument("--days", type=int, default=7); vu.add_argument("--json", action="store_true")

    qp = sub.add_parser("queue"); qsub = qp.add_subparsers(dest="qcmd", required=True)
    qa = qsub.add_parser("add"); qa.add_argument("--config", default=None)
    qa.add_argument("type", choices=["ask", "review", "images", "backfill", "cmd"])
    qa.add_argument("args", nargs="*")
    qa.add_argument("--panel", default="decision"); qa.add_argument("--range")
    qa.add_argument("--expires"); qa.add_argument("--max-targets", type=int, default=2)
    qa.add_argument("--not-before", default=None, metavar="HH:MM",
                    help="leftovers only: invisible to the drain before this time of the DIEM day")
    ql = qsub.add_parser("list"); ql.add_argument("--config", default=None)
    qr = qsub.add_parser("rm"); qr.add_argument("id"); qr.add_argument("--config", default=None)

    pa = sub.add_parser("pause")
    pa.add_argument("hours", nargs="?", type=float); pa.add_argument("--config", default=None)
    re_ = sub.add_parser("resume"); re_.add_argument("--config", default=None)

    args = p.parse_args(argv)
    cfg = DiemConfig.load(Path(args.config) if args.config else None)
    now = _now()
    now_iso = now.isoformat(timespec="seconds")

    if args.cmd == "drain":
        return _cmd_drain(cfg, now)
    if args.cmd == "status":
        return _cmd_status(cfg, now)
    if args.cmd == "venice-usage":
        return _cmd_venice_usage(cfg, now, days=args.days, as_json=args.json)
    if args.cmd == "queue":
        q, _, _ = _bits(cfg)
        if args.qcmd == "add":
            payload = None
            if args.type == "ask":
                payload = {"question": " ".join(args.args), "panel": args.panel}
            elif args.type == "review":
                if not args.args:
                    print("error: queue add review requires a repo path",
                          file=sys.stderr)
                    return 2
                repo = args.args[0]
                payload = ({"repo": repo, "range": args.range,
                            "head": args.range.split("..")[-1]} if args.range
                           else {"repo": repo, "diff": True})
            elif args.type == "images":
                if len(args.args) < 2:
                    print("error: queue add images requires a repo path and a count",
                          file=sys.stderr)
                    return 2
                try:
                    count = int(args.args[1])
                except ValueError:
                    print(f"error: queue add images count must be an integer, "
                          f"got {args.args[1]!r}", file=sys.stderr)
                    return 2
                payload = {"repo": args.args[0], "count": count}
            elif args.type == "backfill":
                payload = {"max_targets": args.max_targets}
            elif args.type == "cmd":
                if not args.args:
                    print("error: queue add cmd requires a name", file=sys.stderr)
                    return 2
                payload = {"name": args.args[0]}
            if args.not_before is not None:
                # Refused here because the drain reads a garbled value as "absent", and an
                # item meant for leftovers would then take the morning allowance.
                hh, _, mm = args.not_before.partition(":")
                if not (hh.isdigit() and mm.isdigit() and len(mm) == 2
                        and int(hh) <= 23 and int(mm) <= 59):
                    print("error: --not-before must be HH:MM", file=sys.stderr)
                    return 2
            it = new_item(args.type, payload, banked=True,
                          expires=args.expires, created=now_iso,
                          not_before=args.not_before)
            added = q.add(it)
            print(it.id if added else "duplicate — not added")
            return 0 if added else 1
        if args.qcmd == "list":
            for i in q.pending(now_iso):
                print(f"{i.id[:8]} {'B' if i.banked else ' '} {i.type:9} "
                      f"{i.created}  {json.dumps(i.payload)[:60]}")
            return 0
        if args.qcmd == "rm":
            return 0 if q.remove(args.id) else 1
    if args.cmd == "pause":
        until = (now + timedelta(hours=args.hours)) if args.hours is not None \
            else next_reset(cfg, now)
        set_pause(cfg.state_dir, until.isoformat(timespec="seconds"))
        print(f"paused until {until}")
        return 0
    if args.cmd == "resume":
        clear_pause(cfg.state_dir)
        print("resumed")
        return 0
    return 1
