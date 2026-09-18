#!/usr/bin/env python3
"""Chair bake-off: replay one recorded panel against several candidate chairs.

Implements `docs/chair-bakeoff-design-2026-09-11.md`. Only the CHAIR is called.
The panel is replayed verbatim from `tools/regress/panels/<fixture>.json`, so
every candidate arbitrates the identical panel and no member tokens are bought.

Why this is a separate module from `run.py`, and not three flags bolted onto it
------------------------------------------------------------------------------
`run.py` is the pinned v0.4.0 regression: it asserts `council == 0.4.0`, asserts
the golden set is exactly `("aris-pr1", "stw-pr11")`, asserts a 4-cell matrix,
and grades on `blocking == expected_blocking`. Every one of those is load-bearing
for what that script claims. The bake-off needs a third fixture, a chair-model
dimension, repeats, no panel calls, and grading on the chair's own JSON — and it
needs council at HEAD, because the rubric reads `Synthesis.raw_response` and
`Synthesis.review_status`, neither of which exists at v0.4.0. Sharing `harness`
keeps the fixture hashes, the grounding-context construction and the atomic
evidence writer identical; forking the runner keeps the regression's contract
intact.

Grading the chair, not the gate
-------------------------------
Axis 1 is scored on the chair's own `blocking_findings`, with the gate's
`decide_blocking` count recorded next to it. That is not a softening of the
design — it is the design's own instruction for Case C ("the count is a
tripwire; the actual grade ... reads which findings were named") applied to all
three cases, and it is forced by a fact the design did not check: `stw-pr11`
and `baw-pr11` both change only developer-tooling paths, so `risk_tier` is
"reduced", no panel finding in either is `critical`, and `decide_blocking`
therefore returns 0 for every candidate no matter what the chair says.
(True of the gate as it stood on 2026-09-12. The reduced bar has admitted a
high at confidence >= 8 since docs/council-gate-policy-2026-09-18.md; the saved
runs are replayed under that bar in tests/test_gate_policy_replay.py.)

Usage
-----
    python3 tools/regress/bakeoff.py --dry-run --output /tmp/bakeoff-dry.json
    python3 tools/regress/bakeoff.py --paid --estimate-diem 2.70 --repeats 3 \
        --chair-model claude-opus-4-8 --chair-model claude-sonnet-5 \
        --transport-hook /abs/path/tools/budget_transport.py:post \
        --output tools/regress/results/chair-bakeoff-<stamp>.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time

try:
    from . import harness
except ImportError:  # Direct execution keeps this directory on sys.path.
    import harness  # type: ignore[no-redef]

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
FIXTURES = HERE / "fixtures"
PANELS = HERE / "panels"
BAKEOFF_FIXTURE_IDS = ("aris-pr1", "stw-pr11", "baw-pr11")

# Council at HEAD, from this checkout — NOT the pinned v0.4.0 `run.py` installs.
# The rubric reads `Synthesis.raw_response` and `Synthesis.review_status`; both
# were added after that tag, so v0.4.0 cannot be graded. `council_provenance()`
# records exactly which tree answered, in the evidence file.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def council_provenance() -> dict:
    import council

    try:
        import subprocess

        commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        commit = ""
    return {"path": council.__file__, "commit": commit,
            "version": getattr(council, "__version__", "")}


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------

def _load_hook(specification: str | None):
    """Same contract as run.py: /absolute/file.py:callable."""
    if not specification:
        return None
    path_text, separator, callable_name = specification.rpartition(":")
    path = Path(path_text)
    if not separator or not path.is_absolute() or not callable_name or not path.is_file():
        raise ValueError("--transport-hook must be /absolute/file.py:callable")
    module_spec = importlib.util.spec_from_file_location("council_bakeoff_transport", path)
    if module_spec is None or module_spec.loader is None:
        raise ValueError(f"cannot load transport hook: {path}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    hook = getattr(module, callable_name, None)
    if not callable(hook):
        raise ValueError(f"transport hook is not callable: {callable_name}")
    return hook, module


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


def _safe_error(exc: Exception) -> str:
    try:
        message = f"{type(exc).__name__}: {exc}"
        env_file = Path.home() / ".env"
        secrets = [os.environ.get(n) for n in ("VENICE_COUNCIL_KEY", "VENICE_API_KEY")]
        secrets += [_env_assignment(env_file, n) for n in ("VENICE_COUNCIL_KEY", "VENICE_API_KEY")]
        for secret in secrets:
            if secret:
                message = message.replace(secret, "<redacted>")
        return message
    except Exception:
        return f"{type(exc).__name__}: details omitted because credential redaction failed"


# --------------------------------------------------------------------------
# Panel replay
# --------------------------------------------------------------------------

def load_panel(fixture_id: str):
    """Recorded `list[MemberResult]` for one fixture. No API call."""
    from council.models import Finding, MemberResult

    payload = json.loads((PANELS / f"{fixture_id}.json").read_text())
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"replay panel for {fixture_id} is empty or not a list")
    results = []
    for member in payload:
        results.append(MemberResult(
            member=str(member["member"]), model=str(member["model"]),
            stance=str(member["stance"]), headline=str(member.get("headline", "")),
            error=member.get("error"),
            findings=[Finding(point=str(f["point"]), severity=str(f["severity"]),
                              confidence=int(f["confidence"]))
                      for f in member.get("findings", [])],
            suggestions=[str(s) for s in member.get("suggestions", [])],
        ))
    return results


def chair_prompt(fixture, panel):
    """The exact (system, user) the chair is sent — built by council's own code."""
    from council.prompts import REVIEW_SYNTH_OUTPUT
    from council.review import _code_context
    from council.routing import split_diff_by_type
    from council.synthesize import _panel_digest

    code_diff, _ = split_diff_by_type(fixture.diff_path.read_text())
    context = _code_context(code_diff, harness.gather_file_context(fixture))
    user = (f"ORIGINAL INPUT:\n{context}\n\n"
            f"PANELIST ANSWERS (they answered independently, blind to each other):\n"
            f"{_panel_digest(panel)}")
    return REVIEW_SYNTH_OUTPUT, user, context, code_diff


