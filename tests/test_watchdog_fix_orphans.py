"""watchdog.fix_orphans: stop leftover Codex processes, pinned by pidfd, once each.

The tests never signal a real Codex run. Each one starts a decoy: a copy of `sleep`
named `codex`, a child of the test, which the test reports as PPID 1.
"""
import os
import shutil
import signal
import subprocess

import pytest

from watchdog import fix_orphans, fixkit, run, triage


@pytest.fixture
def world(tmp_path, monkeypatch):
    decoy = tmp_path / "codex"
    shutil.copy("/bin/sleep", decoy)
    procs = []
    monkeypatch.setattr(fixkit, "STATE", tmp_path / "heal")
    sent = []
    monkeypatch.setattr(fixkit, "tg_send", lambda text: sent.append(text) or True)

    def start(ignore_term=False, name=decoy):
        pre = (lambda: signal.signal(signal.SIGTERM, signal.SIG_IGN)) if ignore_term else None
        p = subprocess.Popen([str(name), "300"], preexec_fn=pre)
        procs.append(p)
        return p

    rows = []
    monkeypatch.setattr(run, "_cmd", lambda args: "    PID    PPID     ELAPSED COMMAND\n"
                        + "".join(f"{pid} 1 {etime} {cmd}\n" for pid, etime, cmd in rows))
    real_identity = fix_orphans.identity
    # The decoy's parent is the test; report it as reparented to init, nothing else changed.
    monkeypatch.setattr(fix_orphans, "identity",
                        lambda pid, proc=None: (lambda i: i and dict(i, ppid=1))(real_identity(pid, proc)))
    w = type("W", (), dict(decoy=decoy, start=start, rows=rows, sent=sent, procs=procs))
    yield w
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.wait()


def alive(p):
    return p.poll() is None


def test_a_leftover_codex_is_stopped_once_and_reported(world):
    p = world.start()
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))
    assert fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"]) == 0
    p.wait(timeout=5)
    assert len(world.sent) == 1 and world.sent[0].startswith("STOPPED: 1 leftover Codex")
    assert f"pid {p.pid}, 5h: codex 300" in world.sent[0]
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    assert len(world.sent) == 1


def test_one_that_ignores_term_gets_kill(world, monkeypatch):
    monkeypatch.setattr(fix_orphans, "TERM_WAIT_S", 0.3)
    p = world.start(ignore_term=True)
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))
    fd = os.pidfd_open(p.pid)
    try:
        assert fix_orphans.stop(fd, term_wait=0.3, kill_wait=5)
    finally:
        os.close(fd)
    assert p.wait(timeout=5) == -signal.SIGKILL


def test_a_young_process_is_left_alone(world):
    p = world.start()
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))
    # ps says 5 h, but /proc says it started a moment ago: the real age wins.
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "3"])
    assert alive(p) and world.sent == []


def test_default_age_limit_is_three_hours_and_never_global(world):
    p = world.start()
    world.rows.append((p.pid, "02:59:00", f"{world.decoy} 300"))
    fix_orphans.main([])
    assert alive(p) and world.sent == []
    with pytest.raises(SystemExit):
        fix_orphans.main(["--min-hours", "0"])


def test_only_codex_names_are_touched(world, tmp_path):
    other = tmp_path / "codexd"
    shutil.copy("/bin/sleep", other)
    p = world.start(name=other)
    world.rows.append((p.pid, "05:10:00", f"{other} 300"))
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    assert alive(p) and world.sent == []


def test_a_name_that_changed_since_ps_is_left_alone(world, tmp_path):
    p = world.start()
    # ps saw a codex helper under this pid, but /proc now shows plain `codex`.
    world.rows.append((p.pid, "05:10:00", f"{tmp_path}/codex-code-mode 300"))
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    assert alive(p) and world.sent == []


def test_a_process_with_a_living_parent_is_left_alone(world, monkeypatch):
    p = world.start()
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))
    monkeypatch.setattr(fix_orphans, "identity", lambda pid, proc=None: dict(
        state="S", ppid=os.getpid(), start=0, exe="codex", uid=os.getuid()))
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    assert alive(p) and world.sent == []


def test_dry_run_and_pause_change_nothing(world, capsys):
    p = world.start()
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))
    fixkit.queue(["PROBLEM: earlier"], "orphans-outbox")
    fix_orphans.main(["--dry-run", "--only-pid", str(p.pid), "--min-hours", "0"])
    assert alive(p) and world.sent == [] and "would stop" in capsys.readouterr().out
    (fixkit.STATE / "pause").write_text("")
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    assert alive(p) and world.sent == ["PROBLEM: earlier"]     # a pause stops actions, not reports


