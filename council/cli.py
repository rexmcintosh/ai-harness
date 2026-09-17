from __future__ import annotations
import argparse
import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

from .config import load_panels, get_api_key, Settings, truncate, resolve_budget
from .venice import VeniceClient
from .engine import run_panel
from .router import pick_panel
from .synthesize import synthesize
from .render import render_markdown, render_terminal


def _build(panels_path=None):
    # Config loads without any network/secret; the client is built lazily so
    # local-only commands (e.g. `panels`) don't require VENICE_API_KEY.
    settings, panels = load_panels(panels_path)
    return settings, panels, None


def _gather_context(question: str, files: list[str], cap: int) -> str:
    parts = [question] if question else []
    for fp in files or []:
        if fp == "-":
            parts.append("--- stdin ---\n" + sys.stdin.read())
        else:
            try:
                parts.append(f"--- {fp} ---\n" + Path(fp).read_text(errors="ignore"))
            except OSError as e:
                print(f"error: cannot read --file {fp}: {e}", file=sys.stderr)
                raise SystemExit(2)
    return truncate("\n\n".join(parts), cap)


def _looks_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(4096)
    except OSError:
        return True


def _read_for_review(path_arg: str, cap: int) -> str:
    """Read a file or directory into review text. Skips dotfiles (.env/.git),
    binary and unreadable files, and enforces the byte budget DURING collection
    so a huge tree can't build a giant string before truncation."""
    pth = Path(path_arg)
    files = sorted(pth.rglob("*")) if pth.is_dir() else [pth]
    parts, used = [], 0
    for f in files:
        if f.is_symlink():
            continue  # never follow a symlink out of the tree to the API
        if not f.is_file() or any(p.startswith(".") for p in f.parts):
            continue
        if _looks_binary(f):
            continue
        try:
            # Bounded read: never pull more than the whole budget from one file,
            # so a single multi-GB file can't exhaust memory before truncation.
            with open(f, "r", errors="ignore") as fh:
                content = fh.read(cap + 1)
        except OSError:
            continue
        chunk = f"--- {f} ---\n{content}"
        parts.append(chunk)
        used += len(chunk.encode("utf-8", errors="ignore"))
        if used >= cap:
            parts.append(f"\n... [stopped collecting at {cap} bytes] ...")
            break
    return "\n\n".join(parts)


def _run(context, panel_name, settings, panels, client, rigor, fmt, *, task_type="chat"):
    if panel_name is None:
        panel_name = pick_panel(context, panels, client,
                                router_model=settings.router_model,
                                default=settings.default_panel, task_type=task_type,
                                max_completion_tokens=settings.router_max_completion_tokens)
    if panel_name not in panels:
        print(f"error: unknown panel '{panel_name}'. Available: "
              f"{', '.join(panels)}.", file=sys.stderr)
        raise SystemExit(2)
    panel = panels[panel_name]
    rigor = rigor or panel.default_rigor
    # --rigor now buys something beyond rendering: `deep` gets a bigger output
    # ceiling for both the seats and the chair (see [settings.rigor.deep]).
    budget = resolve_budget(settings, panel, rigor)
    results = run_panel(panel, context, client, task_type=task_type, budget=budget)
    syn = synthesize(context, results, client, chair_model=settings.chair_model,
                     task_type=task_type, max_completion_tokens=budget.chair)
    render = render_markdown if fmt == "md" else render_terminal
    print(f"[panel: {panel_name} · rigor: {rigor}]\n")
    print(render(context[:120], syn, results, rigor=rigor))
    return 0


