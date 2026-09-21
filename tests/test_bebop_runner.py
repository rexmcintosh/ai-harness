"""Bebop runner: one retry when the briefing agent could not reach its tools.

Evidence (2026-09-18 18:00 and 2026-08-26 18:00 transcripts): the claude.ai Gmail and
Calendar connectors are listed by name at session start but on a slow start are not
callable for the first 30 to 45 seconds. The haiku agent looks them up ~10 times, gives
up and answers FAILED. A fresh process a little later connects normally.

The script is copied into a temp tree so state.json, logs, the claude CLI and tg-send
are all fakes. HOME is a temp dir, so the PATH line in the script finds no real claude.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEFING = "☀️ *Morning, Rex*\n📅 Clear\n📧 Nothing new"


def executable(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def run(tmp_path: Path, replies: list[str], *, claude_rc: int = 0, extra_env=None,
        mode: str = "morning", backlog: str | None = None, helper: str | None = None):
    """replies[n] is what the fake claude answers on its n-th call (last one repeats).

    `backlog` is the YAML the backlog helper reads; `helper` replaces the helper script
    itself (to stand in for a crash or a hang). With neither, BEBOP_BACKLOG_FILE points
    at a file that does not exist, so no test can read Rex's real backlog.
    """
    bebop = tmp_path / "tree" / "bebop"
    shutil.copytree(ROOT / "bebop" / "prompts", bebop / "prompts")
    shutil.copy(ROOT / "bebop" / "run-briefing.sh", bebop / "run-briefing.sh")
    shutil.copy(ROOT / "bebop" / "backlog_line.py", bebop / "backlog_line.py")
    if helper is not None:
        executable(bebop / "backlog_line.py", helper)
    backlog_file = tmp_path / "backlog.yaml"
    if backlog is not None:
        backlog_file.write_text(backlog)
    (tmp_path / "replies.json").write_text(json.dumps(replies))
    executable(tmp_path / "fakebin" / "claude", f"""#!/usr/bin/env python3
import json, pathlib, sys
calls = pathlib.Path({str(tmp_path / 'claude-calls')!r})
n = int(calls.read_text()) if calls.exists() else 0
calls.write_text(str(n + 1))
replies = json.loads(pathlib.Path({str(tmp_path / 'replies.json')!r}).read_text())
print(json.dumps({{"result": replies[min(n, len(replies) - 1)], "total_cost_usd": 0.1,
                  "usage": {{"input_tokens": 1, "output_tokens": 2}}}}))
sys.exit({claude_rc})
""")
    executable(tmp_path / "tree" / "bin" / "tg-send", f"""#!/usr/bin/env bash
