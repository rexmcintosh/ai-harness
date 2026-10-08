import json
import os
import subprocess
from pathlib import Path

import pytest


def _gnu_timeout() -> bool:
    try:
        return subprocess.run(["timeout", "--kill-after=1", "5", "true"],
                              capture_output=True).returncode == 0
    except OSError:
        return False


# Tests that need the investigator to run: without a usable timeout(1) the
# wrapper correctly takes the direct-only path, so they would not apply.
needs_timeout = pytest.mark.skipif(not _gnu_timeout(),
                                   reason="needs timeout(1) with --kill-after")


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "watchdog" / "run-watchdog.sh"


def executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


def run_wrapper(tmp_path: Path, send_rc: int):
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({
        "version": 1, "attempt_id": "attempt-1", "detected_at": 1,
        "fired": [{"name": "disk", "level": "crit", "summary": "full", "evidence": ""}],
        "candidate_suppression_state": {"disk": {"level": "crit", "ts": 1}},
        "delivery": {"status": "pending"},
    }))
    precheck = executable(tmp_path / "precheck", "#!/usr/bin/env bash\nprintf 'disk full\\nWATCHDOG_JSON:{\"escalate\":true,\"attempt_id\":\"attempt-1\"}\\n'\n")
    claude = executable(tmp_path / "claude", "#!/usr/bin/env bash\nprintf '{\"result\":\"diagnosis\"}\\n'\n")
    receipt = '{"ok":true,"status":"accepted","chat_id":1,"message_ids":[9]}'
    sender = executable(tmp_path / "tg-send", f"#!/usr/bin/env bash\nprintf '{receipt}\\n'\nexit {send_rc}\n")
    env = os.environ.copy()
    env.update({
        "WATCHDOG_PRECHECK_CMD": str(precheck),
        "WATCHDOG_CLAUDE_BIN": str(claude),
        "WATCHDOG_TG_SEND": str(sender),
        "WATCHDOG_LOG_DIR": str(tmp_path / "logs"),
        "WATCHDOG_ENV_FILE": str(tmp_path / "missing-env"),
        "WATCHDOG_STATE": str(tmp_path / "state.json"),
        "WATCHDOG_PENDING": str(pending),
        "WATCHDOG_DELIVERY_LAST": str(tmp_path / "last.json"),
        "WATCHDOG_METRICS": str(tmp_path / "metrics.json"),
    })
    result = subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True)
    return result, pending


def test_wrapper_commits_suppression_only_after_accepted_receipt(tmp_path):
    result, pending = run_wrapper(tmp_path, 0)
    assert result.returncode == 0
    assert not pending.exists()
    assert json.loads((tmp_path / "state.json").read_text())["disk"]["ts"] == 1
    assert json.loads((tmp_path / "last.json").read_text())["delivery"]["provider_receipt"]["message_ids"] == [9]


def test_wrapper_retains_failed_delivery_without_suppression(tmp_path):
    result, pending = run_wrapper(tmp_path, 1)
    assert result.returncode == 1
    assert not (tmp_path / "state.json").exists()
    assert json.loads(pending.read_text())["delivery"]["status"] == "failed"


# --- MeetTrack alerts go to Telegram without waiting on the investigator ----

