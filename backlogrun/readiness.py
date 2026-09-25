"""Validate structured review evidence and derive review readiness.

This module is deliberately pure apart from checking evidence files.  Callers own
record discovery and persistence.
"""
from __future__ import annotations

import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_UTC_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)
_RUNNER_OUTCOMES = {"completed", "incomplete", "failed", "unknown"}
_REVIEW_STATUSES = {"clean", "changes_requested", "failed", "unknown"}
_VALIDATION_STATUSES = {"passed", "failed", "unknown"}


def _result(branch_sha: str, *, record_id: str | None = None,
            status: str = "unknown", reasons: list[str] | None = None,
            evidence_path: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_id": record_id,
        "branch_sha": branch_sha,
        "status": status,
        "reasons": reasons or [],
        "evidence_path": evidence_path,
    }


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _full_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA_RE.fullmatch(value) is not None


_SHORT_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def expand_sha(reported: object, head_sha: str) -> object:
    """A session may report a validation's commit as a short SHA (``0123abc``). When that is a
    hex prefix of at least 7 characters of the full head SHA it was run against, return the
    full head SHA. Anything else comes back unchanged, so evaluate() still rejects it."""
    if (isinstance(reported, str) and _SHORT_SHA_RE.fullmatch(reported) and len(reported) < 40
            and _full_sha(head_sha) and head_sha.lower().startswith(reported.lower())):
        return head_sha
    return reported


def _utc_rfc3339(value: object) -> datetime | None:
    if not isinstance(value, str) or _UTC_RFC3339_RE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed


def _evidence_is_readable(path_value: object, state_dir: str) -> bool:
    """Accept only relative, readable regular files that resolve inside state_dir."""
    if not _nonempty_string(path_value):
        return False
    relative = Path(path_value)
    if relative.is_absolute() or ".." in relative.parts:
        return False
    try:
        root = Path(state_dir).resolve(strict=True)
        candidate = (root / relative).resolve(strict=True)
        candidate.relative_to(root)
        mode = candidate.stat().st_mode
        if not stat.S_ISREG(mode) or mode & 0o444 == 0:
            return False
        with candidate.open("rb") as handle:
            handle.read(1)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def evaluate(record: object, *, branch_sha: str, state_dir: str) -> dict[str, Any]:
    """Return a conservative readiness decision for one structured review record."""
    if not isinstance(record, dict):
        return _result(branch_sha, reasons=["Review input is not an object."])

    reasons: list[str] = []
    structural_errors: list[str] = []
    record_id = record.get("record_id") if _nonempty_string(record.get("record_id")) else None

    required_fields = {
        "schema_version", "record_id", "finished_at", "branch_sha", "runner_outcome",
        "review_status", "blocking_findings", "required_validations", "validations",
        "source_record",
    }
    for field in sorted(required_fields - record.keys()):
        structural_errors.append(f"Missing required field: {field}.")

    if type(record.get("schema_version")) is not int or record.get("schema_version") != 1:
        structural_errors.append("Unsupported or invalid schema_version.")
    if record_id is None:
        structural_errors.append("record_id must be a non-empty string.")
    if _utc_rfc3339(record.get("finished_at")) is None:
        structural_errors.append("finished_at must be an RFC3339 UTC timestamp.")

    record_sha = record.get("branch_sha")
    if not _full_sha(record_sha):
        structural_errors.append("branch_sha must be a full 40-character commit SHA.")
    elif record_sha.lower() != branch_sha.lower():
        structural_errors.append("Review evidence is for a different branch SHA.")
    if not _full_sha(branch_sha):
        structural_errors.append("Current branch SHA must be a full 40-character commit SHA.")

    runner_outcome = record.get("runner_outcome")
    if not isinstance(runner_outcome, str) or runner_outcome not in _RUNNER_OUTCOMES:
        structural_errors.append("runner_outcome is invalid.")
    review_status = record.get("review_status")
    if not isinstance(review_status, str) or review_status not in _REVIEW_STATUSES:
        structural_errors.append("review_status is invalid.")

    blocking = record.get("blocking_findings")
    if not isinstance(blocking, list) or any(not isinstance(item, str) for item in blocking):
        structural_errors.append("blocking_findings must be a list of strings.")
        blocking_text: list[str] = []
    else:
        blocking_text = list(blocking)
        reasons.extend(blocking_text)

    required = record.get("required_validations")
    required_names: list[str] | None
    if required is None:
        required_names = None
    elif (not isinstance(required, list)
          or any(not _nonempty_string(name) for name in required)
          or len(set(required)) != len(required)):
        structural_errors.append(
            "required_validations must be None or a list of unique non-empty names."
        )
        required_names = None
    else:
        required_names = list(required)

    source_record = record.get("source_record")
    evidence_path: str | None = None
    if _evidence_is_readable(source_record, state_dir):
        evidence_path = source_record
    else:
        structural_errors.append("source_record is not a readable regular file within state_dir.")

    validations = record.get("validations")
    parsed_validations: dict[str, dict[str, str]] = {}
    validation_conflict = False
    if not isinstance(validations, list):
        structural_errors.append("validations must be a list.")
    else:
        for index, validation in enumerate(validations):
            if not isinstance(validation, dict):
                structural_errors.append(f"Validation {index} is not an object.")
                continue
            name = validation.get("name")
            validation_sha = validation.get("branch_sha")
            validation_status = validation.get("status")
            validation_path = validation.get("evidence_path")
            valid = True
            if not _nonempty_string(name):
                structural_errors.append(f"Validation {index} has an invalid name.")
                valid = False
            if not _full_sha(validation_sha):
                structural_errors.append(f"Validation {index} has an invalid branch_sha.")
                valid = False
            elif validation_sha.lower() != branch_sha.lower():
                validation_conflict = True
                reasons.append(f"Validation {name or index} is for a different branch SHA.")
            if not isinstance(validation_status, str) or validation_status not in _VALIDATION_STATUSES:
                structural_errors.append(f"Validation {name or index} has an invalid status.")
                valid = False
            if not _evidence_is_readable(validation_path, state_dir):
                structural_errors.append(
                    f"Validation {name or index} evidence is not a readable regular file within state_dir."
                )
                valid = False
            if valid:
                normalized = {
                    "name": name,
                    "branch_sha": validation_sha.lower(),
                    "status": validation_status,
                    "evidence_path": validation_path,
                }
                prior = parsed_validations.get(name)
                if prior is not None and prior != normalized:
                    validation_conflict = True
                    reasons.append(f"Validation {name} has conflicting results.")
                else:
                    parsed_validations[name] = normalized

    # Invalid evidence and contradictions must never be hidden by a failure verdict.
    if review_status == "clean" and blocking_text:
        reasons.append("A clean review contains blocking findings.")
        validation_conflict = True
    if structural_errors or validation_conflict:
        reasons.extend(structural_errors)
        return _result(branch_sha, record_id=record_id, reasons=reasons,
                       evidence_path=evidence_path)

    failed_required = [
        name for name in (required_names or [])
        if name in parsed_validations and parsed_validations[name]["status"] == "failed"
    ]
    if runner_outcome == "failed" or review_status == "failed" or failed_required:
        reasons.extend(f"Required validation failed: {name}." for name in failed_required)
        if runner_outcome == "failed":
            reasons.append("The runner reported failure.")
        if review_status == "failed":
            reasons.append("The review reported failure.")
        return _result(branch_sha, record_id=record_id, status="failed", reasons=reasons,
                       evidence_path=evidence_path)

    if blocking_text or review_status == "changes_requested":
        if review_status == "changes_requested" and not blocking_text:
            reasons.append("The review requested changes.")
        return _result(branch_sha, record_id=record_id, status="changes_requested",
                       reasons=reasons, evidence_path=evidence_path)

    if required_names is None:
        reasons.append("Required validations were not declared before execution.")
    else:
        for name in required_names:
            validation = parsed_validations.get(name)
            if validation is None:
                reasons.append(f"Required validation is missing: {name}.")
            elif validation["status"] == "unknown":
                reasons.append(f"Required validation is unknown: {name}.")
    if runner_outcome != "completed":
        reasons.append("The runner has no explicit completed outcome.")
    if review_status != "clean":
        reasons.append("The review has no explicit clean verdict.")
    if reasons:
        return _result(branch_sha, record_id=record_id, reasons=reasons,
                       evidence_path=evidence_path)

    return _result(branch_sha, record_id=record_id, status="ready", reasons=[],
                   evidence_path=evidence_path)


