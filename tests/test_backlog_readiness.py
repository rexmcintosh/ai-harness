from __future__ import annotations

from copy import deepcopy

import pytest

from backlogrun.readiness import evaluate, select


SHA = "a" * 40
OTHER_SHA = "b" * 40


@pytest.fixture
def evidence(tmp_path):
    (tmp_path / "reviews").mkdir()
    (tmp_path / "reviews" / "review.md").write_text("complete review\n")
    (tmp_path / "reviews" / "tests.txt").write_text("passed\n")
    return tmp_path


def record(**changes):
    value = {
        "schema_version": 1,
        "record_id": "run-1",
        "finished_at": "2026-09-06T03:00:00Z",
        "branch_sha": SHA,
        "runner_outcome": "completed",
        "review_status": "clean",
        "blocking_findings": [],
        "required_validations": ["tests"],
        "validations": [{
            "name": "tests",
            "branch_sha": SHA,
            "status": "passed",
            "evidence_path": "reviews/tests.txt",
        }],
        "source_record": "reviews/review.md",
    }
    value.update(changes)
    return value


def test_ready_requires_explicit_success_and_returns_source_record(evidence):
    got = evaluate(record(), branch_sha=SHA, state_dir=str(evidence))
    assert got == {
        "schema_version": 1,
        "record_id": "run-1",
        "branch_sha": SHA,
        "status": "ready",
        "reasons": [],
        "evidence_path": "reviews/review.md",
    }
    assert evaluate(record(required_validations=[], validations=[]), branch_sha=SHA,
                    state_dir=str(evidence))["status"] == "ready"


@pytest.mark.parametrize("field,value", [
    ("schema_version", True),
    ("record_id", ""),
    ("finished_at", "2026-09-06T03:00:00-04:00"),
    ("finished_at", "2026-09-06 03:00:00+00:00"),
    ("branch_sha", "abc"),
    ("runner_outcome", "done"),
    ("review_status", None),
    ("blocking_findings", "none"),
    ("required_validations", ["tests", "tests"]),
    ("validations", {}),
    ("source_record", None),
])
def test_malformed_types_are_unknown(evidence, field, value):
    assert evaluate(record(**{field: value}), branch_sha=SHA,
                    state_dir=str(evidence))["status"] == "unknown"


def test_stale_sha_and_validation_sha_are_unknown(evidence):
    assert evaluate(record(branch_sha=OTHER_SHA), branch_sha=SHA,
                    state_dir=str(evidence))["status"] == "unknown"
    stale_validation = record(validations=[{
        "name": "tests", "branch_sha": OTHER_SHA, "status": "failed",
        "evidence_path": "reviews/tests.txt",
    }])
    assert evaluate(stale_validation, branch_sha=SHA,
                    state_dir=str(evidence))["status"] == "unknown"


@pytest.mark.parametrize("path", ["../outside.md", "/tmp/outside.md", "reviews/missing.md"])
def test_unreadable_or_unsafe_source_is_unknown(evidence, path):
    assert evaluate(record(source_record=path), branch_sha=SHA,
                    state_dir=str(evidence))["status"] == "unknown"


def test_symlink_escape_and_non_regular_evidence_are_unknown(evidence, tmp_path):
    outside = tmp_path.parent / "outside-review.md"
    outside.write_text("outside\n")
    (evidence / "reviews" / "escape.md").symlink_to(outside)
    escaped = record(source_record="reviews/escape.md")
    assert evaluate(escaped, branch_sha=SHA, state_dir=str(evidence))["status"] == "unknown"
    directory_evidence = record(source_record="reviews")
    assert evaluate(directory_evidence, branch_sha=SHA,
                    state_dir=str(evidence))["status"] == "unknown"


def test_full_blocking_reason_survives_unknown_contradiction(evidence):
    long_reason = "x" * 500 + " REQUIRED FIX AT THE END"
    got = evaluate(record(blocking_findings=[long_reason]), branch_sha=SHA,
                   state_dir=str(evidence))
    assert got["status"] == "unknown"
    assert long_reason in got["reasons"]
    assert got["evidence_path"] == "reviews/review.md"


def test_failures_and_missing_markers_never_report_ready(evidence):
    assert evaluate(record(runner_outcome="failed"), branch_sha=SHA,
                    state_dir=str(evidence))["status"] == "failed"
    assert evaluate(record(review_status="failed"), branch_sha=SHA,
                    state_dir=str(evidence))["status"] == "failed"
    assert evaluate(record(runner_outcome="unknown"), branch_sha=SHA,
                    state_dir=str(evidence))["status"] == "unknown"
    assert evaluate(record(review_status="unknown"), branch_sha=SHA,
                    state_dir=str(evidence))["status"] == "unknown"


def test_failed_required_validation_precedes_changes_requested(evidence):
    failed = record(
        review_status="changes_requested",
        validations=[{
            "name": "tests", "branch_sha": SHA, "status": "failed",
            "evidence_path": "reviews/tests.txt",
        }],
    )
    assert evaluate(failed, branch_sha=SHA, state_dir=str(evidence))["status"] == "failed"


def test_unknown_and_missing_required_validation_are_unknown(evidence):
    undeclared = evaluate(record(required_validations=None), branch_sha=SHA,
                          state_dir=str(evidence))
    assert undeclared["status"] == "unknown"
    assert evaluate(record(validations=[]), branch_sha=SHA,
                    state_dir=str(evidence))["status"] == "unknown"
    unknown = record(validations=[{
        "name": "tests", "branch_sha": SHA, "status": "unknown",
        "evidence_path": "reviews/tests.txt",
    }])
    assert evaluate(unknown, branch_sha=SHA, state_dir=str(evidence))["status"] == "unknown"