def run_meet_wrapper(tmp_path: Path, *, fired_name="meets.liveness", send_rc=0,
                     claude_script=None, json_line=True, extra_env=None):
    pending = tmp_path / "pending.json"
    fired = [{"name": fired_name, "level": "crit",
              "summary": "1 live meet writer(s) stopped ticking", "evidence": ""}]
    pending.write_text(json.dumps({
        "version": 1, "attempt_id": "attempt-1", "detected_at": 1,
        "fired": fired,
        "candidate_suppression_state": {fired_name: {"level": "crit", "ts": 1}},
        "delivery": {"status": "pending"},
    }))
    payload = json.dumps({"escalate": True, "attempt_id": "attempt-1", "fired": fired})
    report = "[CRIT] meets.liveness: 1 live meet writer(s) stopped ticking"
    if json_line:
        body = f"printf '%s\\n' {json.dumps(report)} {json.dumps('WATCHDOG_JSON:' + payload)}"
    else:
        body = "echo boom >&2; exit 1"
    precheck = executable(tmp_path / "precheck", f"#!/usr/bin/env bash\n{body}\n")
    calls = tmp_path / "claude-calls"
    claude = executable(tmp_path / "claude", claude_script or (
        f"#!/usr/bin/env bash\necho x >> {calls}\n"
        "printf '{\"result\":\"diagnosis\"}\\n'\n"))
    sent = tmp_path / "sent"
    sent.mkdir()
    receipt = '{"ok":true,"status":"accepted","chat_id":1,"message_ids":[9]}'
    sender = executable(tmp_path / "tg-send", (
        "#!/usr/bin/env bash\n"
        f"n=$(ls {sent} | wc -l); cat > {sent}/$n.txt\n"
        f"printf '{receipt}\\n'\nexit {send_rc}\n"))
    env = os.environ.copy()
    env.update({
        "WATCHDOG_PRECHECK_CMD": str(precheck),
        "WATCHDOG_CLAUDE_BIN": str(claude),
        "WATCHDOG_TG_SEND": str(sender),
        "WATCHDOG_LOG_DIR": str(tmp_path / "logs"),
        "WATCHDOG_ENV_FILE": str(tmp_path / "missing-env"),
        "WATCHDOG_STATE": str(tmp_path / "state.json"),
        "WATCHDOG_PENDING": str(pending),
        "WATCHDOG_DELIVERY_LAST": str(tmp_path / "last.json"),
        "WATCHDOG_METRICS": str(tmp_path / "metrics.json"),
        "WATCHDOG_CLAUDE_TIMEOUT": "5",
        "WATCHDOG_CLAUDE_KILL_AFTER": "2",
    })
    env.update(extra_env or {})
    result = subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True)
    messages = [p.read_text() for p in sorted(sent.iterdir())]
    log = (tmp_path / "logs" / "runs.log").read_text()
    return result, pending, messages, log, calls


LIMIT_HIT = ("#!/usr/bin/env bash\n"
             "printf '{\"result\":\"You have hit your weekly limit\"}\\n'\nexit 1\n")


@needs_timeout
def test_a_meet_alert_reaches_telegram_when_the_model_is_out_of_capacity(tmp_path):
    result, pending, messages, log, _ = run_meet_wrapper(tmp_path, claude_script=LIMIT_HIT)
    assert result.returncode == 0
    assert len(messages) == 1
    assert "meets.liveness" in messages[0] and "without the AI investigator" in messages[0]
    assert not pending.exists()                       # delivery recorded as accepted
    assert json.loads((tmp_path / "state.json").read_text())["meets.liveness"]["level"] == "crit"
    assert "direct=1" in log and "narrative=unavailable rc=1" in log


@needs_timeout
def test_the_meet_alert_goes_first_and_the_narrative_follows(tmp_path):
    result, _, messages, log, calls = run_meet_wrapper(tmp_path)
    assert result.returncode == 0
    assert len(messages) == 2
    assert "meets.liveness" in messages[0]
    assert messages[1].strip() == "diagnosis"
    assert "narrative=sent" in log


@needs_timeout
def test_a_hung_investigator_cannot_hold_the_alert(tmp_path):
    hang = "#!/usr/bin/env bash\nsleep 30\n"
    env_timeout = run_meet_wrapper(tmp_path, claude_script=hang)
    result, pending, messages, log, _ = env_timeout
    assert result.returncode == 0
    assert len(messages) == 1 and not pending.exists()
    assert "narrative=unavailable rc=124" in log


