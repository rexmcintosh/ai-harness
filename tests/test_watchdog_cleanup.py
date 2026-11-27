"""watchdog.cleanup: free disk space from an explicit list, before the disk alerts fire."""
import json
import os
from pathlib import Path

from watchdog import cleanup, fixkit

NOW = 1_800_000_000.0
OLD = NOW - 40 * 86400


def make_servers(tmp_path, names, lru):
    servers = tmp_path / "cli" / "servers"
    for n in names:
        (servers / n).mkdir(parents=True)
        (servers / n / "f").write_text("x")
    (servers / "lru.json").write_text(json.dumps(lru))
    return servers


def test_vscode_keeps_two_newest_the_cli_match_and_in_use(tmp_path):
    names = ["Stable-a", "Stable-b", "Stable-c", "Stable-d", "Stable-e"]
    servers = make_servers(tmp_path, names, ["Stable-a", "Stable-b", "Stable-c", "Stable-d"])
    (tmp_path / "code-e").write_text("")              # the newest CLI is for Stable-e
    in_use = [f"node {servers}/Stable-d/out/server-main.js"]
    out = cleanup.old_vscode_servers(servers, tmp_path, in_use)
    assert [p.name for p in out] == ["Stable-c"]


def test_vscode_unreadable_lru_removes_nothing(tmp_path):
    servers = make_servers(tmp_path, ["Stable-a", "Stable-b", "Stable-c"], [])
    (servers / "lru.json").write_text("not json")
    assert cleanup.old_vscode_servers(servers, tmp_path, []) == []
    (servers / "lru.json").write_text("[]")
    assert cleanup.old_vscode_servers(servers, tmp_path, []) == []


def test_referenced_projects_from_cron_and_units():
    texts = ["*/5 * * * * cd /home/dev/projects/sat-prep && npm run x",
             "ExecStart=/usr/bin/node /home/dev/projects/nutrition-tracker/server/main.js"]
    assert cleanup.referenced_projects(texts) == {"sat-prep", "nutrition-tracker"}


def make_project(root, name, *, lock=True, nm_age=OLD):
    proj = root / name
    (proj / "node_modules" / "lib").mkdir(parents=True)
    (proj / "node_modules" / "lib" / "i.js").write_text("x")
    if lock:
        (proj / "package-lock.json").write_text("{}")
    os.utime(proj / "node_modules", (nm_age, nm_age))
    return proj


def test_node_modules_only_for_idle_unreferenced_locked_projects(tmp_path):
    for name in ("idle", "cron-job", "no-lock", "recent-install", "recent-commit", "busy", "_archive",
                 "idle-2"):
        make_project(tmp_path, name, lock=name != "no-lock",
                     nm_age=NOW - 3600 if name == "recent-install" else OLD)
    commits = {"recent-commit": NOW - 86400}
    in_use = [str(tmp_path / "busy" / "src")]
    out = cleanup.stale_node_modules(tmp_path, {"cron-job"}, in_use, NOW,
                                     last_commit=lambda p: commits.get(p.name, OLD))
    assert sorted(p.parent.name for p in out) == ["idle", "idle-2"]


def test_a_similar_name_in_use_does_not_protect_another_project(tmp_path):
    make_project(tmp_path, "app")
    in_use = [str(tmp_path / "app-2"), f"node {tmp_path}/app-2/x.js"]
    out = cleanup.stale_node_modules(tmp_path, set(), in_use, NOW, last_commit=lambda p: OLD)
    assert [p.parent.name for p in out] == ["app"]
    in_use = [f"node {tmp_path}/app/x.js"]
    assert cleanup.stale_node_modules(tmp_path, set(), in_use, NOW, last_commit=lambda p: OLD) == []


def test_no_commit_history_is_kept(tmp_path):
    make_project(tmp_path, "nogit")
    assert cleanup.stale_node_modules(tmp_path, set(), [], NOW, last_commit=lambda p: None) == []


class World:
    def __init__(self, tmp_path, monkeypatch, disk, mac=40.0):
        self.disk = list(disk)            # successive df readings
        self.sent, self.step1_calls, self.mac_cleaned = [], [], []
        monkeypatch.setattr(fixkit, "STATE", tmp_path / "heal")
        monkeypatch.setattr(fixkit, "tg_send", lambda t: self.sent.append(t) or True)
        monkeypatch.setattr(cleanup.time, "time", lambda: NOW)
        monkeypatch.setattr(cleanup, "df_pct", lambda path="/": self.disk.pop(0) if len(self.disk) > 1 else self.disk[0])
        monkeypatch.setattr(cleanup, "in_use_paths", lambda: [])
        monkeypatch.setattr(cleanup, "step1", lambda dry, in_use: self.step1_calls.append(dry) or ["old copies (2.0 GB)"])
        monkeypatch.setattr(cleanup, "idle_node_modules", lambda in_use, now: "node_modules in 1 projects (x; 1.0 GB)")
        monkeypatch.setattr(cleanup, "mac_free_gb", lambda: mac)
        monkeypatch.setattr(cleanup, "mac_clean", lambda dry: self.mac_cleaned.append(dry) or True)


def test_below_trigger_does_nothing(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, [72])
    cleanup.main([])
    assert w.step1_calls == [] and w.sent == [] and w.mac_cleaned == []


