from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.regress import harness
from tools.regress import run


FIXTURES = Path(__file__).parents[1] / "tools" / "regress" / "fixtures"


def test_committed_fixtures_are_complete_and_match_their_manifest():
    fixtures = harness.load_fixtures(FIXTURES)

    # `fixtures/` holds the pinned golden set AND the chair bake-off's own case;
    # every committed fixture must still hash-validate.
    assert [fixture.fixture_id for fixture in fixtures] == ["aris-pr1", "baw-pr11", "stw-pr11"]
    assert {f.fixture_id: f.expected_blocking for f in fixtures} == {
        "aris-pr1": 0, "baw-pr11": 2, "stw-pr11": 0,
    }
    assert all(len(fixture.base) == 40 and len(fixture.head) == 40 for fixture in fixtures)
    for fixture in fixtures:
        harness.validate_fixture(fixture)


def test_golden_set_is_pinned_by_name_not_by_directory_listing():
    """The pinned regression's matrix must not grow when a fixture is added.

    `baw-pr11` lives in the same directory for the bake-off; loading the golden
    set by listing would sweep it in, change 4 cells to 6, and change what a
    `--paid` run costs and claims.
    """
    assert [f.fixture_id for f in run._golden_fixtures()] == list(run.EXPECTED_FIXTURE_IDS)
    assert len(harness.build_run_matrix(run._golden_fixtures())) == 4


def test_context_uses_fixed_head_files_and_required_anchors():
    fixtures = {fixture.fixture_id: fixture for fixture in harness.load_fixtures(FIXTURES)}

    aris = harness.gather_file_context(fixtures["aris-pr1"])
    assert "--- src/pages/work.astro ---" in aris
    assert "widths={[672, 1008, 1344]}" in aris
    assert "--- package.json ---" in aris
    assert "--- .gitignore ---" in aris
    assert "--- .nvmrc ---" in aris

    swimtrack = harness.gather_file_context(fixtures["stw-pr11"])
    assert "--- tools/i18n/translate.mjs ---" in swimtrack
    assert "process.loadEnvFile(path)" in swimtrack
    assert '"node": ">=22.12.0"' in swimtrack
    assert "--- .gitignore ---" in swimtrack
    assert "--- .nvmrc ---" not in swimtrack


def test_context_caps_each_file_and_the_total(tmp_path):
    fixture_dir = tmp_path / "fixture"
    (fixture_dir / "head" / "src").mkdir(parents=True)
    (fixture_dir / "head" / "src" / "large.py").write_text("x" * 50_000)
    (fixture_dir / "head" / "package.json").write_text("y" * 50_000)
    (fixture_dir / "change.diff").write_text(
        "diff --git a/src/large.py b/src/large.py\n"
        "--- a/src/large.py\n+++ b/src/large.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    harness.write_manifest(
        fixture_dir,
        fixture_id="synthetic",
        repo="example.invalid/repo",
        pr=1,
        base="a" * 40,
        head="b" * 40,
        expected_blocking=0,
    )
    fixture = harness.load_fixture(fixture_dir)

    context = harness.gather_file_context(fixture, per_file_cap=40_000, total_cap=60_000)

    assert len(context.encode()) <= 60_000
    assert context.count("x") <= 40_000
    assert context.count("y") <= 40_000


def test_run_matrix_has_grounded_and_ungrounded_arm_for_each_fixture():
    matrix = harness.build_run_matrix(run._golden_fixtures())

    assert [(case.fixture.fixture_id, case.grounding) for case in matrix] == [
        ("aris-pr1", "none"),
        ("aris-pr1", "full-file-context"),
        ("stw-pr11", "none"),
        ("stw-pr11", "full-file-context"),
    ]


@pytest.mark.parametrize(
    ("record", "expected_verdict"),
    [
        ({"blocking": 0, "unavailable": False, "error": None}, "pass"),
        ({"blocking": 1, "unavailable": False, "error": None}, "fail"),
        ({"blocking": 0, "unavailable": True, "error": None}, "fail"),
        ({"blocking": 0, "unavailable": False, "error": "review failed"}, "fail"),
    ],
)
def test_result_grading_fails_closed(record, expected_verdict):
    result = harness.grade_records([record])

    assert result["verdict"] == expected_verdict


