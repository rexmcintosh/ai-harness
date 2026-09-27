#!/usr/bin/env python3
"""Plan and install a bounded, reversible Claude-to-Codex file mirror."""
from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import filecmp
import hashlib
import json
import os
import shutil
import stat
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PRESERVED_CODEX_SKILLS = {"delegate", "site-flow", "skill-creator"}
LOCK_TIMEOUT_SECONDS = 30.0


class MirrorSafetyError(ValueError):
    """Raised when an inventory-derived name or path would escape its intended root."""


@dataclass(frozen=True)
class Operation:
    kind: str
    source: str
    target: str
    status: str
    description: str = ""
    repo: str | None = None
    root: str = ""


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def slug(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "-" for char in value)
    return "-".join(part for part in safe.split("-") if part)


def validate_segment(value: str) -> str:
    """Reject inventory-derived names that could traverse or escape a path segment."""
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise MirrorSafetyError(f"invalid path segment: {value!r}")
    return value


def validate_command_filename(value: str) -> str:
    if not value.endswith(".md"):
        raise MirrorSafetyError(f"invalid command filename: {value!r}")
    validate_segment(value)
    validate_segment(value[: -len(".md")])
    return value


def ensure_within(root: Path, path: Path) -> Path:
    """Resolve `path` and confirm it stays under `root`, even through existing symlinked parents."""
    root_real = root.resolve()
    resolved = path.resolve()
    if resolved != root_real and root_real not in resolved.parents:
        raise MirrorSafetyError(f"{path} escapes required root {root}")
    return resolved


def same_tree(left: Path, right: Path) -> bool:
    """True when two skill folders hold the same files, links and bytes, so one is a plain duplicate."""
    def entries(root: Path) -> dict[str, tuple[str, ...]]:
        found: dict[str, tuple[str, ...]] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                path = Path(dirpath) / name
                relative = str(path.relative_to(root))
                if path.is_symlink():
                    found[relative] = ("link", os.readlink(path))
                elif path.is_dir():
                    found[relative] = ("dir",)
                elif path.is_file():
                    found[relative] = ("file", str(stat.S_IMODE(path.stat().st_mode) & 0o111))
                else:
                    found[relative] = ("other",)
        return found

    try:
        left_entries, right_entries = entries(left), entries(right)
        if left_entries != right_entries or any(kind == ("other",) for kind in left_entries.values()):
            return False
        return all(filecmp.cmp(left / relative, right / relative, shallow=False)
                   for relative, kind in left_entries.items() if kind[0] == "file")
    except OSError:
        return False


def fingerprint(target: Path) -> tuple[str, ...]:
    """A cheap, comparable snapshot of what a target currently is, used for crash recovery."""
    try:
        if target.is_symlink():
            return ("symlink", os.readlink(target))
        if target.is_dir():
            return ("dir",)
        if target.is_file():
            return ("file", digest(target.read_text(encoding="utf-8")))
        return ("missing",)
    except (OSError, UnicodeDecodeError):
        return ("unreadable",)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}"
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    fsync_dir(path.parent)


def fsync_dir(directory: Path) -> None:
    """Best-effort directory fsync so a rename is durable across power loss.

    This does not by itself guarantee crash consistency of everything under
    `directory` — only that the rename we just performed is recorded.
    """
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