if [ "$2" = "-" ]; then cat > "{tmp_path}/sent-$(date +%s%N).txt"; else printf '%s' "$2" > "{tmp_path}/sent-$(date +%s%N).txt"; fi
""")
    home = tmp_path / "home"; home.mkdir()
    env = {"HOME": str(home), "PATH": f"{tmp_path / 'fakebin'}:/usr/bin:/bin",
           "BEBOP_RETRY_DELAY": "0", "BEBOP_BACKLOG_FILE": str(backlog_file),
           "BEBOP_BACKLOG_NOW": "2026-09-21", **(extra_env or {})}
    proc = subprocess.run(["bash", str(bebop / "run-briefing.sh"), mode],
                          env=env, capture_output=True, text=True, timeout=60)
    calls = int((tmp_path / "claude-calls").read_text())
    sent = [p.read_text() for p in sorted(tmp_path.glob("sent-*.txt"))]
    log = (bebop / "logs" / "runs.log").read_text().splitlines()
    return proc, calls, sent, log, bebop / "state.json"


def test_a_good_first_run_is_not_retried(tmp_path):
    proc, calls, sent, log, state = run(tmp_path, [BRIEFING])
    assert proc.returncode == 0 and calls == 1
    assert sent == [BRIEFING]
    assert len(log) == 1 and " rc=0 " in log[0]
    assert state.exists()


def test_tools_unavailable_is_retried_once_and_the_briefing_is_sent(tmp_path):
    proc, calls, sent, log, state = run(
        tmp_path, ["FAILED: Gmail and Calendar tools unavailable — MCP connection issue.", BRIEFING])
    assert proc.returncode == 0 and calls == 2
    assert sent == [BRIEFING]                       # no failure ping: the run recovered
    assert len(log) == 1 and " rc=0 " in log[0] and "attempts=2" in log[0]
    assert state.exists()                           # the delta window advances


def test_two_failures_ping_once_and_do_not_advance_the_window(tmp_path):
    proc, calls, sent, log, state = run(tmp_path, ["FAILED: tools unavailable"])
    assert proc.returncode == 1 and calls == 2      # exactly one retry, never a loop
    assert len(sent) == 1 and "briefing failed" in sent[0]
    assert len(log) == 1 and " rc=1 " in log[0] and "attempts=2" in log[0]
    assert not state.exists()                       # a failed run never skips email


def test_a_crashed_claude_is_retried_too(tmp_path):
    proc, calls, sent, log, state = run(tmp_path, [""], claude_rc=1)
    assert proc.returncode == 1 and calls == 2


def test_retry_can_be_turned_off(tmp_path):
    proc, calls, sent, log, state = run(tmp_path, ["FAILED: tools unavailable", BRIEFING],
                                        extra_env={"BEBOP_MAX_ATTEMPTS": "1"})
    assert proc.returncode == 1 and calls == 1


def test_the_log_line_keeps_the_shape_the_watchdog_parses(tmp_path):
    from watchdog.triage import check_bebop_runs
    import time
    proc, calls, sent, log, state = run(tmp_path, ["FAILED: tools unavailable", BRIEFING])
    assert check_bebop_runs("\n".join(log) + "\n", int(time.time())).level == "ok"


# --- council review: "exactly one retry" must hold whatever the environment says ----

def test_attempts_are_capped_at_two_even_if_the_env_asks_for_more(tmp_path):
    proc, calls, sent, log, state = run(tmp_path, ["FAILED: tools unavailable"],
                                        extra_env={"BEBOP_MAX_ATTEMPTS": "5"})
    assert calls == 2 and proc.returncode == 1


def test_a_non_numeric_attempt_count_falls_back_to_the_default(tmp_path):
    proc, calls, sent, log, state = run(tmp_path, ["FAILED: tools unavailable", BRIEFING],
                                        extra_env={"BEBOP_MAX_ATTEMPTS": "foo"})
    assert calls == 2 and proc.returncode == 0
    assert "BEBOP_MAX_ATTEMPTS" in proc.stderr


def test_a_non_numeric_delay_falls_back_to_the_default_with_a_warning(tmp_path):
    proc, calls, sent, log, state = run(tmp_path, [BRIEFING], extra_env={"BEBOP_RETRY_DELAY": "soon"})
    assert proc.returncode == 0 and calls == 1
    assert "BEBOP_RETRY_DELAY" in proc.stderr


# --- the backlog line ---------------------------------------------------------------
# Appended here, in the shell, AFTER the agent has composed the briefing: the model never
# sees it, so it can neither drop it nor reword it. Morning only, and only on a briefing
# that actually composed.

BACKLOG = """\
items:
- id: 2026-09-03-review-complaint-sweep
  status: in_review
  created: 2026-09-03
  worked: 2026-09-03
- id: 2026-07-20-shots-dir-retention-prune
  status: held
  created: 2026-07-20
  worked: 2026-09-21