def test_dry_run_validates_without_install_or_review(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(run, "install_and_run", lambda *args, **kwargs: calls.append(args))
    output = tmp_path / "dry-run.json"

    exit_code = run.main(["--dry-run", "--output", str(output)])

    assert exit_code == 0
    assert calls == []
    payload = json.loads(output.read_text())
    assert payload["mode"] == "dry-run"
    assert payload["estimated_api_calls"] == 16
    assert payload["fixture_count"] == 2
    assert "0 API calls" in capsys.readouterr().out


def test_live_run_requires_explicit_paid_flag(tmp_path, capsys):
    exit_code = run.main(["--output", str(tmp_path / "result.json")])

    assert exit_code == 2
    assert "--paid" in capsys.readouterr().err


def test_paid_run_prints_estimate_before_starting(monkeypatch, tmp_path, capsys):
    seen = []

    def fake_install_and_run(**kwargs):
        seen.append(capsys.readouterr().out)
        Path(kwargs["output"]).write_text(json.dumps({"verdict": "pass", "runs": []}))
        return 0

    monkeypatch.setattr(run, "install_and_run", fake_install_and_run)

    exit_code = run.main([
        "--paid", "--estimate-diem", "4", "--output", str(tmp_path / "result.json")
    ])

    assert exit_code == 0
    assert seen and "16 paid API calls" in seen[0]
    assert "4.00 DIEM" in seen[0]
    assert "operator estimate, not a billing cap" in seen[0]


def test_worker_records_review_exception_and_fails_closed(monkeypatch, tmp_path):
    output = tmp_path / "evidence.json"

    def fail_review(*args, **kwargs):
        raise RuntimeError("synthetic outage")

    monkeypatch.setattr(run, "_load_pinned_council", lambda: (object(), object(), fail_review))
    monkeypatch.setattr(run, "_build_client", lambda *args, **kwargs: object())

    exit_code = run.worker(output=output, transport_hook=None)

    payload = json.loads(output.read_text())
    assert exit_code == 1
    assert payload["verdict"] == "fail"
    assert len(payload["runs"]) == 4
    assert all(item["error"] == "RuntimeError: synthetic outage" for item in payload["runs"])


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"blocking": False, "unavailable": False, "error": None},
        {"blocking": -1, "unavailable": False, "error": None},
        {"blocking": 0, "unavailable": 0, "error": None},
        {"blocking": 0, "unavailable": None, "error": None},
    ],
)
def test_result_grading_rejects_malformed_records(record):
    assert harness.grade_records([record])["verdict"] == "fail"


def test_dry_run_rejects_an_empty_or_changed_golden_set(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run, "_golden_fixtures", lambda: [])
    called = []
    monkeypatch.setattr(run, "install_and_run", lambda **kwargs: called.append(kwargs))

    exit_code = run.main(["--dry-run", "--output", str(tmp_path / "evidence.json")])

    assert exit_code == 1
    assert called == []
    assert "expected fixtures" in capsys.readouterr().err


def test_worker_cli_requires_paid_and_never_runs_by_default(monkeypatch, tmp_path, capsys):
    called = []
    monkeypatch.setattr(run, "worker", lambda **kwargs: called.append(kwargs) or 0)

    exit_code = run.main(["--_worker", "--output", str(tmp_path / "evidence.json")])

    assert exit_code == 2
    assert called == []
    assert "--paid" in capsys.readouterr().err


def test_dry_run_and_paid_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit) as exc:
        run.main([
            "--dry-run", "--paid", "--estimate-diem", "4",
            "--output", str(tmp_path / "evidence.json"),
        ])
    assert exc.value.code == 2


def test_paid_run_requires_positive_operator_estimate(monkeypatch, tmp_path, capsys):
    called = []
    monkeypatch.setattr(run, "install_and_run", lambda **kwargs: called.append(kwargs) or 0)

    missing = run.main(["--paid", "--output", str(tmp_path / "missing.json")])
    zero = run.main([
        "--paid", "--estimate-diem", "0", "--output", str(tmp_path / "zero.json")
    ])

    assert missing == 2 and zero == 2
    assert called == []
    assert "--estimate-diem" in capsys.readouterr().err


def test_pinned_council_loads_its_bundled_panels(monkeypatch):
    import council.config

    seen = []
    monkeypatch.setattr(run.importlib.metadata, "version", lambda name: "0.4.0")
    monkeypatch.setattr(
        council.config,
        "load_panels",
        lambda path: (seen.append(Path(path)) or (object(), object())),
    )

    run._load_pinned_council()

    assert len(seen) == 1
    assert seen[0].name == "panels.toml"
    assert seen[0].is_file()



def test_worker_paid_still_requires_operator_estimate(monkeypatch, tmp_path, capsys):
    called = []
    monkeypatch.setattr(run, "worker", lambda **kwargs: called.append(kwargs) or 0)

    exit_code = run.main([
        "--_worker", "--paid", "--output", str(tmp_path / "evidence.json")
    ])

    assert exit_code == 2
    assert called == []
    assert "--estimate-diem" in capsys.readouterr().err