def select(records: list[object], *, branch_sha: str, state_dir: str) -> dict[str, Any]:
    """Select the newest attempt for ``branch_sha`` and evaluate it.

    Invalid timestamps and ambiguous duplicate records fail closed because their
    ordering cannot be established safely.
    """
    if not isinstance(records, list):
        return _result(branch_sha, reasons=["Review inputs must be a list."])
    if not records:
        return _result(branch_sha, reasons=["No review evidence exists for this branch SHA."])

    unique: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            return _result(branch_sha, reasons=["A review input is malformed."])
        record_id = record.get("record_id")
        if _nonempty_string(record_id):
            prior = by_id.get(record_id)
            if prior is not None:
                if prior != record:
                    return _result(
                        branch_sha,
                        reasons=[f"Record ID {record_id} has conflicting payloads."],
                    )
                continue
            by_id[record_id] = record
        unique.append(record)

    dated: list[tuple[datetime, dict[str, Any]]] = []
    for record in unique:
        finished_at = _utc_rfc3339(record.get("finished_at"))
        if finished_at is None:
            return _result(
                branch_sha,
                record_id=record.get("record_id") if _nonempty_string(record.get("record_id")) else None,
                reasons=["A review input has a missing or invalid finished_at timestamp."],
            )
        dated.append((finished_at, record))

    matching = [(finished_at, record) for finished_at, record in dated
                if isinstance(record.get("branch_sha"), str)
                and record["branch_sha"].lower() == branch_sha.lower()]
    if not matching:
        return _result(branch_sha, reasons=["No review evidence exists for this branch SHA."])

    newest_time = max(finished_at for finished_at, _ in matching)
    if any(not _full_sha(record.get("branch_sha")) and finished_at >= newest_time
           for finished_at, record in dated):
        return _result(
            branch_sha,
            reasons=["A newer review input has no valid branch SHA."],
        )
    newest = [record for finished_at, record in matching if finished_at == newest_time]
    if len(newest) == 1:
        return evaluate(newest[0], branch_sha=branch_sha, state_dir=state_dir)

    evaluated = [evaluate(record, branch_sha=branch_sha, state_dir=state_dir) for record in newest]
    comparable = [
        (item["status"], item["reasons"], item["evidence_path"])
        for item in evaluated
    ]
    if any(item != comparable[0] for item in comparable[1:]):
        return _result(branch_sha, reasons=["Newest review records have conflicting outcomes."])
    return evaluated[0]
