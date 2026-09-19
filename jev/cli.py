"""`jev`: the door for callers that are not Python (Node scripts, shell). One JSON line out
per request, `{"error": ...}` with exit 1 on failure, never a traceback, never the key.

  jev ask   --project P --task T [--model M]            < {"state": ..., "questions": {...}}
  jev batch --project P --task T --questions q.json      < JSONL of {"id", "state"[, "questions"]}
  jev usage [--days N]
  jev doctor
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import client, usage


def _emit(stdout, obj) -> None:
    stdout.write(json.dumps(obj) + "\n")


def _ask(args, stdin, stdout) -> int:
    try:
        req = json.loads(stdin.read())
        got = client.ask(req["state"], req["questions"], project=args.project, task=args.task, model=args.model)
    except Exception as err:  # noqa: BLE001
        _emit(stdout, {"error": f"{type(err).__name__}: {str(err)[:300]}"})
        return 1
    _emit(stdout, got)
    return 0


def _batch(args, stdin, stdout) -> int:
    try:
        shared = json.loads(open(args.questions).read()) if args.questions else None
    except Exception as err:  # noqa: BLE001
        _emit(stdout, {"error": f"cannot read --questions: {str(err)[:200]}"})
        return 1
    items = []
    for n, line in enumerate(l for l in stdin.read().splitlines() if l.strip()):
        try:
            item = json.loads(line)
            item["state"], item["id"]        # both required
            items.append(item)
        except Exception as err:  # noqa: BLE001
            items.append({"id": None, "_bad": f"line {n + 1}: {type(err).__name__}: {str(err)[:120]}"})
    good = [(n, i) for n, i in enumerate(items) if "_bad" not in i]
    answers = client.ask_many([i for _, i in good], lambda item: item.get("questions") or shared,
                              project=args.project, task=args.task, model=args.model, workers=args.workers)
    if len(answers) != len(good):
        raise RuntimeError(f"asked {len(good)} items, got {len(answers)} answers")
    by_line = {n: answer for (n, _), answer in zip(good, answers)}
    for n, item in enumerate(items):       # one output line per input line, in order
        _emit(stdout, by_line[n] if n in by_line else {"id": None, "error": item["_bad"]})
    return 0


def _usage(args, stdout) -> int:
    since = int(time.time()) - args.days * 86400 if args.days else 0
    rows = sorted(usage.summarize(since_epoch=since).items(), key=lambda kv: -kv[1]["cost_usd"])
    stdout.write(f"{'project':24s} {'task':24s} {'calls':>7s} {'errors':>7s} {'tokens':>11s} {'usd':>9s}\n")
    for (project, task), s in rows:
        stdout.write(f"{project:24s} {task:24s} {s['calls']:7d} {s['errors']:7d} {s['input_tokens']:11d} {s['cost_usd']:9.4f}\n")
    total = sum(s["cost_usd"] for _, s in rows)
    stdout.write(f"{'total':24s} {'':24s} {sum(s['calls'] for _, s in rows):7d} {'':7s} {'':11s} {total:9.4f}\n")
    return 0


def _doctor(stdout) -> int:
    report = {"model": client.MODEL, "key_present": bool(client.load_key()), "disabled": client.disabled(),
              "usage_log": str(usage.log_path())}
    got = client.try_ask("The build finished with exit code 0.", {"ok": client.noul("Did the build succeed?")},
                         project="jev", task="doctor")
    report["live_call"] = {"ok": bool(got), **({"seconds": got["seconds"], "answer": got["answers"]["ok"]["noul"]} if got else {})}
    _emit(stdout, report)
    return 0 if got or client.disabled() else 1


def main(argv=None, *, stdin=None, stdout=None, stderr=None) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    parser = argparse.ArgumentParser(prog="jev", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("ask", "batch"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
        p.add_argument("--task", required=True)
        p.add_argument("--model", default=None, help="pin a version for a measured gate (default: the shared pin)")
        if name == "batch":
            p.add_argument("--questions", help="JSON file of questions shared by every item")
            p.add_argument("--workers", type=int, default=8)
    sub.add_parser("usage").add_argument("--days", type=int, default=0)
    sub.add_parser("doctor")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "ask":
            return _ask(args, stdin, stdout)
        if args.cmd == "batch":
            return _batch(args, stdin, stdout)
        if args.cmd == "usage":
            return _usage(args, stdout)
        return _doctor(stdout)
    except BaseException as err:  # noqa: BLE001 - the contract is one JSON line, never a traceback
        if isinstance(err, (KeyboardInterrupt, SystemExit)):
            raise
        _emit(stdout, {"error": client._scrub(f"{type(err).__name__}: {err}", client.load_key())})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