def test_worker_rejects_changed_fixture_set_before_loading_council(monkeypatch, tmp_path):
    loaded = []
    output = tmp_path / "evidence.json"
    monkeypatch.setattr(run, "_golden_fixtures", lambda: [])
    monkeypatch.setattr(run, "_load_pinned_council", lambda: loaded.append(True))

    exit_code = run.worker(output=output, transport_hook=None)

    payload = json.loads(output.read_text())
    assert exit_code == 1
    assert payload["verdict"] == "fail"
    assert payload["bootstrap_error"]["phase"] == "worker-bootstrap"
    assert "expected fixtures" in payload["bootstrap_error"]["error"]
    assert loaded == []


def test_manifest_schema_and_integer_types_are_strict(tmp_path):
    fixture_dir = tmp_path / "synthetic"
    (fixture_dir / "head").mkdir(parents=True)
    (fixture_dir / "change.diff").write_text(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    harness.write_manifest(
        fixture_dir,
        fixture_id="synthetic",
        repo="example.invalid/repo",
        pr=1,
        base="a" * 40,
        head="b" * 40,
        expected_blocking=0,
    )
    manifest = fixture_dir / "manifest.json"
    payload = json.loads(manifest.read_text())
    payload["schema_version"] = 2
    payload["source"]["pr"] = True
    manifest.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="manifest"):
        harness.load_fixture(fixture_dir)


def _synthetic_snapshot_with_its_own_tests(tmp_path):
    fixture_dir = tmp_path / "synthetic"
    (fixture_dir / "head" / "tests").mkdir(parents=True)
    (fixture_dir / "head" / "tests" / "test_archived.py").write_text("def test_archived():\n    assert True\n")
    (fixture_dir / "change.diff").write_text(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    harness.write_manifest(
        fixture_dir,
        fixture_id="synthetic",
        repo="example.invalid/repo",
        pr=1,
        base="a" * 40,
        head="b" * 40,
        expected_blocking=0,
    )
    harness.validate_fixture(harness.load_fixture(fixture_dir))
    return fixture_dir


def test_validation_rejects_generated_bytecode_like_any_other_extra_file(tmp_path):
    """Archived tests stay out of pytest discovery (pyproject.toml `norecursedirs`).

    That is the whole correction. The validator must keep rejecting the bytecode
    a stray import leaves behind, not learn to look past `__pycache__`.
    """
    fixture_dir = _synthetic_snapshot_with_its_own_tests(tmp_path)
    cache = fixture_dir / "head" / "tests" / "__pycache__"
    cache.mkdir()
    (cache / "test_archived.cpython-312-pytest-9.0.3.pyc").write_bytes(b"\x00")

    with pytest.raises(ValueError, match="snapshot files differ from manifest"):
        harness.validate_fixture(harness.load_fixture(fixture_dir))


def test_validation_rejects_a_symlink_inside_the_snapshot(tmp_path):
    fixture_dir = _synthetic_snapshot_with_its_own_tests(tmp_path)
    try:
        os.symlink(fixture_dir / "head" / "tests" / "test_archived.py", fixture_dir / "head" / "linked.py")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this platform cannot create a symlink here: {exc!r}")

    with pytest.raises(ValueError, match="snapshot contains symlink"):
        harness.validate_fixture(harness.load_fixture(fixture_dir))


@pytest.mark.parametrize("estimate", ["nan", "inf", "-inf"])
@pytest.mark.parametrize("internal_worker", [False, True])
def test_paid_rejects_nonfinite_estimates(monkeypatch, tmp_path, estimate, internal_worker):
    called = []
    monkeypatch.setattr(run, "install_and_run", lambda **kwargs: called.append(kwargs) or 0)
    monkeypatch.setattr(run, "worker", lambda **kwargs: called.append(kwargs) or 0)
    args = ["--paid", f"--estimate-diem={estimate}", "--output", str(tmp_path / "result.json")]
    if internal_worker:
        args.append("--_worker")
    assert run.main(args) == 2
    assert called == []


def test_fresh_environment_installs_recorded_commit_not_a_movable_tag(monkeypatch, tmp_path):
    from types import SimpleNamespace
    invocations = []
    monkeypatch.setattr(run.venv, "EnvBuilder", lambda **kwargs: SimpleNamespace(create=lambda path: None))

    def fake_subprocess(command, **kwargs):
        invocations.append(command)
        return SimpleNamespace(returncode=0, stdout=run.PINNED_COMMIT + "\n")

    monkeypatch.setattr(run.subprocess, "run", fake_subprocess)
    result = run.install_and_run(output=tmp_path / "result.json", transport_hook=None, estimate_diem=4)
    assert result == 0
    installed_source = next(command[-1] for command in invocations if "pip" in command)
    assert installed_source.endswith("@" + run.PINNED_COMMIT)



@pytest.mark.skipif(shutil.which("git") is None, reason="fixture checkout validation requires git")
def test_fixture_attributes_preserve_bytes_with_autocrlf_checkout(tmp_path):
    source = tmp_path / "source"
    checkout = tmp_path / "checkout"
    source.mkdir()
    shutil.copy(Path(__file__).parents[1] / ".gitattributes", source / ".gitattributes")
    fixture_source = FIXTURES / "stw-pr11"
    fixture_copy = source / "tools" / "regress" / "fixtures" / "stw-pr11"
    shutil.copytree(fixture_source, fixture_copy)
    expected = {
        path.relative_to(source): path.read_bytes()
        for path in fixture_copy.rglob("*")
        if path.is_file()
    }
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Fixture Test"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "fixture@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "fixture"], check=True)
    subprocess.run(["git", "clone", "-qn", str(source), str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "core.autocrlf", "true"], check=True)
    subprocess.run(["git", "-C", str(checkout), "checkout", "-q"], check=True)

    for relative, original_bytes in expected.items():
        assert (checkout / relative).read_bytes() == original_bytes, relative
    fixtures = harness.load_fixtures(checkout / "tools" / "regress" / "fixtures")
    harness.validate_fixture(fixtures[0])


