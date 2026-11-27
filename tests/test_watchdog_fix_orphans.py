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
    monkeypatch.setattr(fix_orphans, "service_main_pid", lambda pid: False)
    monkeypatch.setattr(fix_orphans, "output_abandoned", lambda pid: True)
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
    # ps shows the arguments; a prompt or token in them never reaches the note.
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} exec --prompt s3cret-token"))
    assert fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"]) == 0
    p.wait(timeout=5)
    assert len(world.sent) == 1 and world.sent[0].startswith("STOPPED: a leftover Codex")
    assert f"pid {p.pid}, 5h, codex exec)" in world.sent[0] and "s3cret" not in world.sent[0]
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    assert len(world.sent) == 1


def test_one_that_ignores_term_gets_kill(world, monkeypatch):
    monkeypatch.setattr(fix_orphans, "TERM_WAIT_S", 0.3)
    p = world.start(ignore_term=True)
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))
    fd = os.pidfd_open(p.pid)
    try:
        assert fix_orphans.stop(fd, term_wait=0.3, kill_wait=5) == fix_orphans.STOPPED
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
    monkeypatch.setattr(fix_orphans, "stop", lambda fd, *a, **k: calls.append(fd) or fix_orphans.STOPPED)
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    assert calls == [] and alive(p)
    assert len(world.sent) == 1 and world.sent[0].startswith("PROBLEM: a leftover Codex process is still")


def test_one_that_will_not_die_is_one_problem(world, monkeypatch):
    p = world.start()
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))
    monkeypatch.setattr(fix_orphans, "stop", lambda fd, *a, **k: fix_orphans.FAILED)
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


def _real_orphan(decoy, stdout):
    """Double fork, as when a session dies: the middle process exits, so the decoy is
    adopted, and with stdout=PIPE the only reader of its output is gone with it."""
    r, w = os.pipe()
    if os.fork() == 0:
        os.setsid()
        child = subprocess.Popen([str(decoy), "300"], stdin=subprocess.DEVNULL,
                                 stdout=stdout, stderr=subprocess.DEVNULL)
        os.write(w, str(child.pid).encode())
        os._exit(0)
    os.wait()
    pid = int(os.read(r, 32))
    os.close(r)
    os.close(w)
    return pid, fix_orphans.identity(pid)


def _kill_if_same(pid, ident):
    now = fix_orphans.identity(pid)
    if now and now["start"] == ident["start"]:
        os.kill(pid, 9)


def test_real_orphans_found_live_only_the_abandoned_one_is_stopped(tmp_path, monkeypatch):
    """No faked parent or reader. Nothing but the two decoys is acted on (--only-pid)."""
    decoy = tmp_path / "codex"
    shutil.copy("/bin/sleep", decoy)
    monkeypatch.setattr(fixkit, "STATE", tmp_path / "heal")
    sent = []
    monkeypatch.setattr(fixkit, "tg_send", lambda text: sent.append(text) or True)
    abandoned, a_ident = _real_orphan(decoy, subprocess.PIPE)
    to_file, f_ident = _real_orphan(decoy, open(tmp_path / "result.txt", "w"))
    try:
        if not fix_orphans.is_reaper(a_ident["ppid"]):
            pytest.skip(f"adopted by pid {a_ident['ppid']}, not init or a user manager")
        assert fix_orphans.output_abandoned(abandoned) and not fix_orphans.output_abandoned(to_file)
        fix_orphans.main(["--only-pid", str(to_file), "--min-hours", "0"])
        assert sent == [] and fix_orphans.identity(to_file)["start"] == f_ident["start"]
        fix_orphans.main(["--only-pid", str(abandoned), "--min-hours", "0"])
        assert len(sent) == 1 and sent[0].startswith("STOPPED: a leftover Codex")
        now = fix_orphans.identity(abandoned)
        assert now is None or now["start"] != a_ident["start"]
    finally:
        _kill_if_same(abandoned, a_ident)
        _kill_if_same(to_file, f_ident)


def test_a_pipe_someone_still_reads_is_not_abandoned(tmp_path):
    decoy = tmp_path / "codex"
    shutil.copy("/bin/sleep", decoy)
    p = subprocess.Popen([str(decoy), "300"], stdout=subprocess.PIPE)
    try:
        assert not fix_orphans.output_abandoned(p.pid)          # the test holds the read end
        p.stdout.close()
        assert fix_orphans.output_abandoned(p.pid)
    finally:
        p.kill()
        p.wait()


def test_unknown_subcommands_never_reach_a_note():
    assert fix_orphans._short("/x/codex exec --json") == "codex exec"
    assert fix_orphans._short("/x/codex s3cret") == "codex"
    assert fix_orphans._short("/x/codex-code-mode") == "codex-code-mode"


def test_a_decided_outcome_is_reported_once_after_a_crash(world, monkeypatch):
    p = world.start()
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))
    real_queue = fixkit.queue

    def queue_then_die(messages, name):
        real_queue(messages, name)
        if any(m.startswith("STOPPED") for m in messages):
            raise KeyboardInterrupt              # dies between queueing and clearing pending
    monkeypatch.setattr(fixkit, "queue", queue_then_die)
    with pytest.raises(KeyboardInterrupt):
        fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    monkeypatch.setattr(fixkit, "queue", real_queue)
    world.rows.clear()
    fix_orphans.main([])
    fix_orphans.main([])
    assert len(world.sent) == 1 and world.sent[0].startswith("STOPPED: a leftover Codex")


