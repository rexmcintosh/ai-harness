"""`venice-usage` — the universal append primitive + offline rollup.
`log` is best-effort and always exits 0 — logging must never break a caller —
covering both a bad/unwritable ledger DB (append() failure) and a malformed
invocation (argparse failure); see `main()` for the latter."""
from __future__ import annotations
import argparse
import json
import sys
from . import reconcile as _reconcile
from .ledger import append, query_rollup
from .portable import export_jsonl, github_origin, ingest_jsonl

def _cmd_log(a) -> int:
    try:
        append(project=a.project, task_type=a.task_type, model=a.model,
               tokens_in=a.tokens_in, tokens_out=a.tokens_out,
               usd=a.usd, source=a.source, ts=a.ts)
    except Exception as e:  # noqa: BLE001 — append is best-effort
        print(f"venice-usage: log failed (ignored): {e}", file=sys.stderr)
    return 0

def _cmd_report(a) -> int:
    """Roll the ledger up — and say, every single time, which prices were used.

    The header is not decoration. A report that silently mixed a stale table's
    numbers with fresh ones put a 2.5x overstatement into an audit; naming the
    basis and the table's vintage on every report is the fix for that."""
    from .pricing import REFRESHED_AT, basis_line, is_stale
    gb = tuple(c.strip() for c in a.group_by.split(",") if c.strip())
    try:
        rows = query_rollup(since=a.since, until=a.until, project=a.project,
                            group_by=gb, price_basis=a.price_basis)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2
    if a.json:
        print(json.dumps({"price_basis": a.price_basis,
                          "prices_refreshed_at": REFRESHED_AT,
                          "prices_stale": is_stale(),
                          "rows": rows}, indent=1))
        return 0
    print(basis_line(a.price_basis))
    if not rows:
        print("(no usage rows)"); return 0
    unpriced = sum(r.get("unpriced_calls", 0) for r in rows)
    if unpriced:
        print(f"note: {unpriced} call(s) could not be valued on this basis and "
              f"count as 0 — they are not free")
    headers = list(gb) + ["calls", "tokens_in", "tokens_out", "usd"]
    widths = {h: max(len(h), *(len(str(r[h])) for r in rows)) for h in headers}
    print("  ".join(h.ljust(widths[h]) for h in headers))
    for r in rows:
        cells = [str(r[h]) if h != "usd" else f"{r['usd']:.4f}" for h in headers]
        print("  ".join(c.ljust(widths[h]) for c, h in zip(cells, headers)))
    return 0

def _cmd_export(a) -> int:
    """Dump this machine's ledger to a portable JSONL file.

    Built for the CI merge gate: the GitHub runner logs usage into a ledger it
    then destroys, so the job exports here and the workflow keeps the file as a
    build artifact. Best-effort like `log` — accounting must never be the reason
    a merge gate fails — unless --strict is passed."""
    try:
        origin = {"id": a.origin_id} if a.origin_id else github_origin()
        if not origin:
            raise ValueError("no --origin-id and no GitHub Actions environment "
                             "(GITHUB_REPOSITORY + GITHUB_RUN_ID) to derive one from")
        n = export_jsonl(a.output, origin=origin, since=a.since, until=a.until)
    except Exception as e:  # noqa: BLE001
        # Non-strict is the CI default: a run whose usage we failed to capture is
        # a lost row, not a bad merge. Say so loudly, exit 0.
        level = "error" if a.strict else "warning"
        print(f"venice-usage: {level}: export failed: {e}", file=sys.stderr)
        return 2 if a.strict else 0
    print(f"venice-usage: exported {n} row(s) to {a.output}", file=sys.stderr)
    return 0


