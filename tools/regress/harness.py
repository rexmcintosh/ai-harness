"""Pure fixture, grounding-context, and verdict logic for the golden set."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Iterable

ANCHORS = ("package.json", ".gitignore", ".nvmrc")
PER_FILE_CAP = 40_000
TOTAL_CAP = 160_000
_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")


@dataclass(frozen=True)
class Fixture:
    root: Path
    fixture_id: str
    repo: str
    pr: int
    base: str
    head: str
    expected_blocking: int
    diff_sha256: str
    head_files: dict[str, dict[str, int | str]]

    @property
    def diff_path(self) -> Path:
        return self.root / "change.diff"

    @property
    def head_dir(self) -> Path:
        return self.root / "head"


@dataclass(frozen=True)
class RunCase:
    fixture: Fixture
    grounding: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(path_text: str) -> PurePosixPath:
    path = PurePosixPath(path_text)
    if not path_text or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"unsafe fixture path: {path_text!r}")
    return path


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def write_manifest(
    fixture_dir: Path,
    *, fixture_id: str,
    repo: str,
    pr: int,
    base: str,
    head: str,
    expected_blocking: int,
) -> None:
    """Hash an already-built fixture snapshot and write its manifest."""
    fixture_dir = Path(fixture_dir)
    diff_path = fixture_dir / "change.diff"
    head_dir = fixture_dir / "head"
    if not diff_path.is_file() or diff_path.is_symlink():
        raise ValueError("fixture change.diff must be a regular file")
    files: dict[str, dict[str, int | str]] = {}
    if head_dir.exists():
        for path in sorted(head_dir.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"fixture snapshot contains symlink: {path}")
            if path.is_file():
                relative = path.relative_to(head_dir).as_posix()
                _safe_relative(relative)
                files[relative] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    payload = {
        "schema_version": 1,
        "fixture_id": fixture_id,
        "source": {"repo": repo, "pr": pr, "base": base, "head": head},
        "expected_blocking": expected_blocking,
        "diff": {"path": "change.diff", "sha256": _sha256(diff_path)},
        "head_files": files,
    }
    _atomic_json(fixture_dir / "manifest.json", payload)


def load_fixture(fixture_dir: Path) -> Fixture:
    fixture_dir = Path(fixture_dir)
    manifest_path = fixture_dir / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text())
        source = payload["source"]
        diff = payload["diff"]
        if payload["schema_version"] != 1 or diff["path"] != "change.diff":
            raise ValueError("unsupported fixture manifest schema")
        if type(source["pr"]) is not int or type(payload["expected_blocking"]) is not int:
            raise ValueError("fixture manifest integers must not be coerced")
        if not isinstance(payload["fixture_id"], str) or not isinstance(source["repo"], str):
            raise ValueError("fixture manifest labels must be strings")
        if not isinstance(source["base"], str) or not isinstance(source["head"], str):
            raise ValueError("fixture commits must be strings")
        if not isinstance(diff["sha256"], str) or not isinstance(payload["head_files"], dict):
            raise ValueError("fixture evidence must be structured")
        return Fixture(
            root=fixture_dir,
            fixture_id=payload["fixture_id"],
            repo=source["repo"],
            pr=source["pr"],
            base=source["base"],
            head=source["head"],
            expected_blocking=payload["expected_blocking"],
            diff_sha256=diff["sha256"],
            head_files=payload["head_files"],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid fixture manifest: {manifest_path}") from exc


def load_fixtures(fixtures_dir: Path) -> list[Fixture]:
    fixtures_dir = Path(fixtures_dir)
    return [load_fixture(path) for path in sorted(fixtures_dir.iterdir()) if path.is_dir()]


def validate_fixture(fixture: Fixture) -> None:
    if not fixture.fixture_id:
        raise ValueError("fixture_id is required")
    if not fixture.repo or fixture.pr < 1:
        raise ValueError(f"invalid source provenance for {fixture.fixture_id}")
    for label, commit in (("base", fixture.base), ("head", fixture.head)):
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError(f"{fixture.fixture_id} has invalid {label} commit")
    if fixture.expected_blocking < 0:
        raise ValueError(f"{fixture.fixture_id} has negative expected_blocking")
    if not fixture.diff_path.is_file() or fixture.diff_path.is_symlink():
        raise ValueError(f"{fixture.fixture_id} change.diff is missing or unsafe")
    if _sha256(fixture.diff_path) != fixture.diff_sha256:
        raise ValueError(f"{fixture.fixture_id} change.diff hash mismatch")

    declared = set(fixture.head_files)
    actual: set[str] = set()
    for path in fixture.head_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"{fixture.fixture_id} snapshot contains symlink")
        if path.is_file():
            actual.add(path.relative_to(fixture.head_dir).as_posix())
    if actual != declared:
        raise ValueError(f"{fixture.fixture_id} snapshot files differ from manifest")
    for relative, evidence in fixture.head_files.items():
        safe = _safe_relative(relative)
        path = fixture.head_dir.joinpath(*safe.parts)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"{fixture.fixture_id} snapshot file is missing: {relative}")
        if path.stat().st_size != evidence.get("bytes") or _sha256(path) != evidence.get("sha256"):
            raise ValueError(f"{fixture.fixture_id} snapshot drift: {relative}")


def changed_paths(diff: str) -> list[str]:
    paths: list[str] = []
    for line in diff.splitlines():
        match = _DIFF_HEADER.match(line)
        if not match:
            continue
        old, new = match.groups()
        if old != new:
            raise ValueError("rename diff headers are not supported by this golden set")
        relative = _safe_relative(new).as_posix()
        if relative not in paths:
            paths.append(relative)
    return paths


def _fit_utf8(data: bytes, cap: int) -> bytes:
    if len(data) <= cap:
        return data
    return data[:cap].decode("utf-8", errors="ignore").encode("utf-8")


def gather_file_context(
    fixture: Fixture,
    *, per_file_cap: int = PER_FILE_CAP,
    total_cap: int = TOTAL_CAP,
) -> str:
    """Mirror the CI grounding shape from the immutable committed head snapshot."""
    validate_fixture(fixture)
    candidates = changed_paths(fixture.diff_path.read_text(errors="strict")) + list(ANCHORS)
    parts: list[bytes] = []
    used = 0
    seen: set[str] = set()
    for relative in candidates:
        if relative in seen:
            continue
        seen.add(relative)
        if relative not in fixture.head_files:
            continue
        path = fixture.head_dir.joinpath(*_safe_relative(relative).parts)
        raw = path.read_bytes()
        if b"\x00" in raw[:4096]:
            continue
        marker = f"\n... [file truncated at {per_file_cap} bytes] ...\n".encode()
        if len(raw) > per_file_cap:
            raw = _fit_utf8(raw, max(0, per_file_cap - len(marker))) + marker
        else:
            raw = raw.decode("utf-8", errors="strict").encode("utf-8")
        chunk = f"--- {relative} ---\n".encode() + raw + b"\n"
        remaining = total_cap - used
        if len(chunk) > remaining:
            total_marker = b"... [context stopped at total byte cap] ...\n"
            if remaining >= len(total_marker):
                parts.append(total_marker)
            break
        parts.append(chunk)
        used += len(chunk)
    return b"".join(parts).decode("utf-8")


def build_run_matrix(fixtures: Iterable[Fixture]) -> list[RunCase]:
    return [RunCase(fixture, grounding) for fixture in fixtures for grounding in ("none", "full-file-context")]


def grade_records(records: Iterable[dict]) -> dict:
    records = list(records)
    required = {"blocking", "unavailable", "error"}
    for record in records:
        blocking = record.get("blocking")
        unavailable = record.get("unavailable")
        expected = record.get("expected_blocking", 0)
        valid = (
            required.issubset(record)
            and type(blocking) is int
            and blocking >= 0
            and type(expected) is int
            and expected >= 0
            and type(unavailable) is bool
            and (record["error"] is None or isinstance(record["error"], str))
        )
        record["status"] = (
            "pass"
            if valid and record["error"] is None and unavailable is False and blocking == expected
            else "fail"
        )
    return {"verdict": "pass" if records and all(r["status"] == "pass" for r in records) else "fail"}


def write_evidence(path: Path, payload: dict) -> None:
    _atomic_json(Path(path), payload)