def test_the_kill_is_skipped_when_it_no_longer_qualifies(world, monkeypatch):
    p = world.start(ignore_term=True)
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))
    monkeypatch.setattr(fix_orphans, "TERM_WAIT_S", 0.2)
    checks = []
    monkeypatch.setattr(fix_orphans, "_still_same",
                        lambda *a: (lambda: checks.append(1) or len(checks) == 1))
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    assert alive(p) and len(checks) == 2
    assert len(world.sent) == 1 and world.sent[0].startswith("PROBLEM: a leftover Codex process did not stop")


def test_ps_lines_are_never_cut(monkeypatch, tmp_path):
    """A set COLUMNS makes ps cut long lines, which hides the `codex` name."""
    monkeypatch.setattr(fixkit, "STATE", tmp_path / "heal")
    seen = []
    monkeypatch.setattr(run, "_cmd", lambda args: seen.append(args) or "")
    fix_orphans.main(["--dry-run"])
    assert seen and "-ww" in seen[0]


def _cgroup(tmp_path, pid, path):
    d = tmp_path / "proc" / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "cgroup").write_text(f"0::{path}\n")
    return tmp_path / "proc"


def test_the_main_process_of_a_service_is_never_stopped(tmp_path):
    from types import SimpleNamespace
    user = "/user.slice/user-1000.slice/user@1000.service/app.slice"
    asked = []

    def answer(main_pid, rc=0):
        def runner(cmd, **kw):
            asked.append(cmd)
            return SimpleNamespace(returncode=rc, stdout=f"{main_pid}\n")
        return runner
    proc = _cgroup(tmp_path, 50, f"{user}/codex-worker.service")
    assert fix_orphans.service_main_pid(50, proc, answer(50))           # supervised
    assert asked[-1] == ["systemctl", "--user", "show", "-p", "MainPID", "--value",
                         "codex-worker.service"]
    assert not fix_orphans.service_main_pid(50, proc, answer(1147))     # left in t3code.service
    assert fix_orphans.service_main_pid(50, proc, answer(50, rc=1))     # cannot tell: keep
    assert fix_orphans.service_main_pid(50, proc, answer(""))
    proc = _cgroup(tmp_path, 51, f"{user}/t3code-codex-9.scope")
    assert not fix_orphans.service_main_pid(51, proc, answer(51))       # a scope is not supervision
    proc = _cgroup(tmp_path, 52, "/system.slice/codex.service")
    fix_orphans.service_main_pid(52, proc, answer(52))
    assert "--user" not in asked[-1]
    assert fix_orphans.service_main_pid(4242, proc, answer(0))         # unreadable cgroup: keep


def test_a_supervised_codex_is_left_alone(world, monkeypatch):
    p = world.start()
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))
    monkeypatch.setattr(fix_orphans, "service_main_pid", lambda pid: True)
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    assert alive(p) and world.sent == []


def test_a_stop_cut_off_after_the_kill_is_still_reported(world, monkeypatch):
    p = world.start()
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))
    real_stop = fix_orphans.stop

    def kills_then_dies(fd, *a, **k):
        real_stop(fd, *a, **k)
        raise KeyboardInterrupt                 # the fixer dies before it writes the outcome
    monkeypatch.setattr(fix_orphans, "stop", kills_then_dies)
    with pytest.raises(KeyboardInterrupt):
        fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    p.wait(timeout=5)
    assert world.sent == []
    world.rows.clear()
    fix_orphans.main([])
    assert len(world.sent) == 1 and world.sent[0].startswith("STOPPED: a leftover Codex")
    assert "cut off" in world.sent[0] and fixkit.load_state("orphans")["pending"] == {}


def test_records_from_another_boot_or_a_gone_process_are_dropped(world):
    p = world.start()
    start = fix_orphans.identity(p.pid)["start"]
    boot = fix_orphans.boot_id()
    live = f"{boot}:{p.pid}:{start}"
    old_boot, gone = f"zzzz:{p.pid}:{start}", f"{boot}:{p.pid}:{start + 1}"
    state = fix_orphans._prune({"tried": {live: 1, old_boot: 1, gone: 1}}, boot)
    assert list(state["tried"]) == [live]


def test_a_pidfd_failure_is_one_problem_a_day(world, monkeypatch):
    p = world.start()
    world.rows.append((p.pid, "05:10:00", f"{world.decoy} 300"))

    def refused(pid):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(fix_orphans.os, "pidfd_open", refused)
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    fix_orphans.main(["--only-pid", str(p.pid), "--min-hours", "0"])
    assert alive(p) and len(world.sent) == 1
    assert "pidfd_open: Operation not permitted" in world.sent[0]


def test_a_process_that_changed_program_before_the_signal_is_left_alone(world):
    p = world.start()
    fd = os.pidfd_open(p.pid)
    try:
        assert fix_orphans.stop(fd, same=lambda: False) == fix_orphans.LEFT
    finally:
        os.close(fd)
    assert alive(p)