"""
BACKLOG_LINE = ("Backlog: 1 waits for your review (oldest 18 days: review-complaint-sweep). "
                "1 new on hold since yesterday. Look: backlog-run report")


def test_the_morning_briefing_gets_the_backlog_line_appended(tmp_path):
    proc, calls, sent, log, state = run(tmp_path, [BRIEFING], backlog=BACKLOG)
    assert proc.returncode == 0
    assert sent == [f"{BRIEFING}\n\n{BACKLOG_LINE}"]
    assert "backlog_line=1" in log[0]


def test_the_evening_briefing_is_left_alone(tmp_path):
    proc, calls, sent, log, state = run(tmp_path, [BRIEFING], backlog=BACKLOG, mode="evening")
    assert proc.returncode == 0
    assert sent == [BRIEFING]
    assert "backlog_line=0" in log[0]


def test_an_empty_backlog_adds_nothing_at_all(tmp_path):
    proc, calls, sent, log, state = run(tmp_path, [BRIEFING], backlog="items: []\n")
    assert proc.returncode == 0
    assert sent == [BRIEFING]
    assert "backlog_line=0" in log[0]


def test_a_missing_backlog_file_still_sends_the_briefing(tmp_path):
    proc, calls, sent, log, state = run(tmp_path, [BRIEFING])
    assert proc.returncode == 0 and sent == [BRIEFING] and "backlog_line=0" in log[0]


def test_a_crashing_helper_costs_the_line_not_the_briefing(tmp_path):
    proc, calls, sent, log, state = run(
        tmp_path, [BRIEFING], backlog=BACKLOG,
        helper="#!/usr/bin/env python3\nraise SystemExit('boom')\n")
    assert proc.returncode == 0
    assert sent == [BRIEFING]                       # no traceback, no partial line
    assert "backlog_line=0" in log[0]


def test_a_hanging_helper_is_cut_off_and_the_briefing_still_goes(tmp_path):
    proc, calls, sent, log, state = run(
        tmp_path, [BRIEFING], backlog=BACKLOG,
        helper="#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n",
        extra_env={"BEBOP_BACKLOG_TIMEOUT": "1"})
    assert proc.returncode == 0
    assert sent == [BRIEFING]
    assert "backlog_line=0" in log[0]


def test_a_failed_briefing_gets_the_failure_ping_alone(tmp_path):
    """The failure ping is an alarm. A backlog nag stapled to it would read like a normal
    morning, which is the one thing the ping exists to prevent."""
    proc, calls, sent, log, state = run(tmp_path, ["FAILED: tools unavailable"], backlog=BACKLOG)
    assert proc.returncode == 1
    assert len(sent) == 1 and "briefing failed" in sent[0]
    assert "Backlog:" not in sent[0]
    assert " rc=1 " in log[0] and "backlog_line=0" in log[0]


def test_the_line_survives_a_retry(tmp_path):
    proc, calls, sent, log, state = run(
        tmp_path, ["FAILED: tools unavailable", BRIEFING], backlog=BACKLOG)
    assert proc.returncode == 0 and calls == 2
    assert sent == [f"{BRIEFING}\n\n{BACKLOG_LINE}"]
    assert "backlog_line=1" in log[0]


def test_the_log_line_still_parses_with_the_backlog_flag_on_it(tmp_path):
    from watchdog.triage import check_bebop_runs
    import time
    proc, calls, sent, log, state = run(tmp_path, [BRIEFING], backlog=BACKLOG)
    assert check_bebop_runs("\n".join(log) + "\n", int(time.time())).level == "ok"
    assert len(log) == 1


MARKING_HELPER = """#!/usr/bin/env python3
import pathlib, sys
pathlib.Path({marker!r}).write_text("ran")
sys.stdout.write("Backlog: 1 waits for your review. Look: backlog-run report")
"""


def test_a_failed_briefing_never_runs_the_helper_so_the_alarm_is_not_delayed(tmp_path):
    marker = tmp_path / "helper-ran"
    proc, calls, sent, log, state = run(
        tmp_path, ["FAILED: tools unavailable"], backlog=BACKLOG,
        helper=MARKING_HELPER.format(marker=str(marker)))
    assert proc.returncode == 1
    assert not marker.exists()


def test_a_line_that_would_push_the_message_past_telegrams_limit_is_left_out(tmp_path):
    long_briefing = "x" * 4050                      # Telegram refuses a message over 4096 characters
    proc, calls, sent, log, state = run(tmp_path, [long_briefing], backlog=BACKLOG)
    assert proc.returncode == 0
    assert sent == [long_briefing]                  # the briefing goes; the optional line does not
    assert "backlog_line=0" in log[0]


def test_a_garbled_timeout_falls_back_to_the_default_and_the_line_still_goes(tmp_path):
    proc, calls, sent, log, state = run(tmp_path, [BRIEFING], backlog=BACKLOG,
                                        extra_env={"BEBOP_BACKLOG_TIMEOUT": "forever"})
    assert proc.returncode == 0
    assert sent == [f"{BRIEFING}\n\n{BACKLOG_LINE}"]


def test_a_huge_timeout_is_capped_so_a_hang_cannot_hold_the_briefing_for_long(tmp_path):
    import time
    started = time.monotonic()
    proc, calls, sent, log, state = run(
        tmp_path, [BRIEFING], backlog=BACKLOG,
        helper="#!/usr/bin/env python3\nimport time\ntime.sleep(120)\n",
        extra_env={"BEBOP_BACKLOG_TIMEOUT": "9999", "BEBOP_BACKLOG_TIMEOUT_CAP": "2"})
    assert proc.returncode == 0 and sent == [BRIEFING]
    assert time.monotonic() - started < 30