def test_a_failed_direct_send_is_recorded_and_skips_the_investigator(tmp_path):
    result, pending, messages, log, calls = run_meet_wrapper(tmp_path, send_rc=1)
    assert result.returncode == 1
    assert json.loads(pending.read_text())["delivery"]["status"] == "failed"
    assert not (tmp_path / "state.json").exists()
    assert not calls.exists()
    assert "direct send failed" in log


def test_an_uncertain_direct_send_is_never_replayed(tmp_path):
    result, pending, _, _, _ = run_meet_wrapper(tmp_path, send_rc=3)
    assert result.returncode == 1
    assert json.loads(pending.read_text())["delivery"]["status"] == "uncertain"


def test_a_failed_precheck_also_goes_direct(tmp_path):
    result, _, messages, log, _ = run_meet_wrapper(tmp_path, json_line=False,
                                                   claude_script=LIMIT_HIT)
    assert result.returncode == 0
    assert len(messages) == 1 and "pre-check failed" in messages[0]


@needs_timeout
def test_non_meet_alerts_keep_the_investigator_path(tmp_path):
    result, pending, messages, log, calls = run_meet_wrapper(tmp_path, fired_name="disk")
    assert result.returncode == 0
    assert messages == ["diagnosis"]
    assert "direct=1" not in log and not pending.exists()


def test_unreadable_precheck_json_fails_towards_the_direct_path(tmp_path):
    pending = tmp_path / "pending.json"
    precheck = executable(tmp_path / "precheck",
                          "#!/usr/bin/env bash\nprintf 'x\\nWATCHDOG_JSON:{not json\\n'\n")
    claude = executable(tmp_path / "claude", LIMIT_HIT)
    sent = tmp_path / "sent"
    sent.mkdir()
    receipt = '{"ok":true,"status":"accepted","chat_id":1,"message_ids":[9]}'
    sender = executable(tmp_path / "tg-send", (
        "#!/usr/bin/env bash\n"
        f"n=$(ls {sent} | wc -l); cat > {sent}/$n.txt\nprintf '{receipt}\\n'\n"))
    env = os.environ.copy()
    env.update({
        "WATCHDOG_PRECHECK_CMD": str(precheck), "WATCHDOG_CLAUDE_BIN": str(claude),
        "WATCHDOG_TG_SEND": str(sender), "WATCHDOG_LOG_DIR": str(tmp_path / "logs"),
        "WATCHDOG_ENV_FILE": str(tmp_path / "missing-env"),
        "WATCHDOG_STATE": str(tmp_path / "state.json"), "WATCHDOG_PENDING": str(pending),
        "WATCHDOG_DELIVERY_LAST": str(tmp_path / "last.json"),
        "WATCHDOG_METRICS": str(tmp_path / "metrics.json"),
        "WATCHDOG_CLAUDE_TIMEOUT": "5",
    })
    # A result nobody can read is a blind watchdog: alert, and directly.
    result = subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True)
    assert result.returncode == 0
    messages = [p.read_text() for p in sorted(sent.iterdir())]
    assert len(messages) == 1 and "result unreadable" in messages[0]
    assert "direct=1" in (tmp_path / "logs" / "runs.log").read_text()


@needs_timeout
def test_a_term_ignoring_investigator_is_killed(tmp_path):
    stubborn = "#!/usr/bin/env bash\ntrap '' TERM\nsleep 60\n"
    import time
    t0 = time.monotonic()
    result, pending, messages, log, _ = run_meet_wrapper(tmp_path, claude_script=stubborn)
    assert result.returncode == 0 and len(messages) == 1 and not pending.exists()
    assert time.monotonic() - t0 < 20          # 5 s timeout + 2 s kill grace


def test_a_broken_delivery_record_still_pages(tmp_path):
    # The pending record cannot be written (its directory is missing): the
    # alert still goes out, unrecorded, so the next run pages again.
    result, _, messages, log, _ = run_meet_wrapper(
        tmp_path, claude_script=LIMIT_HIT,
        extra_env={"WATCHDOG_PENDING": str(tmp_path / "missing" / "pending.json")})
    assert result.returncode == 0
    assert len(messages) == 1 and "meets.liveness" in messages[0]
    assert "sending unrecorded" in log
    assert not (tmp_path / "state.json").exists()     # no cooldown committed


