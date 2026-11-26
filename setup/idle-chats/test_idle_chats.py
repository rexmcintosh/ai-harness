"""Tests for idle-chats: the idle rule, and a real stop of fake chat programs."""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "idle_chats", Path(__file__).resolve().parent / "idle_chats.py")
idle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(idle)

CID = "415d4487-30bd-4cee-816c-cf53d38fc9e3"
CID2 = "1666ff82-212c-4b6c-a451-11ff7e68d61d"
HOUR = 3600


@pytest.mark.parametrize("argv, expected", [
    (["ccd", "--resume=" + CID], CID),
    (["ccd", "--resume", CID, "--verbose"], CID),
    (["ccd", "--session-id=" + CID], CID),
    (["ccd", "--session-id", CID], CID),
    (["ccd", "--resume"], None),
    (["ccd", "--resume=not-a-chat-id"], None),
    (["ccd", "--resumex=" + CID], None),
    (["ccd", "--verbose"], None),
])
def test_chat_id(argv, expected):
    assert idle.chat_id(argv) == expected


def chat(activity, start, cid=CID, pid=100, desktop=True):
    return {"pid": pid, "start": start, "chat": cid, "cwd": "/x", "activity": activity,
            "desktop": desktop, "leader": True, "ticks": 1}


def test_idle_rule():
    now = 1_000_000.0
    assert idle.decide(chat(now - 30 * HOUR, now - 40 * HOUR), now, 24)[:2] == (True, "idle")
    # A recent write keeps it.
    assert idle.decide(chat(now - 1 * HOUR, now - 40 * HOUR), now, 24)[:2] == (False, "active")
    # A recent start (the chat was just reopened) keeps it, even with an old record.
    assert idle.decide(chat(now - 90 * HOUR, now - 2 * HOUR), now, 24)[:2] == (False, "active")
    # Unknown chats are never stopped.
    assert idle.decide(chat(None, now - 40 * HOUR), now, 24)[:2] == (False, "no chat record")
    assert idle.decide(chat(now - 30 * HOUR, now - 40 * HOUR, cid=None), now, 24)[:2] == (False, "no chat id")
    assert idle.decide(chat(now - 30 * HOUR, now - 40 * HOUR, desktop=False), now, 24)[:2] == (
        False, "not started by the desktop app")
    # Nor is the program that runs this job.
    assert idle.decide(chat(now - 30 * HOUR, now - 40 * HOUR), now, 24, {100})[:2] == (False, "runs this job")


def test_activity_counts_the_chat_folder(tmp_path):
    record = tmp_path / "-home-dev" / f"{CID}.jsonl"
    (record.parent / CID / "subagents").mkdir(parents=True)
    record.write_text("{}\n")
    side = record.parent / CID / "subagents" / "agent-1.jsonl"
    side.write_text("{}\n")
    os.utime(record, (1000, 1000))
    os.utime(side, (5000, 5000))
    for folder in (record.parent / CID / "subagents", record.parent / CID):
        os.utime(folder, (2000, 2000))
    assert idle.last_activity(CID, tmp_path) == 5000
    assert idle.last_activity(CID2, tmp_path) is None


