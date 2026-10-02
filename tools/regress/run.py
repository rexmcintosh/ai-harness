#!/usr/bin/env python3
"""Run the council v0.4.0 known-false golden set in a fresh environment."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import importlib.util
import json
from importlib.resources import files
import os
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

try:
    from . import harness
except ImportError:  # Direct execution keeps this directory on sys.path.
    import harness  # type: ignore[no-redef]

PINNED_REF = "council-v0.4.0"
PINNED_VERSION = "0.4.0"
PINNED_COMMIT = "4a01298dcce2734115ac57f02592146969d76f48"
ESTIMATED_API_CALLS = 16
EXPECTED_FIXTURE_IDS = ("aris-pr1", "stw-pr11")
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
FIXTURES = HERE / "fixtures"


def _golden_fixtures():
    """The golden set, loaded BY NAME rather than by listing the directory.

    `fixtures/` is no longer exclusively this script's: the chair bake-off
    (`bakeoff.py`) keeps `baw-pr11` there, and a directory listing would sweep
    it into the pinned v0.4.0 regression, change the matrix from 4 cells to 6,
    and fail `_validate_golden_set` before a single call. Naming the set keeps
    this run's contract fixed no matter what else is stored alongside it.
    """
    return [harness.load_fixture(FIXTURES / fixture_id) for fixture_id in EXPECTED_FIXTURE_IDS]


def _load_hook(specification: str | None):
    if not specification:
        return None
    path_text, separator, callable_name = specification.rpartition(":")
    path = Path(path_text)
    if not separator or not path.is_absolute() or not callable_name or not path.is_file():
        raise ValueError("--transport-hook must be /absolute/file.py:callable")
    module_spec = importlib.util.spec_from_file_location("council_regress_transport", path)
    if module_spec is None or module_spec.loader is None:
        raise ValueError(f"cannot load transport hook: {path}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    hook = getattr(module, callable_name, None)
    if not callable(hook):
        raise ValueError(f"transport hook is not callable: {callable_name}")
    return hook


def _env_assignment(path: Path, name: str) -> str | None:
    if not path.is_file():
        return None
    prefix = name + "="
    for raw_line in path.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if not line.startswith(prefix):
            continue
        value = line[len(prefix):].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        return value or None
    return None


def _resolve_key() -> str:
    for name in ("VENICE_COUNCIL_KEY", "VENICE_API_KEY"):
        value = os.environ.get(name)
        if value:
            return value
    env_file = Path.home() / ".env"
    for name in ("VENICE_COUNCIL_KEY", "VENICE_API_KEY"):
        value = _env_assignment(env_file, name)
        if value:
            return value
    raise RuntimeError("VENICE_COUNCIL_KEY or VENICE_API_KEY is required for --paid")


def _load_pinned_council():
    installed = importlib.metadata.version("council")
    if installed != PINNED_VERSION:
        raise RuntimeError(f"expected council {PINNED_VERSION}, found {installed}")
    from council.config import load_panels
    from council.review import run_pr_review

    bundled_panels = files("council").joinpath("panels.toml")
    settings, panels = load_panels(str(bundled_panels))
    return settings, panels, run_pr_review


def _build_client(settings, transport_hook):
    from council.venice import VeniceClient

    return VeniceClient(
        _resolve_key(), timeout=settings.timeout, retries=0, post=_load_hook(transport_hook)
    )


def _safe_error(exc: Exception) -> str:
    try:
        message = f"{type(exc).__name__}: {exc}"
        secrets = [os.environ.get("VENICE_COUNCIL_KEY"), os.environ.get("VENICE_API_KEY")]
        env_file = Path.home() / ".env"
        secrets.extend(_env_assignment(env_file, name) for name in ("VENICE_COUNCIL_KEY", "VENICE_API_KEY"))
        for secret in secrets:
            if secret:
                message = message.replace(secret, "<redacted>")
        return message
    except Exception:
        # Never return unredacted details when credential lookup or formatting fails.
        return f"{type(exc).__name__}: details omitted because credential redaction failed"


def _base_record(case, error: str | None = None) -> dict:
    fixture = case.fixture
    return {
        "fixture": fixture.fixture_id,
        "grounding": case.grounding,
        "repo": fixture.repo,
        "pr": fixture.pr,
        "base": fixture.base,
        "head": fixture.head,
        "expected_blocking": fixture.expected_blocking,
        "blocking": None,
        "unavailable": True,
        "error": error,
        "body": "",
    }


def _result_payload(records: list[dict], bootstrap_error: dict | None = None) -> dict:
    grade = harness.grade_records(records)
    payload = {
        "schema_version": 1,
        "mode": "paid",
        "council_ref": PINNED_REF,
        "council_version": PINNED_VERSION,
        "council_commit": PINNED_COMMIT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claim": "single stochastic regression detection run; not effect estimation",
        "estimated_api_calls": ESTIMATED_API_CALLS,
        "verdict": "fail" if bootstrap_error else grade["verdict"],
        "runs": records,
    }
    if bootstrap_error:
        payload["bootstrap_error"] = bootstrap_error
    return payload


def _write_bootstrap_failure(output: Path, phase: str, exc: Exception, fixtures=()) -> None:
    error = _safe_error(exc)
    records = [_base_record(case, error) for case in harness.build_run_matrix(fixtures)]
    harness.write_evidence(output, _result_payload(records, {"phase": phase, "error": error}))

def worker(*, output: Path, transport_hook: str | None) -> int:
    fixtures = []
    try:
        fixtures = _golden_fixtures()
        _validate_golden_set(fixtures)
        settings, panels, run_pr_review = _load_pinned_council()
        client = _build_client(settings, transport_hook)
    except Exception as exc:
        _write_bootstrap_failure(output, "worker-bootstrap", exc, fixtures)
        return 1

    records: list[dict] = []
    for case in harness.build_run_matrix(fixtures):
        fixture = case.fixture
        record = _base_record(case)
        try:
            context = harness.gather_file_context(fixture) if case.grounding == "full-file-context" else ""
            body, blocking, unavailable = run_pr_review(
                fixture.diff_path.read_text(), panels, client,
                chair_model=getattr(settings, "chair_model", ""), file_context=context,
            )
            record.update(body=body, blocking=blocking, unavailable=unavailable)
        except Exception as exc:  # Preserve all four cells and fail closed.
            record["error"] = _safe_error(exc)
        records.append(record)
    payload = _result_payload(records)
    harness.write_evidence(output, payload)
    return 0 if payload["verdict"] == "pass" else 1


def install_and_run(*, output: Path, transport_hook: str | None, estimate_diem: float) -> int:
    with tempfile.TemporaryDirectory(prefix="council-v040-regress-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = environment / "bin" / "python"
        resolved = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", f"{PINNED_REF}^{{commit}}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if resolved != PINNED_COMMIT:
            raise RuntimeError(f"{PINNED_REF} does not resolve to the recorded commit")
        source = f"git+file://{REPO_ROOT}@{PINNED_COMMIT}"
        subprocess.run([str(python), "-m", "pip", "install", source], check=True)
        command = [
            str(python), str(Path(__file__).resolve()), "--_worker", "--paid",
            "--estimate-diem", str(estimate_diem), "--output", str(output),
        ]
        if transport_hook:
            command.extend(["--transport-hook", transport_hook])
        clean_env = os.environ.copy()
        clean_env.pop("PYTHONPATH", None)
        return subprocess.run(command, env=clean_env, check=False).returncode


def _validate_golden_set(fixtures) -> int:
    fixture_ids = tuple(fixture.fixture_id for fixture in fixtures)
    if fixture_ids != EXPECTED_FIXTURE_IDS:
        raise ValueError(f"expected fixtures {EXPECTED_FIXTURE_IDS}, found {fixture_ids}")
    for fixture in fixtures:
        harness.validate_fixture(fixture)
        harness.gather_file_context(fixture)
    cells = len(harness.build_run_matrix(fixtures))
    if cells != 4:
        raise ValueError(f"expected 4 matrix cells, found {cells}")
    return cells


def _dry_run(output: Path) -> int:
    fixtures = _golden_fixtures()
    cells = _validate_golden_set(fixtures)
    payload = {
        "schema_version": 1,
        "mode": "dry-run",
        "council_ref": PINNED_REF,
        "council_commit": PINNED_COMMIT,
        "fixture_count": len(fixtures),
        "matrix_cells": cells,
        "estimated_api_calls": ESTIMATED_API_CALLS,
        "api_calls_made": 0,
        "verdict": "pass",
    }
    harness.write_evidence(output, payload)
    print(f"Dry run passed: {len(fixtures)} fixtures, {cells} matrix cells, 0 API calls.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate fixtures with zero API calls")
    mode.add_argument("--paid", action="store_true", help="explicitly permit the paid Venice run")
    parser.add_argument("--estimate-diem", type=float, help="operator's current estimate; not a billing cap")
    parser.add_argument("--output", type=Path, default=HERE / "results" / "latest.json")
    parser.add_argument("--transport-hook", help="inject /absolute/file.py:callable as HTTP post")
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._worker:
        if not args.paid:
            print("error: the internal worker also requires --paid", file=sys.stderr)
            return 2
        if args.estimate_diem is None or not math.isfinite(args.estimate_diem) or args.estimate_diem <= 0:
            print("error: the internal worker also requires positive --estimate-diem", file=sys.stderr)
            return 2
        return worker(output=args.output, transport_hook=args.transport_hook)
    if args.dry_run:
        try:
            return _dry_run(args.output)
        except Exception as exc:
            print(f"error: dry-run validation failed: {exc}", file=sys.stderr)
            return 1
    if not args.paid:
        print("error: a live regression requires the explicit --paid flag", file=sys.stderr)
        return 2
    if args.estimate_diem is None or not math.isfinite(args.estimate_diem) or args.estimate_diem <= 0:
        print("error: --paid requires a positive --estimate-diem from current pricing", file=sys.stderr)
        return 2
    print(
        f"Preflight: {ESTIMATED_API_CALLS} paid API calls, {args.estimate_diem:.2f} DIEM "
        "operator estimate, not a billing cap. This is one stochastic run."
    )
    sys.stdout.flush()
    previous_evidence = args.output.read_bytes() if args.output.is_file() else None
    try:
        exit_code = install_and_run(
            output=args.output, transport_hook=args.transport_hook, estimate_diem=args.estimate_diem
        )
    except Exception as exc:
        _write_bootstrap_failure(args.output, "environment-bootstrap", exc)
        print("error: paid-run environment bootstrap failed; see JSON evidence", file=sys.stderr)
        return 1
    if exit_code:
        try:
            current_evidence = args.output.read_bytes()
            payload = json.loads(current_evidence)
            has_new_failure = (
                current_evidence != previous_evidence
                and isinstance(payload, dict)
                and payload.get("verdict") == "fail"
            )
        except (OSError, ValueError, TypeError):
            has_new_failure = False
        if not has_new_failure:
            _write_bootstrap_failure(
                args.output, "worker-startup", RuntimeError(f"worker exited with status {exit_code}")
            )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