def test_conflicting_duplicate_validation_is_unknown(evidence):
    validations = record()["validations"]
    conflicting = deepcopy(validations[0])
    conflicting["status"] = "failed"
    got = evaluate(record(validations=validations + [conflicting]), branch_sha=SHA,
                   state_dir=str(evidence))
    assert got["status"] == "unknown"


@pytest.mark.parametrize("change", [
    {"name": "", "branch_sha": SHA, "status": "passed",
     "evidence_path": "reviews/tests.txt"},
    {"name": "tests", "branch_sha": "short", "status": "passed",
     "evidence_path": "reviews/tests.txt"},
    {"name": "tests", "branch_sha": SHA, "status": "success",
     "evidence_path": "reviews/tests.txt"},
    {"name": "tests", "branch_sha": SHA, "status": "passed",
     "evidence_path": "../tests.txt"},
])
def test_malformed_validation_fields_are_unknown(evidence, change):
    got = evaluate(record(validations=[change]), branch_sha=SHA, state_dir=str(evidence))
    assert got["status"] == "unknown"


def test_select_uses_newest_current_sha_and_never_reuses_other_sha(evidence):
    older = record(record_id="old", finished_at="2026-09-06T01:00:00Z",
                   review_status="changes_requested")
    newest = record(record_id="new", finished_at="2026-09-06T02:00:00Z")
    other = record(record_id="other", finished_at="2026-09-06T03:00:00Z",
                   branch_sha=OTHER_SHA)
    got = select([newest, other, older], branch_sha=SHA, state_dir=str(evidence))
    assert got["status"] == "ready" and got["record_id"] == "new"
    assert select([other], branch_sha=SHA, state_dir=str(evidence))["status"] == "unknown"


def test_newer_malformed_record_prevents_fallback(evidence):
    old = record(record_id="old", finished_at="2026-09-06T01:00:00Z")
    new = record(record_id="new", finished_at="2026-09-06T02:00:00Z")
    del new["review_status"]
    got = select([old, new], branch_sha=SHA, state_dir=str(evidence))
    assert got["status"] == "unknown" and got["record_id"] == "new"


def test_malformed_json_and_bad_timestamp_are_unknown(evidence):
    assert select([None], branch_sha=SHA, state_dir=str(evidence))["status"] == "unknown"
    assert select([record(finished_at="not-a-date")], branch_sha=SHA,
                  state_dir=str(evidence))["status"] == "unknown"


def test_identical_duplicate_ids_dedupe_and_conflicting_payloads_do_not(evidence):
    one = record()
    assert select([one, deepcopy(one)], branch_sha=SHA,
                  state_dir=str(evidence))["status"] == "ready"
    changed = deepcopy(one)
    changed["review_status"] = "failed"
    assert select([one, changed], branch_sha=SHA,
                  state_dir=str(evidence))["status"] == "unknown"


def test_tied_newest_conflicting_records_are_unknown(evidence):
    clean = record(record_id="clean")
    failed = record(record_id="failed", runner_outcome="failed")
    got = select([clean, failed], branch_sha=SHA, state_dir=str(evidence))
    assert got["status"] == "unknown"
    assert "conflicting" in got["reasons"][0].lower()


# The Jev shadow label (docs/council-jev-shadow-2026-09-19.md) is saved in the same inputs
# record. It is display only: readiness must not read it, in any state, whatever it says.
JEV_FIELDS = [
    {"jev_shadow": {"verdict": {"label": "approve", "confidence": 0.99}}},
    {"jev_shadow": {"verdict": {"label": "request_changes", "confidence": 0.97}}},
    {"jev_shadow": {"verdict": {"label": "approve_with_conditions", "confidence": 0.5}},
     "jev_verdict": "approve", "jev_confidence": 1.0, "jev": {"review_status": "clean", "blocking_findings": []}},
    {"jev_shadow": "garbage"},
]


@pytest.mark.parametrize("jev_fields", JEV_FIELDS)
@pytest.mark.parametrize("expected,changes", [
    ("ready", {}),
    ("changes_requested", {"review_status": "changes_requested", "blocking_findings": ["fix the wrong answer"]}),
    ("unknown", {"review_status": "unknown"}),
    ("unknown", {"required_validations": None}),
    ("failed", {"review_status": "failed"}),
])
def test_readiness_ignores_every_jev_field(evidence, expected, changes, jev_fields):
    plain = record(**changes)
    with_jev = {**deepcopy(plain), **deepcopy(jev_fields)}
    got_plain = evaluate(plain, branch_sha=SHA, state_dir=str(evidence))
    got_jev = evaluate(with_jev, branch_sha=SHA, state_dir=str(evidence))
    assert got_plain["status"] == expected
    assert got_jev == got_plain                                   # status, reasons, evidence: identical
    assert select([with_jev], branch_sha=SHA, state_dir=str(evidence)) == \
        select([plain], branch_sha=SHA, state_dir=str(evidence))
    assert "jev" not in repr(got_jev).lower()


def test_the_readiness_module_never_mentions_jev():
    from pathlib import Path
    import backlogrun.readiness as readiness
    assert "jev" not in Path(readiness.__file__).read_text().lower()