def jsonparse_branch(raw: str) -> str:
    """Which arm of `jsonparse.loads_lenient` would recover this response.

    Axis 3 of the rubric. Computed from the recorded text rather than by
    monkeypatching, so it is reproducible from the evidence file alone.
    """
    import json as _json
    import re

    if not isinstance(raw, str) or not raw.strip():
        return "empty"
    text = raw.strip()
    try:
        _json.loads(text)
        return "strict"
    except ValueError:
        pass
    match = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE).search(text)
    if match:
        try:
            _json.loads(match.group(1).strip())
            return "fence"
        except ValueError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            _json.loads(text[start:end + 1])
            return "brace"
        except ValueError:
            pass
    return "unparseable"


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------

def _cells(fixtures, chair_models, repeats):
    return [(fixture, model, index)
            for fixture in fixtures for model in chair_models
            for index in range(1, repeats + 1)]


def _blocking_payload(syn):
    return [{"point": b.point, "severity": b.severity, "why": b.why}
            for b in syn.blocking_findings]


def _disagreement_payload(syn):
    return [{"topic": d.topic, "type": d.type, "positions": d.positions,
             "resolution": d.resolution} for d in syn.disagreements]


def run(*, chair_models, repeats, output: Path, transport_hook: str | None) -> int:
    from council.config import load_panels, resolve_budget
    from council.gate import candidate_findings, decide_blocking, risk_tier
    from council.routing import changed_paths
    from council.synthesize import synthesize
    from council.venice import VeniceClient

    settings, panels = load_panels()
    panel_config = panels["code-review"]
    budget = resolve_budget(settings, panel_config, panel_config.default_rigor)

    fixtures = [harness.load_fixture(FIXTURES / fid) for fid in BAKEOFF_FIXTURE_IDS]
    prepared = {}
    for fixture in fixtures:
        harness.validate_fixture(fixture)
        panel = load_panel(fixture.fixture_id)
        system, user, context, code_diff = chair_prompt(fixture, panel)
        tier = risk_tier(changed_paths(code_diff))
        prepared[fixture.fixture_id] = {
            "panel": panel, "system": system, "user": user, "context": context,
            "tier": tier, "candidates": len(candidate_findings(panel, tier=tier)),
        }

    hook = module = None
    if transport_hook:
        hook, module = _load_hook(transport_hook)
    # The hook really does call Venice, so its usage rows are real (venice_usage
    # .guard refuses them otherwise, and this run must be visible in the ledger).
    client = VeniceClient(_resolve_key(), timeout=settings.timeout, temperature=0,
                          max_completion_tokens=settings.max_completion_tokens,
                          post=hook, transport_is_real=hook is not None)

    records = []
    cells = _cells(fixtures, chair_models, repeats)
    for number, (fixture, chair_model, repeat) in enumerate(cells, start=1):
        ready = prepared[fixture.fixture_id]
        print(f"[{number}/{len(cells)}] {fixture.fixture_id} · {chair_model} · run {repeat}",
              flush=True)
        record = {
            "fixture": fixture.fixture_id, "chair_model": chair_model, "repeat": repeat,
            "tier": ready["tier"], "panel_candidates": ready["candidates"],
            "expected_blocking": fixture.expected_blocking,
            "prompt_bytes": len(ready["user"].encode()) + len(ready["system"].encode()),
            "error": None,
        }
        started = time.monotonic()
        try:
            # Exactly council/review.py's chair call; `synthesize` rebuilds
            # `ready["user"]` from this context and this panel.
            syn = synthesize(ready["context"], ready["panel"], client,
                             chair_model=chair_model, system=ready["system"],
                             task_type="review", max_completion_tokens=budget.chair)
            record["seconds"] = round(time.monotonic() - started, 2)
            record.update(
                chair_error=syn.error,
                raw_response=syn.raw_response,
                jsonparse_branch=jsonparse_branch(syn.raw_response),
                recommendation=syn.recommendation,
                confidence=syn.confidence,
                consensus=list(syn.consensus),
                disagreements=_disagreement_payload(syn),
                blocking_findings=_blocking_payload(syn),
                review_status=syn.review_status,
                required_changes=syn.required_changes,
                blocking=decide_blocking(ready["panel"], syn, tier=ready["tier"]),
            )
        except Exception as exc:  # Preserve every other cell.
            record["seconds"] = round(time.monotonic() - started, 2)
            record["error"] = _safe_error(exc)
            print(f"    ! {record['error']}", file=sys.stderr, flush=True)
        records.append(record)
        harness.write_evidence(output, _payload(records, chair_models, repeats, prepared, module))
    harness.write_evidence(output, _payload(records, chair_models, repeats, prepared, module))
    return 0