class MirrorLock:
    """A single-writer lock for this mirror's ownership state, held across apply_plan."""

    def __init__(self, path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
        self.path = path
        self.timeout = timeout
        self._handle: Any = None

    def __enter__(self) -> "MirrorLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self._handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._handle.close()
                    raise TimeoutError(f"Could not acquire mirror lock: {self.path}")
                time.sleep(0.1)

    def __exit__(self, *exc_info: object) -> None:
        assert self._handle is not None
        fcntl.flock(self._handle, fcntl.LOCK_UN)
        self._handle.close()


def wrapper(name: str, description: str, source: Path, repo: Path | None = None) -> str:
    scope = ""
    if repo is not None:
        scope = f"\nUse only for `{repo}`, including its worktrees. Verify repository identity if unclear. Apply changes in the active checkout, not the source repository checkout.\n"
    adaptation = ""
    if source.parent.name == "commands" and repo is None:
        if source.stem == "setup-mcp":
            adaptation = "Use current Codex MCP configuration and `codex mcp` commands for this harness. Treat Claude CLI examples as intent; do not configure Claude again unless the user targets Claude. Consult the installed openai-docs and claude-connectors skills as needed.\n"
        elif source.stem in {"install-skill", "create-command", "export-skill"}:
            adaptation = "Default to Codex skills and command equivalents: global skills under ~/.codex/skills and project skills under .agents/skills. Use the installed skill-creator or skill-installer where appropriate. Preserve an explicitly requested Claude target; do not copy Claude-specific configuration syntax into Codex.\n"
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n\n"
        f"# {name}\n"
        f"{scope}\n"
        f"{adaptation}Read `{source}` in full, then follow its instructions for this request.\n"
        "Treat Claude-specific tool names as intent. Use the matching Codex tool when one exists.\n"
        "Keep the canonical source unchanged. This mirror does not copy secrets; use the source workflow existing credential loader.\n"
    )