def _cmd_ingest(a) -> int:
    """Merge exported artifacts back into this machine's ledger. Idempotent by
    ext_id, so re-downloading the same artifact cannot double-count spend."""
    totals = {"files": 0, "rows": 0, "inserted": 0, "skipped": 0, "malformed": 0}
    failures = []
    for target in a.paths:
        try:
            for k, v in ingest_jsonl(target).items():
                totals[k] += v
        except (ValueError, OSError) as e:
            failures.append(str(e))
    if a.json:
        print(json.dumps({**totals, "failures": failures}, indent=1))
    else:
        print("files={files} rows={rows} inserted={inserted} skipped={skipped} "
              "malformed={malformed}".format(**totals))
    for f in failures:
        print(f"error: {f}", file=sys.stderr)
    return 2 if failures else 0


def _cmd_reconcile(a) -> int:
    """Compare the ledger with Venice's bills — and recover what it never saw.

    This was implemented, tested and then reachable only as
    `/home/dev/.local/share/pipx/venvs/council/bin/python -m venice_usage.reconcile`.
    Nobody types that, so the one check that catches a mispriced ledger — and
    the only way to recover spend an OOM-killed session never logged — was in
    practice never run. It is a subcommand now, with the same flags, because a
    command that exists but cannot be reached is a command that does not exist."""
    return _reconcile.run(a)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="venice-usage")
    sub = p.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("log")
    lg.add_argument("--project", required=True)
    lg.add_argument("--task-type", required=True, dest="task_type")
    lg.add_argument("--model", required=True)
    lg.add_argument("--tokens-in", type=int, default=0, dest="tokens_in")
    lg.add_argument("--tokens-out", type=int, default=0, dest="tokens_out")
    lg.add_argument("--usd", type=float, default=None)
    lg.add_argument("--source", default=None)
    lg.add_argument("--ts", default=None)
    ex = sub.add_parser("export", help="dump the ledger to portable JSONL")
    ex.add_argument("--output", required=True)
    ex.add_argument("--origin-id", dest="origin_id", default=None,
                    help="unique id for the producing run; defaults to the "
                         "GitHub Actions repo/run/attempt when available")
    ex.add_argument("--since"); ex.add_argument("--until")
    ex.add_argument("--strict", action="store_true",
                    help="exit 2 on failure instead of the best-effort 0")
    ing = sub.add_parser("ingest", help="merge exported JSONL back into the ledger")
    ing.add_argument("paths", nargs="+", help="export files, or directories of them")
    ing.add_argument("--json", action="store_true")
    rp = sub.add_parser("report")
    rp.add_argument("--since"); rp.add_argument("--until"); rp.add_argument("--project")
    rp.add_argument("--group-by", default="project,task_type", dest="group_by")
    rp.add_argument("--price-basis", default="current", choices=("current", "stored"),
                    dest="price_basis",
                    help="current (default): value every row from the current price "
                         "table on read. stored: sum the usd column as it was written "
                         "at log time — mixed vintages, unpriced models read as 0. "
                         "Neither ever modifies a stored row.")
    rp.add_argument("--json", action="store_true")
    _reconcile.add_arguments(
        sub.add_parser("reconcile", description=_reconcile.DESCRIPTION,
                       help="compare the ledger with Venice's bills; --backfill "
                            "recovers billed calls the ledger never saw"))
    argv = sys.argv[1:] if argv is None else list(argv)
    # Deviation from brief, flagged: argparse's own validation (missing --model,
    # non-int --tokens-in, ...) calls sys.exit(2) from inside parse_args() below —
    # *before* _cmd_log's try/except ever runs. That breaks the stated contract
    # ("log ... ALWAYS exits 0 ... logging must never break a caller"): a
    # malformed call-site invocation (e.g. a shell script under `set -e` passing a
    # bad --tokens-in) would abort the caller. Catch that one extra failure mode
    # for `log` specifically; `report`'s argparse/ValueError exit codes are
    # intentionally untouched — report is a diagnostic command, not the
    # best-effort primitive.
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        if argv[:1] == ["log"] and e.code not in (0, None):
            print("venice-usage: log failed (ignored): bad arguments", file=sys.stderr)
            return 0
        raise
    return {"log": _cmd_log, "export": _cmd_export, "ingest": _cmd_ingest,
            "report": _cmd_report, "reconcile": _cmd_reconcile}[a.cmd](a)

if __name__ == "__main__":
    raise SystemExit(main())