class Fakes:
    """Starts fake chat programs whose argv[0] sits in a fake ccd-cli folder."""

    def __init__(self, root):
        self.root = root
        self.ccd = root / "ccd-cli"
        self.ccd.mkdir()
        (self.ccd / "9.9.9").symlink_to(sys.executable)
        self.projects = root / "projects"
        (self.projects / "-home-dev").mkdir(parents=True)
        self.procs = []
        # The fake chats are children of this test process, so it plays the desktop server.
        with open("/proc/self/cmdline", "rb") as handle:
            self.server_dir = os.path.dirname(handle.read().split(b"\0")[0].decode())

    def group_members(self, group):
        found = []
        for entry in os.listdir("/proc"):
            stat = idle.read_stat(int(entry)) if entry.isdigit() else None
            if stat is not None and stat[1] == group:
                found.append(int(entry))
        return found

    def start(self, cid, record_age, ignore_term=False, own_session=False, late_child=False,
              no_kid=False):
        """Start a fake chat program in its own process group, like the real ones.

        Its helper is a `sleep` in the same group, or in a new session (own_session).
        late_child: on SIGTERM it starts one more `sleep` and exits at once, orphaning it."""
        code = ("import os, signal, subprocess, time, sys\n"
                + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "")
                + ("def late(*_):\n"
                   "    subprocess.Popen(['sleep', '301'])\n"
                   "    os._exit(0)\n"
                   "signal.signal(signal.SIGTERM, late)\n" if late_child else "")
                + ("print(0, flush=True)\n" if no_kid else
                   f"kid = subprocess.Popen(['sleep', '300'], start_new_session={own_session})\n"
                   "print(kid.pid, flush=True)\n")
                + "time.sleep(300)\n")
        proc = subprocess.Popen([str(self.ccd / "9.9.9"), "-c", code, "--resume=" + cid],
                                executable=str(self.ccd / "9.9.9"), stdout=subprocess.PIPE,
                                text=True, process_group=0)
        kid = int(proc.stdout.readline())
        record = self.projects / "-home-dev" / f"{cid}.jsonl"
        record.write_text("{}\n")
        when = time.time() - record_age
        os.utime(record, (when, when))
        self.procs.append((proc, kid))
        return proc, kid

    def run(self, *extra):
        log = self.root / "runs.jsonl"
        code = idle.main(["--ccd-dir", str(self.ccd), "--projects", str(self.projects),
                          "--server-dir", self.server_dir, "--log", str(log),
                          "--lock", str(self.root / "run.lock"), *extra])
        return code, [json.loads(line) for line in log.read_text().splitlines()]

    def cleanup(self):
        for proc, kid in self.procs:
            for pid in (proc.pid, kid):
                if pid <= 0:  # no helper; kill(0) would hit this test run
                    continue
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                pass
            proc.wait()


@pytest.fixture
def fakes(tmp_path):
    made = Fakes(tmp_path)
    yield made
    made.cleanup()


def gone(proc, kid):
    proc.wait(timeout=10)
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(kid, 0)
        except ProcessLookupError:
            return True
        # The sleep is reparented; it is gone once it is a zombie or reaped.
        with open(f"/proc/{kid}/stat") as handle:
            if handle.read().rsplit(")", 1)[1].split()[0] == "Z":
                return True
        time.sleep(0.1)
    return False


def test_stops_idle_chat_and_its_helpers_keeps_active_one(fakes):
    old, old_kid = fakes.start(CID, record_age=10 * HOUR)
    new, _ = fakes.start(CID2, record_age=-HOUR)  # written "in the future": clearly active
    time.sleep(1.5)
    code, runs = fakes.run("--apply", "--hours", str(1 / HOUR))
    assert code == 0
    assert gone(old, old_kid)
    assert new.poll() is None
    results = {c["chat"]: c["result"] for c in runs[-1]["chats"]}
    assert results == {CID: "stopped", CID2: "kept"}


def test_report_only_stops_nothing(fakes):
    old, _ = fakes.start(CID, record_age=10 * HOUR)
    time.sleep(1.5)
    code, runs = fakes.run("--hours", str(1 / HOUR))
    assert code == 0
    assert old.poll() is None
    assert runs[-1]["chats"][0]["result"] == "would stop"
    assert runs[-1]["apply"] is False


def test_stubborn_chat_is_killed(fakes, monkeypatch):
    monkeypatch.setattr(idle, "TERM_WAIT", 1.0)
    old, old_kid = fakes.start(CID, record_age=10 * HOUR, ignore_term=True)
    time.sleep(1.5)
    code, runs = fakes.run("--apply", "--hours", str(1 / HOUR))
    assert code == 0
    assert gone(old, old_kid)
    assert runs[-1]["chats"][0]["result"] == "killed"