def _payload(records, chair_models, repeats, prepared, module) -> dict:
    spend = dict(getattr(module, "STATE", {})) if module else {}
    return {
        "schema_version": 1,
        "mode": "paid",
        "experiment": "chair bake-off (docs/chair-bakeoff-design-2026-09-11.md)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claim": "detection, not effect estimation: 3 fixtures x N repeats, one chair each",
        "chair_models": list(chair_models),
        "repeats": repeats,
        "panel_replayed": True,
        "council": council_provenance(),
        "fixtures": {fid: {"tier": v["tier"], "panel_candidates": v["candidates"],
                           "chair_prompt_bytes": len(v["user"].encode()) + len(v["system"].encode())}
                     for fid, v in prepared.items()},
        "transport_spend": spend,
        "runs": records,
    }


def dry_run(*, chair_models, repeats, output: Path) -> int:
    from council.config import load_panels, resolve_budget
    from council.gate import candidate_findings, risk_tier
    from council.routing import changed_paths

    settings, panels = load_panels()
    panel_config = panels["code-review"]
    budget = resolve_budget(settings, panel_config, panel_config.default_rigor)

    try:
        from venice_usage.pricing import PRICES
    except Exception:
        PRICES = {}

    fixtures = [harness.load_fixture(FIXTURES / fid) for fid in BAKEOFF_FIXTURE_IDS]
    rows, total = [], {model: 0.0 for model in chair_models}
    for fixture in fixtures:
        harness.validate_fixture(fixture)
        panel = load_panel(fixture.fixture_id)
        system, user, _, code_diff = chair_prompt(fixture, panel)
        tier = risk_tier(changed_paths(code_diff))
        prompt_bytes = len(user.encode()) + len(system.encode())
        tokens_in = prompt_bytes / 3.0        # deliberately pessimistic, per the design
        row = {"fixture": fixture.fixture_id, "tier": tier,
               "panel_candidates": len(candidate_findings(panel, tier=tier)),
               "chair_prompt_bytes": prompt_bytes, "pessimistic_tokens_in": round(tokens_in),
               "per_call_diem": {}}
        for model in chair_models:
            price = PRICES.get(model)
            if not price:
                row["per_call_diem"][model] = None
                continue
            cost = (tokens_in / 1e6) * price["input"] + (3000 / 1e6) * price["output"]
            row["per_call_diem"][model] = round(cost, 4)
            total[model] += cost * repeats
        rows.append(row)

    payload = {
        "schema_version": 1, "mode": "dry-run",
        "experiment": "chair bake-off (docs/chair-bakeoff-design-2026-09-11.md)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "chair_models": list(chair_models), "repeats": repeats,
        "council": council_provenance(),
        "chair_max_completion_tokens": budget.chair,
        "planned_api_calls": len(fixtures) * len(chair_models) * repeats,
        "api_calls_made": 0,
        "pessimistic_total_diem": round(sum(total.values()), 4),
        "pessimistic_diem_by_model": {k: round(v, 4) for k, v in total.items()},
        "fixtures": rows,
        "verdict": "pass",
    }
    harness.write_evidence(output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate and price with zero API calls")
    mode.add_argument("--paid", action="store_true", help="explicitly permit the paid Venice run")
    parser.add_argument("--chair-model", action="append", dest="chair_models", default=[],
                        help="candidate chair (repeatable)")
    parser.add_argument("--repeats", type=int, default=3, help="rollouts per cell")
    parser.add_argument("--estimate-diem", type=float, help="operator's estimate; not a billing cap")
    parser.add_argument("--output", type=Path, default=HERE / "results" / "bakeoff-latest.json")
    parser.add_argument("--transport-hook", help="inject /absolute/file.py:callable as HTTP post")
    args = parser.parse_args(argv)

    if not args.chair_models:
        print("error: at least one --chair-model is required", file=sys.stderr)
        return 2
    if args.repeats < 1:
        print("error: --repeats must be >= 1", file=sys.stderr)
        return 2
    if args.dry_run:
        return dry_run(chair_models=args.chair_models, repeats=args.repeats, output=args.output)
    if not args.paid:
        print("error: a live bake-off requires the explicit --paid flag", file=sys.stderr)
        return 2
    if args.estimate_diem is None or not math.isfinite(args.estimate_diem) or args.estimate_diem <= 0:
        print("error: --paid requires a positive --estimate-diem from current pricing",
              file=sys.stderr)
        return 2
    calls = len(BAKEOFF_FIXTURE_IDS) * len(args.chair_models) * args.repeats
    print(f"Preflight: {calls} paid CHAIR calls (no panel calls), {args.estimate_diem:.2f} DIEM "
          "operator estimate, not a billing cap.", flush=True)
    return run(chair_models=args.chair_models, repeats=args.repeats,
               output=args.output, transport_hook=args.transport_hook)


if __name__ == "__main__":
    raise SystemExit(main())