def test_crash_after_the_reservation_never_signals_twice(world, monkeypatch):
    p = world.start()
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))

    def dies(fd, *a, **k):
        raise KeyboardInterrupt                  # killed between the reservation and the signal
    monkeypatch.setattr(fix_orphans, "stop", dies)
    with pytest.raises(KeyboardInterrupt):
        fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    calls = []
    monkeypatch.setattr(fix_orphans, "stop", lambda fd, *a, **k: calls.append(fd) or True)
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    assert calls == [] and alive(p)
    assert len(world.sent) == 1 and world.sent[0].startswith("PROBLEM: a leftover Codex process is still")


def test_one_that_will_not_die_is_one_problem(world, monkeypatch):
    p = world.start()
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))
    monkeypatch.setattr(fix_orphans, "stop", lambda fd, *a, **k: False)
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    assert len(world.sent) == 1 and world.sent[0].startswith("PROBLEM: a leftover Codex process did not stop")


def test_identity_reads_a_command_name_with_spaces_and_parens(tmp_path):
    proc = tmp_path / "proc"
    (proc / "42").mkdir(parents=True)
    fields = ["S", "1"] + ["0"] * 17 + ["12345"] + ["0"] * 10
    (proc / "42" / "stat").write_text("42 (codex (x) y) " + " ".join(fields) + "\n")
    (proc / "42" / "cmdline").write_bytes(b"/opt/bin/codex\0exec\0")
    ident = fix_orphans.identity(42, proc)
    assert ident["ppid"] == 1 and ident["start"] == 12345 and ident["exe"] == "codex"


def test_the_check_and_the_fixer_share_one_matcher():
    ps = ("    PID    PPID     ELAPSED COMMAND\n"
          "10 1 07:00:00 /x/codex exec\n11 1 04:00:00 /x/codex-code-mode\n12 1 09:00:00 /x/codexd\n"
          "13 9 09:00:00 /x/codex\n14 1 1:2:3:4 /x/codex\n")
    assert [r[1] for r in triage.orphan_rows(ps, min_minutes=180)] == ["10", "11", "14"]
    assert triage.check_orphan_processes(ps).summary.startswith("2 orphaned codex")


def test_a_codex_adopted_by_the_user_manager_counts_as_an_orphan():
    ps = ("    PID    PPID     ELAPSED COMMAND\n"
          "929 1 12:00:00 /usr/lib/systemd/systemd --user\n"
          "20 929 05:00:00 /x/codex exec review\n"
          "21 1147 05:00:00 /x/codex app-server\n"         # T3 is alive and waiting: kept
          "1147 929 12:00:00 /home/dev/.t3/runtime/t3 __service-launcher\n")
    assert [r[1] for r in triage.orphan_rows(ps, min_minutes=180)] == ["20"]


def test_is_reaper_needs_our_own_user_manager(tmp_path):
    proc = tmp_path / "proc"
    (proc / "929").mkdir(parents=True)
    (proc / "929" / "cmdline").write_bytes(b"/usr/lib/systemd/systemd\0--user\0")
    (proc / "1147").mkdir()
    (proc / "1147" / "cmdline").write_bytes(b"/home/dev/.t3/runtime/t3\0__service-launcher\0")
    assert fix_orphans.is_reaper(1, proc) and fix_orphans.is_reaper(929, proc)
    assert not fix_orphans.is_reaper(1147, proc) and not fix_orphans.is_reaper(4242, proc)


def test_a_real_orphan_adopted_by_the_user_manager_is_found_live(tmp_path, monkeypatch):
    """No faked parent: a double fork, as when a session dies. The decoy is stopped by
    the fixer through its own pidfd, and nothing else is touched (--only-pid)."""
    decoy = tmp_path / "codex"
    shutil.copy("/bin/sleep", decoy)
    r, w = os.pipe()
    if os.fork() == 0:
        os.setsid()
        child = subprocess.Popen([str(decoy), "300"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.write(w, str(child.pid).encode())
        os._exit(0)
    os.wait()
    pid = int(os.read(r, 32))
    try:
        ident = fix_orphans.identity(pid)
        if not fix_orphans.is_reaper(ident["ppid"]):
            pytest.skip(f"adopted by pid {ident['ppid']}, not init or a user manager")
        monkeypatch.setattr(fixkit, "STATE", tmp_path / "heal")
        sent = []
        monkeypatch.setattr(fixkit, "tg_send", lambda text: sent.append(text) or True)
        fix_orphans.main(["--only-pid", str(pid), "--min-hours", "0"])
        assert sent and sent[0].startswith("STOPPED: 1 leftover Codex")
        assert fix_orphans.identity(pid) is None or fix_orphans.identity(pid)["start"] != ident["start"]
    finally:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


def test_ps_lines_are_never_cut(monkeypatch, tmp_path):
    """A set COLUMNS makes ps cut long lines, which hides the `codex` name."""
    monkeypatch.setattr(fixkit, "STATE", tmp_path / "heal")
    seen = []
    monkeypatch.setattr(run, "_cmd", lambda args: seen.append(args) or "")
    fix_orphans.main(["--dry-run"])
    assert seen and "-ww" in seen[0]