def owned_entries(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = load_json(path)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported ownership manifest: {path}")
    return {entry["target"]: entry for entry in data.get("entries", [])}


def classify(target: Path, kind: str, source: Path, content: str | None, owned: dict[str, Any]) -> str:
    if not target.exists() and not target.is_symlink():
        return "create"
    prior = owned.get(str(target))
    if prior is None:
        if kind == "symlink" and not target.is_symlink() and target.is_dir() and same_tree(target, source):
            return "replace-identical-copy"
        return "preserve-unmanaged"
    if kind == "symlink":
        if target.is_symlink() and Path(os.readlink(target)) == source:
            return "unchanged"
        if target.is_symlink() and os.readlink(target) == prior.get("source"):
            return "replace-owned"
        return "preserve-owned-modified"
    if kind == "wrapper" and target.is_file() and content is not None:
        try:
            current_hash = digest(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return "preserve-owned-modified"
        if current_hash == digest(content):
            return "unchanged"
        if current_hash == prior.get("content_sha256"):
            return "replace-owned"
    return "preserve-owned-modified"


def reject(ops: list[Operation], source: str, context: str, error: MirrorSafetyError) -> None:
    ops.append(Operation("rejected", source, "", "rejected-invalid-name", f"{context}: {error}"))


def safe_segment(value: str, ops: list[Operation], *, source: str, context: str) -> str | None:
    try:
        return validate_segment(value)
    except MirrorSafetyError as error:
        reject(ops, source, context, error)
        return None


def add_symlink(ops: list[Operation], source: Path, target: Path, owned: dict[str, Any],
                root: Path, repo: Path | None = None) -> None:
    status = "source-missing"
    if source.is_dir() and (source / "SKILL.md").is_file():
        status = classify(target, "symlink", source, None, owned)
    ops.append(Operation("symlink", str(source), str(target), status, repo=str(repo) if repo else None, root=str(root)))


def add_wrapper(ops: list[Operation], name: str, description: str, source: Path, target: Path,
                owned: dict[str, Any], root: Path, repo: Path | None = None) -> None:
    status = "source-missing"
    if source.is_file():
        status = classify(target, "wrapper", source, wrapper(name, description, source, repo), owned)
    ops.append(Operation("wrapper", str(source), str(target), status, description, str(repo) if repo else None, str(root)))


def build_plan(inventory: dict[str, Any], codex_home: Path, projects_root: Path, ownership: Path) -> list[Operation]:
    owned = owned_entries(ownership)
    ops: list[Operation] = []
    skills_root = codex_home / "skills"
    for skill in inventory["claude_global"]["skills"]:
        raw_name = skill["name"]
        if skill["classification"] == "already-present" or raw_name in PRESERVED_CODEX_SKILLS:
            continue
        name = safe_segment(raw_name, ops, source=str(skill.get("path", "")), context="claude_global skill name")
        if name is None:
            continue
        add_symlink(ops, Path(skill["path"]), skills_root / name, owned, skills_root)

    plugins = inventory.get("plugins", {})
    for plugin_ref, enabled in sorted(plugins.get("enabled", {}).items()):
        if not enabled:
            continue
        raw_plugin_name = plugin_ref.split("@", 1)[0]
        plugin_name = safe_segment(raw_plugin_name, ops, source=plugin_ref, context="plugin name")
        if plugin_name is None:
            continue
        active = plugins.get("active_paths", {}).get(plugin_name)
        if not active:
            continue
        declared = set(plugins.get("plugin_skill_names", {}).get(plugin_name, []))
        discovered = {path.parent.name for path in (Path(active) / "skills").glob("*/SKILL.md")}
        for raw_skill_name in sorted(declared | discovered):
            skill_name = safe_segment(raw_skill_name, ops, source=str(Path(active) / "skills" / raw_skill_name),
                                       context="plugin skill name")
            if skill_name is None:
                continue
            if plugin_name == "delegate" and skill_name == "delegate":
                continue
            target_name = skill_name
            if target_name in PRESERVED_CODEX_SKILLS:
                target_name = f"claude-plugin-{plugin_name}-{skill_name}"
            source = Path(active) / "skills" / skill_name
            if plugin_name == "delegate":
                add_wrapper(ops, target_name, f"Use the mirrored delegate {skill_name} workflow in Codex.",
                            source / "SKILL.md", skills_root / target_name / "SKILL.md", owned, skills_root)
            else:
                add_symlink(ops, source, skills_root / target_name, owned, skills_root)

    for raw_command in inventory["claude_global"].get("commands", []):
        command = safe_segment(raw_command, ops, source=raw_command, context="claude command name")
        if command is None:
            continue
        name = f"claude-command-{slug(command)}"
        source = Path.home() / ".claude" / "commands" / f"{command}.md"
        add_wrapper(ops, name, f"Run the mirrored Claude /{command} command in Codex.", source,
                    skills_root / name / "SKILL.md", owned, skills_root)

    projects = inventory.get("project_scoped_all_direct_projects", {})
    for raw_repo_name, commands in sorted(projects.get("project_claude_commands", {}).items()):
        repo_name = safe_segment(raw_repo_name, ops, source=raw_repo_name, context="project repo name")
        if repo_name is None:
            continue
        repo = projects_root / repo_name
        try:
            ensure_within(projects_root, repo)
        except MirrorSafetyError as error:
            reject(ops, str(repo), "project repo path", error)
            continue
        repo_root = repo / ".agents" / "skills"
        for command_file in commands:
            try:
                validate_command_filename(command_file)
            except MirrorSafetyError as error:
                reject(ops, command_file, "project command filename", error)
                continue
            command = Path(command_file).stem
            name = f"repo-command-{slug(command)}"
            source = repo / ".claude" / "commands" / command_file
            add_wrapper(ops, name, f"Run this repository's /{command} command in Codex.", source,
                        repo_root / name / "SKILL.md", owned, repo_root, repo)
    for raw_repo_name, skill_names in sorted(projects.get("project_claude_skills", {}).items()):
        repo_name = safe_segment(raw_repo_name, ops, source=raw_repo_name, context="project repo name")
        if repo_name is None:
            continue
        repo = projects_root / repo_name
        try:
            ensure_within(projects_root, repo)
        except MirrorSafetyError as error:
            reject(ops, str(repo), "project repo path", error)
            continue
        repo_root = repo / ".agents" / "skills"
        for raw_skill_name in skill_names:
            skill_name = safe_segment(raw_skill_name, ops, source=raw_skill_name, context="project skill name")
            if skill_name is None:
                continue
            name = f"repo-skill-{slug(skill_name)}"
            source = repo / ".claude" / "skills" / skill_name / "SKILL.md"
            add_wrapper(ops, name, f"Use this repository's {skill_name} skill in Codex.", source,
                        repo_root / name / "SKILL.md", owned, repo_root, repo)
    return ops


def backup_destination(target: Path, backup_root: Path) -> Path:
    relative = Path(*target.parts[1:]) if target.is_absolute() else target
    destination = backup_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def backup_target(target: Path, backup_root: Path) -> None:
    destination = backup_destination(target, backup_root)
    if target.is_symlink():
        destination.symlink_to(os.readlink(target))
    elif target.is_dir():
        shutil.copytree(target, destination, symlinks=True)
    else:
        shutil.copy2(target, destination, follow_symlinks=False)


def recompute_status(op: Operation, owned: dict[str, Any]) -> tuple[str, str | None]:
    """Classify `op` against the current, lock-held view of the world (not the stale plan)."""
    source = Path(op.source)
    target = Path(op.target)
    if op.kind == "symlink":
        if not (source.is_dir() and (source / "SKILL.md").is_file()):
            return "source-missing", None
        return classify(target, "symlink", source, None, owned), None
    if not source.is_file():
        return "source-missing", None
    repo = Path(op.repo) if op.repo else None
    content = wrapper(target.parent.name, op.description, source, repo)
    return classify(target, "wrapper", source, content, owned), content


def recover_journal(journal_path: Path, ownership: Path) -> None:
    """Finish an operation that mutated its target but crashed before the ownership update.

    Only ever merges the recorded new state; a target left in any other state
    (someone else's edit) is untouched and simply falls through to normal
    preserve-owned-modified / preserve-unmanaged handling on the next classify.
    """
    if not journal_path.exists():
        return
    try:
        entry = json.loads(journal_path.read_text(encoding="utf-8"))
        target = Path(entry["target"])
        new_fp = tuple(entry["new_fingerprint"])
    except (json.JSONDecodeError, OSError, KeyError, TypeError):
        journal_path.unlink(missing_ok=True)
        return
    if fingerprint(target) == new_fp:
        owned = owned_entries(ownership)
        owned[entry["target"]] = entry["owned_record"]
        atomic_write_json(ownership, {
            "schema_version": SCHEMA_VERSION,
            "entries": sorted(owned.values(), key=lambda item: item["target"]),
        })
    journal_path.unlink(missing_ok=True)


def replace_atomically(target: Path, kind: str, source: Path, content: str | None) -> None:
    temporary = target.parent / f".{target.name}.mirror-tmp-{os.getpid()}"
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    if kind == "symlink":
        temporary.symlink_to(source, target_is_directory=True)
    else:
        assert content is not None
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    os.replace(temporary, target)
    fsync_dir(target.parent)


def mirror_lock(ownership: Path) -> MirrorLock:
    return MirrorLock(ownership.with_suffix(ownership.suffix + ".lock"))


def apply_plan(ops: list[Operation], ownership: Path, backup_dir: Path,
               outcomes: list[dict[str, str]] | None = None, *, take_lock: bool = True) -> dict[str, int]:
    """Apply `ops` under the lock. `outcomes`, when given, receives each changed target's final status.

    Pass `take_lock=False` only when the caller already holds `mirror_lock(ownership)`.
    """
    counts = {"created": 0, "replaced": 0, "deduplicated": 0, "unchanged": 0, "preserved": 0, "missing": 0,
              "rejected": 0}
    journal_path = ownership.with_suffix(ownership.suffix + ".journal")
    backup_root = backup_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")

    with mirror_lock(ownership) if take_lock else contextlib.nullcontext():
        recover_journal(journal_path, ownership)
        owned = owned_entries(ownership)

        for op in ops:
            if op.kind == "rejected":
                counts["rejected"] += 1
                if outcomes is not None:
                    # A rejected name never gets a target path; report what was rejected.
                    outcomes.append({"status": op.status, "target": op.target or op.source})
                continue

            status, content = recompute_status(op, owned)
            if status == "source-missing":
                counts["missing"] += 1
                if outcomes is not None:
                    outcomes.append({"status": status, "target": op.target})
                continue
            if status in {"preserve-unmanaged", "preserve-owned-modified"}:
                counts["preserved"] += 1
                continue
            if status == "unchanged":
                counts["unchanged"] += 1
                continue

            source, target = Path(op.source), Path(op.target)
            root = Path(op.root) if op.root else target.parent
            try:
                ensure_within(root, target.parent)
            except MirrorSafetyError:
                counts["rejected"] += 1
                continue

            if status == "replace-owned":
                backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                backup_target(target, backup_root)

            target.parent.mkdir(parents=True, exist_ok=True)
            prior_fp = fingerprint(target)
            if op.kind == "symlink":
                new_fp = ("symlink", str(source))
                owned_record = {"kind": "symlink", "source": str(source), "target": str(target), "content_sha256": None}
            else:
                new_fp = ("file", digest(content or ""))
                owned_record = {"kind": "wrapper", "source": str(source), "target": str(target),
                                 "content_sha256": digest(content or "")}

            journal: dict[str, Any] = {
                "target": str(target),
                "prior_fingerprint": list(prior_fp),
                "new_fingerprint": list(new_fp),
                "owned_record": owned_record,
            }
            if status == "replace-identical-copy":
                # Recheck right before the move: a directory cannot be renamed over, so it moves aside first.
                if target.is_symlink() or not same_tree(target, source):
                    counts["preserved"] += 1
                    continue
                backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                moved_to = backup_destination(target, backup_root)
                journal["moved_to"] = str(moved_to)
            atomic_write_json(journal_path, journal)

            if status == "replace-identical-copy":
                # Rename is atomic. It fails cleanly (for example across filesystems), and then the copy stays.
                # A crash after it leaves the target missing; the next run sees "create" and links it.
                try:
                    os.rename(target, moved_to)
                except OSError as error:
                    journal_path.unlink(missing_ok=True)
                    counts["preserved"] += 1
                    if outcomes is not None:
                        outcomes.append({"status": f"preserve-rename-failed: {error.strerror}", "target": str(target)})
                    continue
                try:
                    fsync_dir(target.parent)
                    fsync_dir(moved_to.parent)
                    replace_atomically(target, op.kind, source, content)
                except OSError:
                    if not target.exists() and not target.is_symlink():
                        os.rename(moved_to, target)
                        journal_path.unlink(missing_ok=True)
                    # Otherwise the link is in place: keep the journal so the next run records ownership.
                    raise
            else:
                replace_atomically(target, op.kind, source, content)

            owned[str(target)] = owned_record
            atomic_write_json(ownership, {
                "schema_version": SCHEMA_VERSION,
                "entries": sorted(owned.values(), key=lambda item: item["target"]),
            })
            journal_path.unlink(missing_ok=True)

            counts[{"create": "created", "replace-identical-copy": "deduplicated"}.get(status, "replaced")] += 1
            if outcomes is not None:
                outcomes.append({"status": status, "target": str(target)})

    return counts


def install_time(entry: dict[str, Any], key: str) -> datetime:
    """Parse an install timestamp; a missing or malformed one sorts as oldest."""
    try:
        parsed = datetime.fromisoformat(str(entry[key]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def live_inventory(inventory: dict[str, Any], claude_home: Path) -> dict[str, Any]:
    """Replace the snapshot's global skills, commands and plugins with what Claude has installed now.

    Plugin paths are versioned, so a frozen snapshot goes stale on every plugin update.
    Project-scoped sections still come from the snapshot.
    """
    skills_dir = claude_home / "skills"
    if not skills_dir.is_dir():
        raise ValueError(f"Claude skills folder not found: {skills_dir}")
    data = copy.deepcopy(inventory)
    global_part = data.setdefault("claude_global", {})
    global_part["skills"] = [
        {"name": path.parent.name, "path": str(path.parent), "classification": "live"}
        for path in sorted(skills_dir.glob("*/SKILL.md")) if not path.parent.name.startswith(".")
    ]
    global_part["commands"] = sorted(path.stem for path in (claude_home / "commands").glob("*.md"))

    def optional_json(path: Path) -> dict[str, Any]:
        # A fresh Claude install has no plugin file yet; a malformed file still stops the run.
        return load_json(path) if path.exists() else {}

    installed = optional_json(claude_home / "plugins" / "installed_plugins.json").get("plugins", {})
    enabled = optional_json(claude_home / "settings.json").get("enabledPlugins", {})
    base_names = [plugin_ref.split("@", 1)[0] for plugin_ref, is_enabled in enabled.items() if is_enabled is True]
    clashes = sorted({name for name in base_names if base_names.count(name) > 1})
    if clashes:
        raise ValueError(f"Enabled plugins from different marketplaces share a name: {', '.join(clashes)}")
    active_paths: dict[str, str] = {}
    for plugin_ref, is_enabled in sorted(enabled.items()):
        if is_enabled is not True:
            continue
        candidates = [entry for entry in installed.get(plugin_ref, [])
                      if entry.get("scope") == "user" and entry.get("installPath")
                      and Path(entry["installPath"]).is_dir()]
        if candidates:
            newest = max(candidates, key=lambda entry: (install_time(entry, "lastUpdated"),
                                                         install_time(entry, "installedAt"), entry["installPath"]))
            active_paths[plugin_ref.split("@", 1)[0]] = newest["installPath"]
    data["plugins"] = {
        "enabled": {plugin_ref: is_enabled is True for plugin_ref, is_enabled in enabled.items()},
        "active_paths": active_paths,
        "plugin_skill_names": {},
    }
    return data


def persist_inventory(inventory: dict[str, Any], path: Path) -> None:
    atomic_write_json(path, {"schema_version": SCHEMA_VERSION, "inventory": inventory})


def main(argv: list[str] | None = None) -> int:
    try:
        return run_main(argv)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Mirror stopped: {error}", file=sys.stderr)
        return 2


def run_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, help="Fresh inventory JSON; defaults to the saved installed inventory.")
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--claude-home", type=Path, default=Path.home() / ".claude")
    parser.add_argument("--frozen", action="store_true",
                        help="Plan from the inventory alone; skip reading Claude's current skills and plugins.")
    parser.add_argument("--projects-root", type=Path, default=Path.home() / "projects")
    parser.add_argument("--ownership", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="Print one summary line (for scheduled runs).")
    args = parser.parse_args(argv)
    ownership = args.ownership or args.codex_home / "claude-mirror-owned.json"
    backup_dir = args.backup_dir or args.codex_home / "backups" / "claude-mirror"
    saved_inventory = args.codex_home / "mirrors" / "claude" / "inventory.json"
    inventory_path = args.inventory or (saved_inventory if saved_inventory.exists() else Path("/tmp/claude-skill-inventory.json"))
    data = load_json(inventory_path)
    if "inventory" in data:
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Unsupported inventory schema: {inventory_path}")
        data = data["inventory"]
    quiet_statuses = {"unchanged", "preserve-unmanaged", "preserve-owned-modified"}
    # Apply reads Claude's state, plans and applies under one lock, so a run that waited
    # behind another cannot apply an older plan over newer links.
    with mirror_lock(ownership) if args.apply else contextlib.nullcontext():
        if not args.frozen:
            data = live_inventory(data, args.claude_home)
        ops = build_plan(data, args.codex_home, args.projects_root, ownership)
        result: dict[str, Any] = {"mode": "plan", "operations": [asdict(item) for item in ops]}
        changes = [{"status": item.status, "target": item.target} for item in ops
                   if item.status not in quiet_statuses]
        if args.apply:
            result["mode"] = "apply"
            changes = []
            result["applied"] = apply_plan(ops, ownership, backup_dir, changes, take_lock=False)
            persist_inventory(data, saved_inventory)
    if args.quiet:
        summary = {
            "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mode": result["mode"],
            "applied": result.get("applied"),
            "changes": changes,
        }
        json.dump(summary, sys.stdout, sort_keys=True)
    else:
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