def test_over_trigger_runs_step1_only_and_reports(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, [81, 78, 78])
    cleanup.main([])
    assert w.step1_calls == [False]
    assert w.sent == ["CLEANED: the server disk was 81% full. I removed old copies (2.0 GB). It is now 78% full."]
    # Inside the 6-hour cooldown nothing runs again.
    w.disk = [81]
    cleanup.main([])
    assert w.step1_calls == [False]


def test_still_full_reports_what_is_left_once_a_day(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, [90, 87])
    cleanup.main([])
    assert len(w.sent) == 2 and w.sent[1].startswith("PROBLEM: the server disk is still 87% full")
    assert "need your call" in w.sent[1] and "node_modules in 1 projects" in w.sent[1]
    monkeypatch.setattr(cleanup.time, "time", lambda: NOW + 7 * 3600)   # cooled, same day
    w.disk = [87]
    cleanup.main([])
    assert not any(t.startswith("PROBLEM") for t in w.sent[2:])


def test_mac_low_is_cleaned_once_per_cooldown(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, [70], mac=12.0)
    cleanup.main([])
    assert w.mac_cleaned == [False] and w.sent[-1].startswith("CLEANED: the Mac mini had 12 GB free")
    cleanup.main([])
    assert w.mac_cleaned == [False]


def test_dry_run_changes_and_sends_nothing(tmp_path, monkeypatch, capsys):
    w = World(tmp_path, monkeypatch, [72])
    cleanup.main(["--dry-run"])
    assert w.step1_calls == [True] and w.sent == []
    assert not (tmp_path / "heal" / "cleanup.json").exists()
    assert "would remove" in capsys.readouterr().out


def test_step1_removes_only_the_listed_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    servers = make_servers(home / ".vscode-server", ["Stable-a", "Stable-b", "Stable-c"],
                           ["Stable-a", "Stable-b", "Stable-c"])
    (home / ".npm" / "_cacache").mkdir(parents=True)
    (home / ".npm" / "_cacache" / "x").write_text("x")
    (home / ".npm" / "_npx" / "mcp").mkdir(parents=True)
    monkeypatch.setattr(cleanup, "HOME", home)
    monkeypatch.setattr(cleanup, "VSCODE_SERVERS", servers)
    ran = []
    monkeypatch.setattr(cleanup, "_run", lambda cmd, timeout=600: ran.append(cmd) or True)
    monkeypatch.setattr(cleanup.shutil, "which", lambda name: None)
    done = cleanup.step1(False, [])
    assert not (servers / "Stable-c").exists() and (servers / "Stable-a").exists()
    assert not (home / ".npm" / "_cacache").exists() and (home / ".npm" / "_npx" / "mcp").exists()
    assert ["sudo", "-n", "apt-get", "clean"] in ran
    assert ["sudo", "-n", "docker", "image", "prune", "-f"] in ran
    assert any("VS Code" in d for d in done)


def test_service_inventory_fails_closed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(cleanup, "HOME", tmp_path)
    monkeypatch.setattr(cleanup.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    assert cleanup._service_texts() is None
    monkeypatch.setattr(cleanup.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="* * * * * x\n"))
    units = tmp_path / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    (units / "a.service").write_text("ExecStart=/home/dev/projects/a/run")
    assert cleanup._service_texts() == ["* * * * * x\n", "ExecStart=/home/dev/projects/a/run"]
    (units / "b.service").mkdir()                  # unreadable as a file
    assert cleanup._service_texts() is None


def test_partial_removal_reports_only_what_went(tmp_path, monkeypatch):
    d = tmp_path / "d"
    d.mkdir()
    (d / "a").write_bytes(b"x" * 100_000)
    (d / "b").write_bytes(b"x" * 100_000)
    real = cleanup.shutil.rmtree

    def half(path, ignore_errors=False):
        (Path(path) / "a").unlink()
    monkeypatch.setattr(cleanup.shutil, "rmtree", half)
    freed = cleanup._remove(d, False)
    assert 0 < freed < cleanup.size_bytes(d) + freed and (d / "b").exists()
    monkeypatch.setattr(cleanup.shutil, "rmtree", real)


def test_failed_mac_cleanup_is_a_problem(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, [70], mac=8.0)
    monkeypatch.setattr(cleanup, "mac_clean", lambda dry: False)
    cleanup.main([])
    assert w.sent and w.sent[-1].startswith("PROBLEM: the Mac mini has only 8 GB free")


def test_unreadable_projects_still_sends_the_problem(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, [90, 88])

    def broken(in_use, now):
        raise PermissionError("projects")
    monkeypatch.setattr(cleanup, "idle_node_modules", broken)
    cleanup.main([])
    assert any(t.startswith("PROBLEM: the server disk is still 88% full") for t in w.sent)


def test_full_disk_state_save_does_not_stop_the_cleanup(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, [96, 90])
    real = fixkit.save_state
    calls = []

    def full(name, data):
        calls.append(name)
        if len(calls) == 1:
            raise OSError(28, "No space left on device")
        real(name, data)
    monkeypatch.setattr(fixkit, "save_state", full)
    cleanup.main([])
    assert w.step1_calls == [False] and w.sent[0].startswith("CLEANED")


def test_a_disk_that_stays_full_still_gets_its_notes_out(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, [96, 95])

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(fixkit, "save_state", full)
    monkeypatch.setattr(fixkit, "queue", full)
    monkeypatch.setattr(fixkit, "flush", full)
    cleanup.main([])
    assert w.sent[0].startswith("CLEANED") and w.sent[1].startswith("PROBLEM")
