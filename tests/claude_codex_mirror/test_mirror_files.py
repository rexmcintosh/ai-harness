import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[2] / "setup" / "claude-codex-mirror" / "mirror_files.py"
SPEC = importlib.util.spec_from_file_location("mirror_files", MODULE_PATH)
mirror = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = mirror
SPEC.loader.exec_module(mirror)


def make_skill(path: Path, name: str = "sample") -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Test.\n---\n", encoding="utf-8")


def tree_hash(root: Path) -> str:
    sha = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            sha.update(str(path.relative_to(root)).encode())
            sha.update(path.read_bytes())
    return sha.hexdigest()


def inventory(skill: Path, projects: Path, command: Path) -> dict:
    repo = projects / "demo"
    project_command = repo / ".claude" / "commands" / "build.md"
    project_command.parent.mkdir(parents=True)
    project_command.write_text("Build the project.\n", encoding="utf-8")
    project_skill = repo / ".claude" / "skills" / "verify"
    make_skill(project_skill, "verify")
    return {
        "claude_global": {
            "skills": [
                {"name": "portable", "path": str(skill), "classification": "needs-adaptation"},
                {"name": "cloudflare", "path": "/unused", "classification": "already-present"},
            ],
            "commands": [command.stem],
        },
        "plugins": {"enabled": {}, "active_paths": {}, "plugin_skill_names": {}},
        "project_scoped_all_direct_projects": {
            "project_claude_commands": {"demo": ["build.md"]},
            "project_claude_skills": {"demo": ["verify"]},
        },
    }