def main(argv=None, *, _settings: Settings = None, _panels=None, _client=None) -> int:
    p = argparse.ArgumentParser(prog="council")
    p.add_argument("--version", action="version", version=f"%(prog)s {_pkg_version('council')}")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask", help="ask the council a question")
    a.add_argument("question")
    a.add_argument("--panel"); a.add_argument("--file", action="append")
    a.add_argument("--rigor", choices=["daily", "deep"]); a.add_argument("--format", default="term")
    a.add_argument("--panels")

    r = sub.add_parser("review", help="review a file / dir / diff")
    r.add_argument("path", nargs="?")
    r.add_argument("--diff", action="store_true")
    r.add_argument("--panel", default=None)
    r.add_argument("--rigor", choices=["daily", "deep"]); r.add_argument("--format", default="term")
    r.add_argument("--panels")

    c = sub.add_parser("compare", help="rank N candidate solutions, pick a winner")
    c.add_argument("--task", required=True, help="what the candidates are trying to do")
    c.add_argument("files", nargs="+", help="two or more candidate files")
    c.add_argument("--panel", default="code-review")
    c.add_argument("--format", default="term"); c.add_argument("--panels")

    sw = sub.add_parser("sweep", help="repo-wide security sweep")
    sw.add_argument("path", help="file or directory to sweep")
    sw.add_argument("--panel", default="red-team")
    sw.add_argument("--max-chunks", type=int, default=40)
    sw.add_argument("--min-conf", type=int, default=7)
    sw.add_argument("--format", default="term"); sw.add_argument("--panels")

    sub.add_parser("panels", help="list councils").add_argument("--panels", nargs="?")

    args = p.parse_args(argv)

    if _settings is not None:
        settings, panels, client = _settings, _panels, _client
    else:
        settings, panels, client = _build(getattr(args, "panels", None))

    if args.cmd == "panels":
        for name, panel in panels.items():
            seats = ", ".join(m.name for m in panel.members)
            print(f"{name:14} {panel.description}\n{'':14} seats: {seats}")
        return 0

    # ask / review actually call Venice — build the client now (needs the key).
    if client is None:
        # The client-level ceiling is the backstop: every call this process makes
        # is bounded even if a call site forgets to pass its own.
        client = VeniceClient(get_api_key(), timeout=settings.timeout,
                              max_completion_tokens=settings.max_completion_tokens)

    if args.cmd == "ask":
        ctx = _gather_context(args.question, args.file, settings.byte_cap)
        return _run(ctx, args.panel, settings, panels, client, args.rigor, args.format,
                    task_type="ask")

    if args.cmd == "review":
        import subprocess
        explicit_path = False
        if args.diff:
            try:
                proc = subprocess.run(["git", "diff"], capture_output=True,
                                      text=True, timeout=60)
            except subprocess.TimeoutExpired:
                print("error: `git diff` timed out", file=sys.stderr)
                return 2
            if proc.returncode != 0:
                print(f"error: `git diff` failed: {proc.stderr.strip()}", file=sys.stderr)
                return 2
            text = proc.stdout
        elif args.path == "-" or args.path is None:
            text = sys.stdin.read()
        else:
            explicit_path = True
            text = _read_for_review(args.path, settings.byte_cap)
        if not text.strip():
            # An explicit path that matched nothing is likely an operator typo —
            # fail (exit 2) instead of silently passing. A clean `git diff` /
            # empty stdin is legitimately "nothing to do" → exit 0.
            if explicit_path:
                print(f"error: nothing readable to review at '{args.path}'.", file=sys.stderr)
                return 2
            print("Nothing to review (empty diff / no input).", file=sys.stderr)
            return 0
        ctx = truncate(f"Review this:\n\n{text}", settings.byte_cap)
        panel_name = args.panel
        if panel_name is None:
            # auto-pick by target: a single doc file -> spec-review; otherwise code-review.
            # (dirs / --diff / stdin span many files; the CI front-end splits those.)
            from .routing import classify_path
            if not args.diff and args.path not in (None, "-") and Path(args.path).is_file() \
                    and classify_path(args.path) == "doc":
                panel_name = "spec-review"
            else:
                panel_name = "code-review"
        return _run(ctx, panel_name, settings, panels, client, args.rigor, args.format)

    if args.cmd == "compare":
        from .compare import run_compare
        if len(args.files) < 2:
            print("error: compare needs at least two candidate files.", file=sys.stderr)
            return 2
        if args.panel not in panels:
            print(f"error: unknown panel '{args.panel}'. Available: "
                  f"{', '.join(panels)}.", file=sys.stderr)
            return 2
        candidates, seen = [], {}
        for fp in args.files:
            try:
                text = Path(fp).read_text(errors="ignore")
            except OSError as e:
                print(f"error: cannot read {fp}: {e}", file=sys.stderr)
                return 2
            label = Path(fp).name
            if label in seen:  # disambiguate duplicate basenames
                seen[label] += 1
                label = f"{label}#{seen[label]}"
            else:
                seen[label] = 1
            candidates.append((label, truncate(text, settings.byte_cap // len(args.files))))
        cmp_panel = panels[args.panel]
        res = run_compare(args.task, candidates, cmp_panel, client,
                          chair_model=settings.chair_model,
                          budget=resolve_budget(settings, cmp_panel,
                                                cmp_panel.default_rigor))
        from .render import render_comparison
        print(f"[compare · panel: {args.panel} · {len(candidates)} candidates]\n")
        print(render_comparison(args.task, res))
        return 0

    if args.cmd == "sweep":
        from .sweep import chunk_repo, run_sweep
        from .render import render_sweep
        if args.panel not in panels:
            print(f"error: unknown panel '{args.panel}'. Available: "
                  f"{', '.join(panels)}.", file=sys.stderr)
            return 2
        chunks, dropped = chunk_repo(args.path, cap=settings.byte_cap,
                                     max_chunks=args.max_chunks)
        if not chunks:
            print(f"Nothing to scan at '{args.path}'.")
            return 0
        sweep_panel = panels[args.panel]
        report = run_sweep(chunks, sweep_panel, client,
                           chair_model=settings.chair_model, min_conf=args.min_conf,
                           budget=resolve_budget(settings, sweep_panel,
                                                 sweep_panel.default_rigor))
        report.dropped = dropped
        print(f"[sweep · panel: {args.panel} · {report.chunks_scanned} files]\n")
        print(render_sweep(args.path, report))
        return 0

    return 1


if __name__ == "__main__":  # `python -m council.cli ...` runs the same as the console script
    raise SystemExit(main())