def test_worker_bootstrap_failure_writes_four_redacted_failures(monkeypatch, tmp_path):
    output = tmp_path / "bootstrap.json"
    secret = "test-secret-must-not-appear"
    monkeypatch.setenv("VENICE_COUNCIL_KEY", secret)
    monkeypatch.setattr(
        run,
        "_load_pinned_council",
        lambda: (_ for _ in ()).throw(RuntimeError(f"bad client {secret}")),
    )

    exit_code = run.worker(output=output, transport_hook=None)

    raw = output.read_text()
    payload = json.loads(raw)
    assert exit_code == 1
    assert payload["verdict"] == "fail"
    assert payload["bootstrap_error"]["phase"] == "worker-bootstrap"
    assert secret not in raw
    assert len(payload["runs"]) == 4
    assert all(item["status"] == "fail" and item["error"] for item in payload["runs"])


def test_coordinator_bootstrap_failure_writes_json_and_returns_nonzero(monkeypatch, tmp_path):
    output = tmp_path / "coordinator.json"
    monkeypatch.setattr(
        run,
        "install_and_run",
        lambda **kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(1, ["pip"])),
    )

    exit_code = run.main([
        "--paid", "--estimate-diem", "4", "--output", str(output)
    ])

    payload = json.loads(output.read_text())
    assert exit_code == 1
    assert payload["verdict"] == "fail"
    assert payload["bootstrap_error"]["phase"] == "environment-bootstrap"
    assert payload["runs"] == []


def test_missing_worker_evidence_is_replaced_with_failure_json(monkeypatch, tmp_path):
    output = tmp_path / "missing-worker.json"
    output.write_text(json.dumps({"verdict": "pass", "runs": []}))
    monkeypatch.setattr(run, "install_and_run", lambda **kwargs: 2)

    exit_code = run.main([
        "--paid", "--estimate-diem", "4", "--output", str(output)
    ])

    payload = json.loads(output.read_text())
    assert exit_code == 2
    assert payload["verdict"] == "fail"
    assert payload["bootstrap_error"]["phase"] == "worker-startup"


@pytest.mark.parametrize("malformed", ["[]", "null", "42", "not json"])
def test_malformed_worker_evidence_is_replaced_with_failure_json(monkeypatch, tmp_path, malformed):
    output = tmp_path / "malformed-worker.json"

    def failed_worker(**kwargs):
        output.write_text(malformed)
        return 2

    monkeypatch.setattr(run, "install_and_run", failed_worker)
    assert run.main(["--paid", "--estimate-diem", "4", "--output", str(output)]) == 2
    payload = json.loads(output.read_text())
    assert payload["verdict"] == "fail"
    assert payload["bootstrap_error"]["phase"] == "worker-startup"


@pytest.mark.parametrize("failure", [RuntimeError("lookup failed"), OSError("unreadable key file")])
def test_redaction_failure_omits_exception_details(monkeypatch, failure):
    def broken_lookup(*args):
        raise failure

    monkeypatch.setattr(run, "_env_assignment", broken_lookup)
    error = run._safe_error(RuntimeError("private credential must not be emitted"))
    assert "private credential" not in error
    assert "details omitted" in error


def test_exception_with_broken_string_still_produces_failure_evidence(monkeypatch, tmp_path):
    class BrokenError(Exception):
        def __str__(self):
            raise RuntimeError("broken formatter")

    def broken_load():
        raise BrokenError()

    monkeypatch.setattr(run, "_load_pinned_council", broken_load)
    output = tmp_path / "broken-error.json"
    assert run.worker(output=output, transport_hook=None) == 1
    payload = json.loads(output.read_text())
    assert payload["verdict"] == "fail"
    assert "BrokenError" in payload["bootstrap_error"]["error"]
    assert "details omitted" in payload["bootstrap_error"]["error"]