def test_plan_apply_and_reapply_leave_sources_unchanged(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    source = fake_home / ".claude" / "skills" / "portable"
    make_skill(source, "portable")
    (source / ".env").write_text("TOKEN=do-not-copy\n", encoding="utf-8")
    command = fake_home / ".claude" / "commands" / "close.md"
    command.parent.mkdir(parents=True)
    command.write_text("Close safely.\n", encoding="utf-8")
    projects = tmp_path / "projects"
    data = inventory(source, projects, command)
    codex_home = fake_home / ".codex"
    ownership = codex_home / "claude-mirror-owned.json"
    before = tree_hash(fake_home / ".claude")

    plan = mirror.build_plan(data, codex_home, projects, ownership)
    assert {item.status for item in plan} == {"create"}
    result = mirror.apply_plan(plan, ownership, codex_home / "backups")
    assert result["created"] == 4
    target = codex_home / "skills" / "portable"
    assert target.is_symlink()
    assert os.readlink(target) == str(source)
    wrapper = codex_home / "skills" / "claude-command-close" / "SKILL.md"
    assert str(command) in wrapper.read_text()
    assert "do-not-copy" not in ownership.read_text()
    assert tree_hash(fake_home / ".claude") == before

    second = mirror.build_plan(data, codex_home, projects, ownership)
    assert {item.status for item in second} == {"unchanged"}
    result = mirror.apply_plan(second, ownership, codex_home / "backups")
    assert result["unchanged"] == 4


def test_unmanaged_target_is_preserved(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    source = fake_home / ".claude" / "skills" / "portable"
    make_skill(source)
    command = fake_home / ".claude" / "commands" / "close.md"
    command.parent.mkdir(parents=True)
    command.write_text("source\n")
    projects = tmp_path / "projects"
    data = inventory(source, projects, command)
    codex_home = fake_home / ".codex"
    target = codex_home / "skills" / "portable"
    make_skill(target, "owner-copy")

    plan = mirror.build_plan(data, codex_home, projects, codex_home / "owned.json")
    item = next(item for item in plan if item.target == str(target))
    assert item.status == "preserve-unmanaged"
    mirror.apply_plan(plan, codex_home / "owned.json", codex_home / "backups")
    assert not target.is_symlink()
    assert "owner-copy" in (target / "SKILL.md").read_text()


def test_manually_edited_owned_wrapper_is_preserved(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    source = fake_home / ".claude" / "skills" / "portable"
    make_skill(source)
    command = fake_home / ".claude" / "commands" / "close.md"
    command.parent.mkdir(parents=True)
    command.write_text("source\n")
    projects = tmp_path / "projects"
    data = inventory(source, projects, command)
    codex_home = fake_home / ".codex"
    ownership = codex_home / "owned.json"
    mirror.apply_plan(mirror.build_plan(data, codex_home, projects, ownership), ownership, codex_home / "backups")
    target = codex_home / "skills" / "claude-command-close" / "SKILL.md"
    target.write_text("owner-edited\n")

    plan = mirror.build_plan(data, codex_home, projects, ownership)
    item = next(item for item in plan if item.target == str(target))
    assert item.status == "preserve-owned-modified"
    result = mirror.apply_plan(plan, ownership, codex_home / "backups")
    assert result["preserved"] == 1
    assert target.read_text() == "owner-edited\n"


def test_owned_link_source_change_is_backed_up(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    first = fake_home / ".claude" / "skills" / "portable"
    second = fake_home / ".claude" / "skills" / "portable-v2"
    make_skill(first)
    make_skill(second)
    command = fake_home / ".claude" / "commands" / "close.md"
    command.parent.mkdir(parents=True)
    command.write_text("source\n")
    projects = tmp_path / "projects"
    data = inventory(first, projects, command)
    codex_home = fake_home / ".codex"
    ownership = codex_home / "owned.json"
    mirror.apply_plan(mirror.build_plan(data, codex_home, projects, ownership), ownership, codex_home / "backups")
    data["claude_global"]["skills"][0]["path"] = str(second)

    plan = mirror.build_plan(data, codex_home, projects, ownership)
    item = next(item for item in plan if item.target.endswith("/portable"))
    assert item.status == "replace-owned"
    mirror.apply_plan(plan, ownership, codex_home / "backups")
    assert Path(item.target).resolve() == second.resolve()
    assert list((codex_home / "backups").rglob("portable"))


def test_discovers_frontend_skill_from_active_plugin(tmp_path):
    plugin = tmp_path / "frontend-active"
    make_skill(plugin / "skills" / "frontend-design", "frontend-design")
    inventory_data = {
        "claude_global": {"skills": [], "commands": []},
        "plugins": {
            "enabled": {"frontend-design@official": True},
            "active_paths": {"frontend-design": str(plugin)},
            "plugin_skill_names": {},
        },
        "project_scoped_all_direct_projects": {},
    }
    codex_home = tmp_path / "codex"
    plan = mirror.build_plan(inventory_data, codex_home, tmp_path / "projects", codex_home / "owned.json")
    assert [(item.source, item.target, item.status) for item in plan] == [
        (str(plugin / "skills" / "frontend-design"), str(codex_home / "skills" / "frontend-design"), "create")
    ]


def test_traversal_skill_name_is_rejected_and_nothing_written(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    source = fake_home / ".claude" / "skills" / "portable"
    make_skill(source)
    command = fake_home / ".claude" / "commands" / "close.md"
    command.parent.mkdir(parents=True)
    command.write_text("source\n")
    projects = tmp_path / "projects"
    data = inventory(source, projects, command)
    data["claude_global"]["skills"].append(
        {"name": "../../escaped", "path": str(source), "classification": "needs-adaptation"}
    )
    codex_home = fake_home / ".codex"
    ownership = codex_home / "owned.json"

    plan = mirror.build_plan(data, codex_home, projects, ownership)
    rejected = [item for item in plan if item.status == "rejected-invalid-name"]
    assert any("escaped" in item.description for item in rejected)
    assert all(item.target == "" for item in rejected)

    result = mirror.apply_plan(plan, ownership, codex_home / "backups")
    assert result["rejected"] >= 1
    assert not list(tmp_path.rglob("escaped"))


def test_traversal_repo_name_and_command_filename_are_rejected(tmp_path):
    codex_home = tmp_path / "codex"
    projects = tmp_path / "projects"
    data = {
        "claude_global": {"skills": [], "commands": []},
        "plugins": {"enabled": {}, "active_paths": {}, "plugin_skill_names": {}},
        "project_scoped_all_direct_projects": {
            "project_claude_commands": {"../../etc": ["passwd.md"], "demo": ["../escape.md", "ok.md"]},
            "project_claude_skills": {},
        },
    }
    (projects / "demo" / ".claude" / "commands").mkdir(parents=True)
    (projects / "demo" / ".claude" / "commands" / "ok.md").write_text("Ok.\n")

    plan = mirror.build_plan(data, codex_home, projects, codex_home / "owned.json")
    rejected_descriptions = " ".join(item.description for item in plan if item.status == "rejected-invalid-name")
    assert "../../etc" in rejected_descriptions
    assert "../escape.md" in rejected_descriptions
    assert any(item.status != "rejected-invalid-name" for item in plan if "ok" in item.target)
    assert not (tmp_path / "etc").exists()


def test_plugin_name_with_separator_is_rejected(tmp_path):
    plugin = tmp_path / "frontend-active"
    make_skill(plugin / "skills" / "frontend-design", "frontend-design")
    inventory_data = {
        "claude_global": {"skills": [], "commands": []},
        "plugins": {
            "enabled": {"../escape@official": True},
            "active_paths": {"../escape": str(plugin)},
            "plugin_skill_names": {},
        },
        "project_scoped_all_direct_projects": {},
    }
    codex_home = tmp_path / "codex"
    plan = mirror.build_plan(inventory_data, codex_home, tmp_path / "projects", codex_home / "owned.json")
    assert {item.status for item in plan} == {"rejected-invalid-name"}


def test_symlinked_parent_cannot_redirect_wrapper_write(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    source = fake_home / ".claude" / "skills" / "portable"
    make_skill(source)
    command = fake_home / ".claude" / "commands" / "close.md"
    command.parent.mkdir(parents=True)
    command.write_text("source\n")
    projects = tmp_path / "projects"
    data = inventory(source, projects, command)
    codex_home = fake_home / ".codex"
    ownership = codex_home / "owned.json"

    outside = tmp_path / "outside-escape"
    outside.mkdir()
    skills_root = codex_home / "skills"
    skills_root.mkdir(parents=True)
    (skills_root / "claude-command-close").symlink_to(outside, target_is_directory=True)

    plan = mirror.build_plan(data, codex_home, projects, ownership)
    result = mirror.apply_plan(plan, ownership, codex_home / "backups")

    assert result["rejected"] >= 1
    assert not (outside / "SKILL.md").exists()


def test_stale_plan_does_not_overwrite_concurrent_owner_edit(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    source = fake_home / ".claude" / "skills" / "portable"
    make_skill(source)
    command = fake_home / ".claude" / "commands" / "close.md"
    command.parent.mkdir(parents=True)
    command.write_text("source\n")
    projects = tmp_path / "projects"
    data = inventory(source, projects, command)
    codex_home = fake_home / ".codex"
    ownership = codex_home / "owned.json"

    plan = mirror.build_plan(data, codex_home, projects, ownership)  # computed as all "create"

    target = codex_home / "skills" / "portable"
    target.parent.mkdir(parents=True, exist_ok=True)
    make_skill(target, "someone-elses-copy")  # a concurrent owner claims the target first

    result = mirror.apply_plan(plan, ownership, codex_home / "backups")

    assert not target.is_symlink()
    assert "someone-elses-copy" in (target / "SKILL.md").read_text()
    assert result["preserved"] >= 1
    assert str(target) not in mirror.owned_entries(ownership)


def test_recovery_finalizes_ownership_after_crash_before_manifest_write(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    source = fake_home / ".claude" / "skills" / "portable"
    make_skill(source)
    command = fake_home / ".claude" / "commands" / "close.md"
    command.parent.mkdir(parents=True)
    command.write_text("source\n")
    projects = tmp_path / "projects"
    data = inventory(source, projects, command)
    codex_home = fake_home / ".codex"
    ownership = codex_home / "owned.json"
    target = codex_home / "skills" / "portable"

    # Simulate a crash: the symlink was created (and journaled) but the
    # ownership manifest write never ran.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=True)
    journal_path = ownership.with_suffix(ownership.suffix + ".journal")
    owned_record = {"kind": "symlink", "source": str(source), "target": str(target), "content_sha256": None}
    mirror.atomic_write_json(journal_path, {
        "target": str(target),
        "prior_fingerprint": ["missing"],
        "new_fingerprint": ["symlink", str(source)],
        "owned_record": owned_record,
    })
    assert not ownership.exists()

    mirror.recover_journal(journal_path, ownership)

    assert not journal_path.exists()
    assert mirror.owned_entries(ownership)[str(target)]["source"] == str(source)

    second_plan = mirror.build_plan(data, codex_home, projects, ownership)
    item = next(i for i in second_plan if i.target == str(target))
    assert item.status == "unchanged"


def test_recovery_leaves_foreign_edit_untouched(tmp_path):
    codex_home = tmp_path / "codex"
    ownership = codex_home / "owned.json"
    journal_path = ownership.with_suffix(ownership.suffix + ".journal")
    target = codex_home / "skills" / "portable"
    target.parent.mkdir(parents=True)
    other_source = tmp_path / "someone-elses-source"
    other_source.mkdir()
    target.symlink_to(other_source)  # neither the recorded prior nor new state

    expected_source = tmp_path / "expected-source"
    mirror.atomic_write_json(journal_path, {
        "target": str(target),
        "prior_fingerprint": ["missing"],
        "new_fingerprint": ["symlink", str(expected_source)],
        "owned_record": {"kind": "symlink", "source": str(expected_source), "target": str(target), "content_sha256": None},
    })

    mirror.recover_journal(journal_path, ownership)

    assert not journal_path.exists()
    assert not ownership.exists()
    assert os.readlink(target) == str(other_source)


def test_recovery_noop_when_mutation_never_happened(tmp_path):
    codex_home = tmp_path / "codex"
    ownership = codex_home / "owned.json"
    journal_path = ownership.with_suffix(ownership.suffix + ".journal")
    target = codex_home / "skills" / "portable"
    # target never got created before the crash.

    mirror.atomic_write_json(journal_path, {
        "target": str(target),
        "prior_fingerprint": ["missing"],
        "new_fingerprint": ["symlink", "/some/source"],
        "owned_record": {"kind": "symlink", "source": "/some/source", "target": str(target), "content_sha256": None},
    })

    mirror.recover_journal(journal_path, ownership)

    assert not journal_path.exists()
    assert not ownership.exists()
    assert not target.exists()


def test_concurrent_apply_is_serialized_by_lock(tmp_path):
    lock_path = tmp_path / "owned.json.lock"
    holder = mirror.MirrorLock(lock_path, timeout=1)
    with holder:
        contender = mirror.MirrorLock(lock_path, timeout=0.2)
        with pytest.raises(TimeoutError):
            with contender:
                pass  # pragma: no cover - must not be reached


def test_refresh_uses_saved_inventory_when_original_was_temporary(tmp_path, capsys):
    codex_home = tmp_path / "codex"
    source = tmp_path / "portable"
    make_skill(source)
    data = {"claude_global": {"skills": [{"name": "portable", "path": str(source), "classification": "needs-adaptation"}], "commands": []}}
    mirror.persist_inventory(data, codex_home / "mirrors" / "claude" / "inventory.json")
    assert mirror.main(["--frozen", "--codex-home", str(codex_home), "--projects-root", str(tmp_path / "projects")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["operations"]) == 1
    assert result["operations"][0]["source"] == str(source)
    assert result["operations"][0]["status"] == "create"


def test_non_utf8_owned_wrapper_is_preserved(tmp_path):
    target=tmp_path/"SKILL.md"
    target.write_bytes(b"\xff\xfeowner edit")
    before=target.read_bytes()
    owned={str(target):{"content_sha256":mirror.digest("original")}}
    assert mirror.classify(target,"wrapper",tmp_path/"source", "replacement",owned)=="preserve-owned-modified"
    assert mirror.fingerprint(target)==("unreadable",)
    assert target.read_bytes()==before


def test_bad_inventory_has_controlled_error(tmp_path,capsys):
    inventory_path=tmp_path/"invalid.json"
    inventory_path.write_text("{broken")
    assert mirror.main(["--inventory",str(inventory_path),"--codex-home",str(tmp_path/"codex")])==2
    error=capsys.readouterr().err
    assert "Mirror stopped:" in error
    assert "Traceback" not in error


def make_claude_home(root: Path, plugins: dict, enabled: dict) -> Path:
    claude_home = root / ".claude"
    (claude_home / "skills").mkdir(parents=True)
    (claude_home / "commands").mkdir(parents=True)
    (claude_home / "plugins").mkdir(parents=True)
    (claude_home / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": plugins}), encoding="utf-8")
    (claude_home / "settings.json").write_text(json.dumps({"enabledPlugins": enabled}), encoding="utf-8")
    return claude_home


def test_live_inventory_uses_installed_plugin_version_and_current_claude_files(tmp_path):
    old = tmp_path / "cache" / "superpowers" / "6.3.0"
    new = tmp_path / "cache" / "superpowers" / "6.4.1"
    make_skill(old / "skills" / "brainstorming", "brainstorming")
    make_skill(new / "skills" / "brainstorming", "brainstorming")
    disabled = tmp_path / "cache" / "telegram" / "0.0.7"
    make_skill(disabled / "skills" / "access", "access")
    claude_home = make_claude_home(tmp_path, {
        "superpowers@official": [
            {"scope": "project", "installPath": str(old)},
            {"scope": "user", "installPath": str(new)},
        ],
        "telegram@official": [{"scope": "user", "installPath": str(disabled)}],
    }, {"superpowers@official": True, "telegram@official": False})
    make_skill(claude_home / "skills" / "grilling", "grilling")
    (claude_home / "skills" / "synced" / "bucket").mkdir(parents=True)
    (claude_home / "commands" / "close.md").write_text("Close.\n", encoding="utf-8")
    frozen = {
        "claude_global": {"skills": [{"name": "gone", "path": "/nowhere", "classification": "needs-adaptation"}],
                          "commands": ["gone"]},
        "plugins": {"enabled": {"superpowers@official": True},
                    "active_paths": {"superpowers": str(old)}, "plugin_skill_names": {}},
        "project_scoped_all_direct_projects": {"project_claude_commands": {"demo": ["build.md"]}},
    }

    live = mirror.live_inventory(frozen, claude_home)

    assert [skill["name"] for skill in live["claude_global"]["skills"]] == ["grilling"]
    assert live["claude_global"]["commands"] == ["close"]
    assert live["plugins"]["active_paths"] == {"superpowers": str(new)}
    assert live["project_scoped_all_direct_projects"] == frozen["project_scoped_all_direct_projects"]
    assert frozen["plugins"]["active_paths"] == {"superpowers": str(old)}


def test_live_inventory_requires_claude_skills_folder(tmp_path):
    with pytest.raises(ValueError, match="Claude skills folder not found"):
        mirror.live_inventory({}, tmp_path / "missing")


def test_stale_and_broken_plugin_links_follow_the_installed_version(tmp_path):
    old = tmp_path / "cache" / "superpowers" / "6.3.0"
    new = tmp_path / "cache" / "superpowers" / "6.4.1"
    gone = tmp_path / "cache" / "frontend-design" / "85cce"
    current = tmp_path / "cache" / "frontend-design" / "fa59b"
    make_skill(old / "skills" / "brainstorming", "brainstorming")
    make_skill(new / "skills" / "brainstorming", "brainstorming")
    make_skill(gone / "skills" / "frontend-design", "frontend-design")
    make_skill(current / "skills" / "frontend-design", "frontend-design")
    codex_home = tmp_path / "codex"
    ownership = codex_home / "owned.json"

    def data(superpowers: Path, frontend: Path) -> dict:
        return {
            "claude_global": {"skills": [], "commands": []},
            "plugins": {"enabled": {"superpowers@o": True, "frontend-design@o": True},
                        "active_paths": {"superpowers": str(superpowers), "frontend-design": str(frontend)},
                        "plugin_skill_names": {}},
        }

    mirror.apply_plan(mirror.build_plan(data(old, gone), codex_home, tmp_path / "p", ownership),
                      ownership, codex_home / "backups")
    import shutil
    shutil.rmtree(gone)
    assert not (codex_home / "skills" / "frontend-design").exists()

    plan = mirror.build_plan(data(new, current), codex_home, tmp_path / "p", ownership)
    assert {item.status for item in plan} == {"replace-owned"}
    result = mirror.apply_plan(plan, ownership, codex_home / "backups")
    assert result["replaced"] == 2
    assert (codex_home / "skills" / "brainstorming").resolve() == (new / "skills" / "brainstorming").resolve()
    assert (codex_home / "skills" / "frontend-design").resolve() == (current / "skills" / "frontend-design").resolve()


def test_identical_unmanaged_copy_becomes_a_link_and_is_backed_up(tmp_path):
    source = tmp_path / "claude" / "skills" / "wrangler"
    make_skill(source, "wrangler")
    (source / "references").mkdir()
    (source / "references" / "cli.md").write_text("wrangler deploy\n", encoding="utf-8")
    codex_home = tmp_path / "codex"
    copy = codex_home / "skills" / "wrangler"
    shutil_copy(source, copy)
    ownership = codex_home / "owned.json"
    data = {"claude_global": {"skills": [{"name": "wrangler", "path": str(source), "classification": "live"}],
                              "commands": []}}

    plan = mirror.build_plan(data, codex_home, tmp_path / "p", ownership)
    assert [item.status for item in plan] == ["replace-identical-copy"]
    result = mirror.apply_plan(plan, ownership, codex_home / "backups")

    assert result["deduplicated"] == 1
    assert copy.is_symlink() and os.readlink(copy) == str(source)
    backups = [path for path in (codex_home / "backups").rglob("wrangler") if path.is_dir() and not path.is_symlink()]
    assert len(backups) == 1 and (backups[0] / "references" / "cli.md").read_text() == "wrangler deploy\n"
    owned = json.loads(ownership.read_text())["entries"]
    assert owned == [{"content_sha256": None, "kind": "symlink", "source": str(source), "target": str(copy)}]
    again = mirror.build_plan(data, codex_home, tmp_path / "p", ownership)
    assert [item.status for item in again] == ["unchanged"]


def test_different_unmanaged_copy_is_preserved(tmp_path):
    source = tmp_path / "claude" / "skills" / "wrangler"
    make_skill(source, "wrangler")
    codex_home = tmp_path / "codex"
    copy = codex_home / "skills" / "wrangler"
    shutil_copy(source, copy)
    (copy / "local-note.md").write_text("Codex-only edit\n", encoding="utf-8")
    ownership = codex_home / "owned.json"
    data = {"claude_global": {"skills": [{"name": "wrangler", "path": str(source), "classification": "live"}],
                              "commands": []}}

    plan = mirror.build_plan(data, codex_home, tmp_path / "p", ownership)
    assert [item.status for item in plan] == ["preserve-unmanaged"]
    mirror.apply_plan(plan, ownership, codex_home / "backups")
    assert not copy.is_symlink()
    assert (copy / "local-note.md").exists()


def test_copy_that_changes_after_planning_is_not_replaced(tmp_path):
    source = tmp_path / "claude" / "skills" / "wrangler"
    make_skill(source, "wrangler")
    codex_home = tmp_path / "codex"
    copy = codex_home / "skills" / "wrangler"
    shutil_copy(source, copy)
    ownership = codex_home / "owned.json"
    data = {"claude_global": {"skills": [{"name": "wrangler", "path": str(source), "classification": "live"}],
                              "commands": []}}
    plan = mirror.build_plan(data, codex_home, tmp_path / "p", ownership)
    (copy / "SKILL.md").write_text("edited after planning\n", encoding="utf-8")

    result = mirror.apply_plan(plan, ownership, codex_home / "backups")
    assert result["preserved"] == 1
    assert (copy / "SKILL.md").read_text() == "edited after planning\n"


def test_main_live_mode_reads_claude_home_and_quiet_prints_one_line(tmp_path, capsys):
    plugin = tmp_path / "cache" / "typesafe" / "0.5.7"
    make_skill(plugin / "skills" / "typesafe-ai", "typesafe-ai")
    claude_home = make_claude_home(tmp_path, {"typesafe@t": [{"scope": "user", "installPath": str(plugin)}]},
                                   {"typesafe@t": True})
    codex_home = tmp_path / "codex"
    mirror.persist_inventory({"claude_global": {"skills": [], "commands": []}},
                             codex_home / "mirrors" / "claude" / "inventory.json")
    args = ["--codex-home", str(codex_home), "--claude-home", str(claude_home),
            "--projects-root", str(tmp_path / "projects"), "--apply", "--quiet"]

    assert mirror.main(args) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    summary = json.loads(lines[0])
    assert summary["applied"]["created"] == 1
    assert summary["changes"] == [{"status": "create", "target": str(codex_home / "skills" / "typesafe-ai")}]
    assert (codex_home / "skills" / "typesafe-ai").is_symlink()
    saved = json.loads((codex_home / "mirrors" / "claude" / "inventory.json").read_text())["inventory"]
    assert saved["plugins"]["active_paths"] == {"typesafe": str(plugin)}


def shutil_copy(source: Path, target: Path) -> None:
    import shutil
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)


def test_live_inventory_skips_missing_install_and_picks_newest_user_install(tmp_path):
    missing = tmp_path / "cache" / "delegate" / "0.3.0"
    older = tmp_path / "cache" / "delegate" / "0.4.0"
    newer = tmp_path / "cache" / "delegate" / "0.5.0"
    make_skill(older / "skills" / "codex", "codex")
    make_skill(newer / "skills" / "codex", "codex")
    claude_home = make_claude_home(tmp_path, {"delegate@ah": [
        {"scope": "user", "installPath": str(newer), "lastUpdated": "2026-09-20T00:00:00Z"},
        {"scope": "user", "installPath": str(missing), "lastUpdated": "2026-09-25T00:00:00Z"},
        {"scope": "user", "installPath": str(older), "lastUpdated": "2026-09-01T00:00:00Z"},
    ]}, {"delegate@ah": True})

    live = mirror.live_inventory({}, claude_home)
    assert live["plugins"]["active_paths"] == {"delegate": str(newer)}


def test_failed_link_after_moving_duplicate_restores_the_copy(tmp_path, monkeypatch):
    source = tmp_path / "claude" / "skills" / "wrangler"
    make_skill(source, "wrangler")
    codex_home = tmp_path / "codex"
    copy = codex_home / "skills" / "wrangler"
    shutil_copy(source, copy)
    ownership = codex_home / "owned.json"
    data = {"claude_global": {"skills": [{"name": "wrangler", "path": str(source), "classification": "live"}],
                              "commands": []}}
    plan = mirror.build_plan(data, codex_home, tmp_path / "p", ownership)

    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(mirror, "replace_atomically", fail)
    with pytest.raises(OSError):
        mirror.apply_plan(plan, ownership, codex_home / "backups")

    assert copy.is_dir() and not copy.is_symlink()
    assert (copy / "SKILL.md").exists()
    assert not ownership.exists()
    assert not ownership.with_suffix(".json.journal").exists()


def test_source_change_after_planning_keeps_the_copy(tmp_path):
    source = tmp_path / "claude" / "skills" / "wrangler"
    make_skill(source, "wrangler")
    codex_home = tmp_path / "codex"
    copy = codex_home / "skills" / "wrangler"
    shutil_copy(source, copy)
    ownership = codex_home / "owned.json"
    data = {"claude_global": {"skills": [{"name": "wrangler", "path": str(source), "classification": "live"}],
                              "commands": []}}
    plan = mirror.build_plan(data, codex_home, tmp_path / "p", ownership)
    (source / "SKILL.md").write_text("updated upstream\n", encoding="utf-8")

    result = mirror.apply_plan(plan, ownership, codex_home / "backups")
    assert result["preserved"] == 1 and result["deduplicated"] == 0
    assert copy.is_dir() and not copy.is_symlink()


def test_quiet_apply_reports_final_outcome_not_the_plan(tmp_path, monkeypatch, capsys):
    source_home = tmp_path / "home"
    claude_home = make_claude_home(source_home, {}, {})
    make_skill(claude_home / "skills" / "wrangler", "wrangler")
    codex_home = tmp_path / "codex"
    copy = codex_home / "skills" / "wrangler"
    shutil_copy(claude_home / "skills" / "wrangler", copy)
    mirror.persist_inventory({"claude_global": {"skills": [], "commands": []}},
                             codex_home / "mirrors" / "claude" / "inventory.json")
    real_apply = mirror.apply_plan

    def edit_then_apply(ops, *args, **kwargs):
        (copy / "SKILL.md").write_text("edited between plan and apply\n", encoding="utf-8")
        return real_apply(ops, *args, **kwargs)
    monkeypatch.setattr(mirror, "apply_plan", edit_then_apply)

    assert mirror.main(["--codex-home", str(codex_home), "--claude-home", str(claude_home),
                        "--projects-root", str(tmp_path / "projects"), "--apply", "--quiet"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["changes"] == []
    assert summary["applied"]["preserved"] == 1
    assert not copy.is_symlink()


def test_failure_after_link_is_placed_keeps_journal_and_next_run_records_ownership(tmp_path, monkeypatch):
    source = tmp_path / "claude" / "skills" / "wrangler"
    make_skill(source, "wrangler")
    codex_home = tmp_path / "codex"
    copy = codex_home / "skills" / "wrangler"
    shutil_copy(source, copy)
    ownership = codex_home / "owned.json"
    data = {"claude_global": {"skills": [{"name": "wrangler", "path": str(source), "classification": "live"}],
                              "commands": []}}
    real_replace = mirror.replace_atomically

    def replace_then_fail(*args, **kwargs):
        real_replace(*args, **kwargs)
        raise OSError("fsync failed")
    monkeypatch.setattr(mirror, "replace_atomically", replace_then_fail)
    with pytest.raises(OSError):
        mirror.apply_plan(mirror.build_plan(data, codex_home, tmp_path / "p", ownership), ownership,
                          codex_home / "backups")
    assert copy.is_symlink()
    assert ownership.with_suffix(".json.journal").exists()

    monkeypatch.setattr(mirror, "replace_atomically", real_replace)
    result = mirror.apply_plan(mirror.build_plan(data, codex_home, tmp_path / "p", ownership), ownership,
                               codex_home / "backups")
    assert result["unchanged"] == 1
    assert str(copy) in mirror.owned_entries(ownership)


def test_malformed_install_time_sorts_as_oldest(tmp_path):
    good = tmp_path / "cache" / "p" / "2.0.0"
    odd = tmp_path / "cache" / "p" / "9.9.9"
    make_skill(good / "skills" / "s", "s")
    make_skill(odd / "skills" / "s", "s")
    claude_home = make_claude_home(tmp_path, {"p@m": [
        {"scope": "user", "installPath": str(odd), "lastUpdated": "not-a-date"},
        {"scope": "user", "installPath": str(good), "lastUpdated": "2026-09-22T20:57:22.364Z"},
    ]}, {"p@m": True})
    assert mirror.live_inventory({}, claude_home)["plugins"]["active_paths"] == {"p": str(good)}


def test_same_plugin_name_from_two_marketplaces_stops_the_run(tmp_path):
    first = tmp_path / "cache" / "a" / "foo" / "1"
    second = tmp_path / "cache" / "b" / "foo" / "1"
    make_skill(first / "skills" / "s", "s")
    make_skill(second / "skills" / "s", "s")
    claude_home = make_claude_home(tmp_path, {
        "foo@official": [{"scope": "user", "installPath": str(first)}],
        "foo@internal": [{"scope": "user", "installPath": str(second)}],
    }, {"foo@official": True, "foo@internal": True})
    with pytest.raises(ValueError, match="share a name: foo"):
        mirror.live_inventory({}, claude_home)


def test_fresh_claude_home_without_plugin_files_is_empty_not_an_error(tmp_path):
    claude_home = tmp_path / ".claude"
    make_skill(claude_home / "skills" / "grilling", "grilling")
    live = mirror.live_inventory({}, claude_home)
    assert live["plugins"]["active_paths"] == {}
    assert [skill["name"] for skill in live["claude_global"]["skills"]] == ["grilling"]


def test_crash_after_moving_duplicate_converges_on_next_run(tmp_path):
    source = tmp_path / "claude" / "skills" / "wrangler"
    make_skill(source, "wrangler")
    codex_home = tmp_path / "codex"
    target = codex_home / "skills" / "wrangler"
    ownership = codex_home / "owned.json"
    journal = ownership.with_suffix(".json.journal")
    moved_to = codex_home / "backups" / "crash" / "wrangler"
    shutil_copy(source, moved_to)
    mirror.atomic_write_json(journal, {
        "target": str(target), "prior_fingerprint": ["dir"], "new_fingerprint": ["symlink", str(source)],
        "owned_record": {"kind": "symlink", "source": str(source), "target": str(target), "content_sha256": None},
        "moved_to": str(moved_to),
    })
    data = {"claude_global": {"skills": [{"name": "wrangler", "path": str(source), "classification": "live"}],
                              "commands": []}}

    result = mirror.apply_plan(mirror.build_plan(data, codex_home, tmp_path / "p", ownership), ownership,
                               codex_home / "backups")
    assert result["created"] == 1
    assert target.is_symlink() and os.readlink(target) == str(source)
    assert str(target) in mirror.owned_entries(ownership)
    assert not journal.exists()
    assert (moved_to / "SKILL.md").exists()


def test_fsync_failure_after_move_restores_the_copy(tmp_path, monkeypatch):
    source = tmp_path / "claude" / "skills" / "wrangler"
    make_skill(source, "wrangler")
    codex_home = tmp_path / "codex"
    copy = codex_home / "skills" / "wrangler"
    shutil_copy(source, copy)
    ownership = codex_home / "owned.json"
    data = {"claude_global": {"skills": [{"name": "wrangler", "path": str(source), "classification": "live"}],
                              "commands": []}}
    plan = mirror.build_plan(data, codex_home, tmp_path / "p", ownership)
    real_fsync = mirror.fsync_dir

    def fail_for_backup(directory):
        if "backups" in str(directory):
            raise OSError("fsync failed")
        real_fsync(directory)
    monkeypatch.setattr(mirror, "fsync_dir", fail_for_backup)
    with pytest.raises(OSError):
        mirror.apply_plan(plan, ownership, codex_home / "backups")
    assert copy.is_dir() and not copy.is_symlink()
    assert not ownership.with_suffix(".json.journal").exists()


def test_copy_with_different_executable_bit_is_preserved(tmp_path):
    source = tmp_path / "claude" / "skills" / "tool"
    make_skill(source, "tool")
    (source / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    codex_home = tmp_path / "codex"
    copy = codex_home / "skills" / "tool"
    shutil_copy(source, copy)
    (copy / "run.sh").chmod(0o755)
    (source / "run.sh").chmod(0o644)
    data = {"claude_global": {"skills": [{"name": "tool", "path": str(source), "classification": "live"}],
                              "commands": []}}
    plan = mirror.build_plan(data, codex_home, tmp_path / "p", codex_home / "owned.json")
    assert [item.status for item in plan] == ["preserve-unmanaged"]


def test_apply_reads_claude_state_while_holding_the_lock(tmp_path, monkeypatch):
    claude_home = make_claude_home(tmp_path, {}, {})
    codex_home = tmp_path / "codex"
    ownership = codex_home / "claude-mirror-owned.json"
    mirror.persist_inventory({"claude_global": {"skills": [], "commands": []}},
                             codex_home / "mirrors" / "claude" / "inventory.json")
    real_live = mirror.live_inventory
    seen = []

    def live_while_locked(data, home):
        with pytest.raises(TimeoutError):
            with mirror.MirrorLock(ownership.with_suffix(".json.lock"), timeout=0.1):
                pass  # pragma: no cover - the lock must already be held
        seen.append(True)
        return real_live(data, home)
    monkeypatch.setattr(mirror, "live_inventory", live_while_locked)

    assert mirror.main(["--codex-home", str(codex_home), "--claude-home", str(claude_home),
                        "--projects-root", str(tmp_path / "projects"), "--apply", "--quiet"]) == 0
    assert seen == [True]


def test_backup_on_another_filesystem_keeps_the_copy_and_continues(tmp_path, monkeypatch):
    import errno
    source = tmp_path / "claude" / "skills" / "wrangler"
    make_skill(source, "wrangler")
    other = tmp_path / "claude" / "skills" / "other"
    make_skill(other, "other")
    codex_home = tmp_path / "codex"
    copy = codex_home / "skills" / "wrangler"
    shutil_copy(source, copy)
    ownership = codex_home / "owned.json"
    data = {"claude_global": {"skills": [
        {"name": "wrangler", "path": str(source), "classification": "live"},
        {"name": "other", "path": str(other), "classification": "live"},
    ], "commands": []}}
    plan = mirror.build_plan(data, codex_home, tmp_path / "p", ownership)

    def cross_device(*args):
        raise OSError(errno.EXDEV, "Invalid cross-device link")
    monkeypatch.setattr(mirror.os, "rename", cross_device)
    outcomes = []
    result = mirror.apply_plan(plan, ownership, codex_home / "backups", outcomes)

    assert result["preserved"] == 1 and result["created"] == 1
    assert copy.is_dir() and not copy.is_symlink()
    assert (codex_home / "skills" / "other").is_symlink()
    assert {"status": "preserve-rename-failed: Invalid cross-device link", "target": str(copy)} in outcomes
    assert not ownership.with_suffix(".json.journal").exists()


def test_quiet_apply_reports_source_that_vanished_after_planning(tmp_path, monkeypatch, capsys):
    import shutil
    claude_home = make_claude_home(tmp_path, {}, {})
    make_skill(claude_home / "skills" / "grilling", "grilling")
    codex_home = tmp_path / "codex"
    mirror.persist_inventory({"claude_global": {"skills": [], "commands": []}},
                             codex_home / "mirrors" / "claude" / "inventory.json")
    real_apply = mirror.apply_plan

    def remove_then_apply(ops, *args, **kwargs):
        shutil.rmtree(claude_home / "skills" / "grilling")
        return real_apply(ops, *args, **kwargs)
    monkeypatch.setattr(mirror, "apply_plan", remove_then_apply)

    assert mirror.main(["--codex-home", str(codex_home), "--claude-home", str(claude_home),
                        "--projects-root", str(tmp_path / "projects"), "--apply", "--quiet"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["changes"] == [{"status": "source-missing", "target": str(codex_home / "skills" / "grilling")}]
    assert summary["applied"]["missing"] == 1


def test_quiet_apply_reports_rejected_names(tmp_path, capsys):
    claude_home = make_claude_home(tmp_path, {}, {})
    codex_home = tmp_path / "codex"
    mirror.persist_inventory({"claude_global": {"skills": [], "commands": []},
                              "project_scoped_all_direct_projects": {"project_claude_skills": {"../escape": ["x"]}}},
                             codex_home / "mirrors" / "claude" / "inventory.json")

    assert mirror.main(["--codex-home", str(codex_home), "--claude-home", str(claude_home),
                        "--projects-root", str(tmp_path / "projects"), "--apply", "--quiet"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["changes"] == [{"status": "rejected-invalid-name", "target": "../escape"}]
    assert summary["applied"]["rejected"] == 1