def test_without_timeout_the_narrative_is_skipped_not_run_uncapped(tmp_path):
    # PATH without coreutils timeout(1): keep the tools the wrapper needs.
    import shutil
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("bash", "python3", "flock", "grep", "sed", "head", "cut", "tr",
                 "date", "find", "sort", "cat", "mkdir", "ls", "wc", "dirname"):
        found = shutil.which(tool)
        if found:
            (bindir / tool).symlink_to(found)
    calls = tmp_path / "claude-calls"
    result, pending, messages, log, _ = run_meet_wrapper(
        tmp_path, extra_env={"PATH": str(bindir)})
    assert result.returncode == 0
    assert len(messages) == 1 and not pending.exists()
    assert "narrative=unavailable no usable timeout(1)" in log
    assert not calls.exists()
    # The accepted direct delivery committed its cooldown before the skip.
    assert json.loads((tmp_path / "state.json").read_text())["meets.liveness"]["level"] == "crit"


def _no_timeout_path(tmp_path: Path) -> str:
    import shutil
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for tool in ("bash", "python3", "flock", "grep", "sed", "head", "cut", "tr",
                 "date", "find", "sort", "cat", "mkdir", "ls", "wc", "dirname"):
        found = shutil.which(tool)
        if found and not (bindir / tool).exists():
            (bindir / tool).symlink_to(found)
    return str(bindir)


def test_a_non_meet_alert_without_timeout_skips_the_investigator_and_pages(tmp_path):
    # No bounded runner: the investigator would run uncapped while holding
    # run.lock, so it does not run at all; the plain report goes direct.
    result, pending, messages, log, calls = run_meet_wrapper(
        tmp_path, fired_name="disk", extra_env={"PATH": _no_timeout_path(tmp_path)})
    assert result.returncode == 0
    assert len(messages) == 1 and "Watchdog alert" in messages[0]
    assert "a diagnosis may follow" not in messages[0]
    assert not calls.exists() and not pending.exists()
    assert "investigator=skipped no usable timeout(1)" in log and "direct=1" in log
    assert json.loads((tmp_path / "state.json").read_text())["disk"]["level"] == "crit"


def test_a_timeout_without_kill_after_counts_as_no_bounded_runner(tmp_path):
    # Present but incompatible (rejects --kill-after, like a minimal busybox
    # build): treated exactly like a missing timeout(1).
    bindir = Path(_no_timeout_path(tmp_path))
    executable(bindir / "timeout", (
        "#!/usr/bin/env bash\n"
        'case "$1" in --kill-after=*) echo "unrecognized option" >&2; exit 125;; esac\n'
        'shift; exec "$@"\n'))
    result, pending, messages, log, calls = run_meet_wrapper(
        tmp_path, fired_name="disk", extra_env={"PATH": str(bindir)})
    assert result.returncode == 0
    assert len(messages) == 1 and "Watchdog alert" in messages[0]
    assert not calls.exists() and not pending.exists()
    assert "investigator=skipped no usable timeout(1)" in log