def test_recent_start_is_kept_by_default(fakes):
    old, _ = fakes.start(CID, record_age=90 * HOUR)
    code, runs = fakes.run("--apply")
    assert code == 0
    assert old.poll() is None
    assert runs[-1]["chats"][0]["reason"] == "active"


def test_second_run_waits_for_the_first(fakes, tmp_path):
    lock = open(tmp_path / "run.lock", "w")
    import fcntl
    fcntl.flock(lock, fcntl.LOCK_EX)
    code = idle.main(["--ccd-dir", str(fakes.ccd), "--projects", str(fakes.projects),
                      "--server-dir", fakes.server_dir, "--log", str(tmp_path / "other.jsonl"),
                      "--lock", str(tmp_path / "run.lock")])
    assert code == 2


def test_bad_hours_are_refused():
    with pytest.raises(SystemExit):
        idle.main(["--hours", "0"])


def test_helper_in_another_session_is_still_stopped(fakes):
    old, old_kid = fakes.start(CID, record_age=10 * HOUR, own_session=True)
    time.sleep(1.5)
    code, runs = fakes.run("--apply", "--hours", str(1 / HOUR))
    assert code == 0
    assert gone(old, old_kid)
    assert runs[-1]["chats"][0]["result"] == "stopped"


def test_helper_started_during_shutdown_is_stopped(fakes):
    old, old_kid = fakes.start(CID, record_age=10 * HOUR, late_child=True)
    time.sleep(1.5)
    code, runs = fakes.run("--apply", "--hours", str(1 / HOUR))
    assert code == 0
    assert gone(old, old_kid)
    time.sleep(0.3)
    assert fakes.group_members(old.pid) == []
    assert runs[-1]["chats"][0]["result"] == "stopped"


def test_chat_not_started_by_the_desktop_app_is_kept(fakes, tmp_path):
    old, _ = fakes.start(CID, record_age=10 * HOUR)
    time.sleep(1.5)
    fakes.server_dir = str(tmp_path / "elsewhere")
    code, runs = fakes.run("--apply", "--hours", str(1 / HOUR))
    assert code == 0
    assert old.poll() is None
    assert runs[-1]["chats"][0]["reason"] == "not started by the desktop app"


def test_log_is_private(fakes):
    (fakes.root / "runs.jsonl").write_text("")
    os.chmod(fakes.root / "runs.jsonl", 0o644)
    fakes.run()
    assert (fakes.root / "runs.jsonl").stat().st_mode & 0o777 == 0o600


def test_track_ignores_a_reused_pid(fakes):
    """If the chat pid now belongs to another process, its children are not tracked."""
    other, other_kid = fakes.start(CID, record_age=0)
    helpers = {}
    real_ticks = idle.read_stat(other.pid)[2]
    idle.track(other.pid, real_ticks - 1, other.pid, helpers, {})
    assert helpers == {}
    idle.track(other.pid, real_ticks, other.pid, helpers, {})
    assert other_kid in helpers


def test_helper_started_by_a_program_that_had_none_is_stopped(fakes):
    old, _ = fakes.start(CID, record_age=10 * HOUR, late_child=True, no_kid=True)
    time.sleep(1.5)
    code, runs = fakes.run("--apply", "--hours", str(1 / HOUR))
    assert code == 0
    old.wait(timeout=10)
    time.sleep(0.3)
    assert fakes.group_members(old.pid) == []
    assert runs[-1]["chats"][0]["result"] == "stopped"


def test_record_gone_before_the_stop_keeps_the_chat(fakes, monkeypatch):
    old, _ = fakes.start(CID, record_age=10 * HOUR)
    time.sleep(1.5)
    real = idle.last_activity
    calls = []

    def vanishing(cid, projects):
        calls.append(cid)
        return real(cid, projects) if len(calls) == 1 else None
    monkeypatch.setattr(idle, "last_activity", vanishing)
    code, runs = fakes.run("--apply", "--hours", str(1 / HOUR))
    assert code == 0
    assert old.poll() is None
    assert runs[-1]["chats"][0]["reason"] == "no chat record"