@needs_timeout
def test_the_narrative_runs_after_run_lock_is_released(tmp_path):
    # While the narrative runs, a new watchdog pass must be able to take
    # run.lock: the fake investigator tries exactly that.
    probe = tmp_path / "lock-probe"
    lock = tmp_path / "logs" / "run.lock"
    script = ("#!/usr/bin/env bash\n"
              f"if flock -n {lock} true; then echo free > {probe}; "
              f"else echo held > {probe}; fi\n"
              "printf '{\"result\":\"diagnosis\"}\\n'\n")
    result, _, messages, log, _ = run_meet_wrapper(tmp_path, claude_script=script)
    assert result.returncode == 0 and len(messages) == 2
    assert probe.read_text().strip() == "free"
    assert "narrative=sent" in log


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs procfs")
@needs_timeout
def test_the_investigator_never_inherits_a_lock_descriptor(tmp_path):
    # A model process that outlives a timeout kill must not keep any lock.
    fds = tmp_path / "fds"
    script = ("#!/usr/bin/env bash\n"
              f"ls /proc/$$/fd > {fds}\n"
              "printf '{\"result\":\"diagnosis\"}\\n'\n")
    for name in ("meets.liveness", "disk"):
        sub = tmp_path / name
        sub.mkdir()
        result, *_ = run_meet_wrapper(sub, fired_name=name, claude_script=script)
        assert result.returncode == 0
        open_fds = set(fds.read_text().split())
        assert not {"8", "9"} & open_fds, (name, open_fds)


@needs_timeout
def test_a_second_narrative_is_skipped_while_one_still_runs(tmp_path):
    import fcntl
    (tmp_path / "logs").mkdir()
    with open(tmp_path / "logs" / "narrative.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result, pending, messages, log, calls = run_meet_wrapper(tmp_path)
    assert result.returncode == 0
    assert len(messages) == 1 and not pending.exists()   # the alert still went
    assert not calls.exists()
    assert "narrative=skipped previous narrative still running" in log


@needs_timeout
@pytest.mark.parametrize("fired_name", ["meets.liveness", "disk"])
def test_a_hung_sender_is_capped_and_counts_as_uncertain(tmp_path, fired_name):
    # tg-send normally caps itself at 30 s; if it ever hangs anyway, the
    # wrapper's own cap releases run.lock, and the alert (which may already
    # have been accepted) is recorded uncertain: never replayed.
    import time
    pending = tmp_path / "pending.json"
    fired = [{"name": fired_name, "level": "crit", "summary": "x", "evidence": ""}]
    pending.write_text(json.dumps({
        "version": 1, "attempt_id": "attempt-1", "detected_at": 1, "fired": fired,
        "candidate_suppression_state": {fired_name: {"level": "crit", "ts": 1}},
        "delivery": {"status": "pending"},
    }))
    payload = json.dumps({"escalate": True, "attempt_id": "attempt-1", "fired": fired})
    precheck = executable(tmp_path / "precheck",
                          f"#!/usr/bin/env bash\nprintf '%s\\n' x {json.dumps('WATCHDOG_JSON:' + payload)}\n")
    claude = executable(tmp_path / "claude",
                        "#!/usr/bin/env bash\nprintf '{\"result\":\"diagnosis\"}\\n'\n")
    sender = executable(tmp_path / "tg-send", "#!/usr/bin/env bash\ncat >/dev/null\nsleep 60\n")
    env = os.environ.copy()
    env.update({
        "WATCHDOG_PRECHECK_CMD": str(precheck), "WATCHDOG_CLAUDE_BIN": str(claude),
        "WATCHDOG_TG_SEND": str(sender), "WATCHDOG_LOG_DIR": str(tmp_path / "logs"),
        "WATCHDOG_ENV_FILE": str(tmp_path / "missing-env"),
        "WATCHDOG_STATE": str(tmp_path / "state.json"), "WATCHDOG_PENDING": str(pending),
        "WATCHDOG_DELIVERY_LAST": str(tmp_path / "last.json"),
        "WATCHDOG_METRICS": str(tmp_path / "metrics.json"),
        "WATCHDOG_CLAUDE_TIMEOUT": "5", "WATCHDOG_SEND_TIMEOUT": "2",
    })
    t0 = time.monotonic()
    result = subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True)
    assert time.monotonic() - t0 < 20
    assert result.returncode == 1
    assert json.loads(pending.read_text())["delivery"]["status"] == "uncertain"
    assert not (tmp_path / "state.json").exists()
