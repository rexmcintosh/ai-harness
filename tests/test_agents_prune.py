"""`agents prune` closes finished Claude background sessions, and nothing else.

The stub `claude` records every argv it is handed, so these tests assert on the
exact commands the script runs — including the ones it must never run.
"""

import json
import os
import subprocess
import time
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "agents"

STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_CLAUDE_CALLS"
case "$1 $2" in
  "agents --json")
    if [ -n "${FAKE_LIST_FAILS:-}" ]; then
      echo "claude: could not reach the session daemon" >&2
      exit 1
    fi
    if [ -n "${FAKE_LIST_GARBAGE:-}" ]; then
      echo "not json at all"
      exit 0
    fi
    cat "$FAKE_CLAUDE_JSON"
    ;;
  "stop "*)
    if [ -n "${FAKE_STOP_FAILS:-}" ]; then
      echo "no running session with id $2" >&2
      exit 1
    fi
    echo "stopped $2"
    ;;
  "rm "*)
    if [ "${FAKE_RM_HANGS:-}" = "$2" ]; then
      sleep 30
    fi
    if [ "${FAKE_RM_REFUSES:-}" = "$2" ]; then
      echo "refusing: worktree has unpushed commits; pass --discard-unpushed abc123@wt-1" >&2
      exit 1
    fi
    echo "removed $2"
    ;;
esac
"""


def minutes_ago(minutes: float) -> int:
    return int((time.time() - minutes * 60) * 1000)


def session(**kwargs) -> dict:
    row = {
        "id": "aaaa1111",
        "cwd": "/home/dev/projects/romance-empire",
        "kind": "background",
        "startedAt": minutes_ago(30),
        "sessionId": "aaaa1111-0000-0000-0000-000000000000",
        "name": "a finished job",
        "state": "done",
    }
    row.update(kwargs)
    return row


def run_agents(tmp_path: Path, sessions: list, *args: str, **envextra) -> tuple:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    calls = tmp_path / "claude-calls"
    payload = tmp_path / "agents.json"
    payload.write_text(json.dumps(sessions))
    stub = bindir / "claude"
    stub.write_text(STUB)
    stub.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "FAKE_CLAUDE_CALLS": str(calls),
            "FAKE_CLAUDE_JSON": str(payload),
            "AGENTS_STATE_DIR": str(tmp_path / "state"),
            "HOME": str(tmp_path),
            # keep the test off the operator's real tmux server
            "TMUX_TMPDIR": str(tmp_path),
        }
    )
    env.pop("TMUX", None)
    env.pop("CLAUDE_JOB_DIR", None)
    env.update(envextra)

    result = subprocess.run(
        [str(SCRIPT), *args], env=env, text=True, capture_output=True
    )
    recorded = calls.read_text().splitlines() if calls.exists() else []
    return result, recorded


def test_dry_run_lists_finished_sessions_without_closing_them(tmp_path):
    result, calls = run_agents(
        tmp_path, [session(id="aaaa1111", name="book covers")], "prune"
    )

    assert result.returncode == 0
    assert "aaaa1111" in result.stdout
    assert "book covers" in result.stdout
    assert "claude stop aaaa1111" in result.stdout
    assert "claude rm aaaa1111" in result.stdout
    assert "--yes" in result.stdout
    assert [c for c in calls if c.startswith(("stop", "rm"))] == []


def test_yes_stops_then_removes_each_finished_session(tmp_path):
    result, calls = run_agents(
        tmp_path,
        [session(id="aaaa1111"), session(id="bbbb2222", name="second")],
        "prune",
        "--yes",
    )

    assert result.returncode == 0
    assert calls[1:] == [
        "stop aaaa1111",
        "rm aaaa1111",
        "stop bbbb2222",
        "rm bbbb2222",
    ]
    assert "closed 2" in result.stdout


def test_leaves_interactive_and_unfinished_sessions_alone(tmp_path):
    result, calls = run_agents(
        tmp_path,
        [
            session(id="aaaa1111", kind="interactive", state=None, status="idle"),
            session(id="bbbb2222", state="working", status="busy"),
            session(id="cccc3333", state="done"),
        ],
        "prune",
        "--yes",
    )

    assert result.returncode == 0
    assert calls[1:] == ["stop cccc3333", "rm cccc3333"]


def test_never_closes_the_session_it_is_running_inside(tmp_path):
    result, calls = run_agents(
        tmp_path,
        [session(id="aaaa1111"), session(id="bbbb2222")],
        "prune",
        "--yes",
        CLAUDE_JOB_DIR=str(tmp_path / "jobs" / "bbbb2222"),
    )

    assert result.returncode == 0
    assert "bbbb2222" not in " ".join(calls[1:])
    assert calls[1:] == ["stop aaaa1111", "rm aaaa1111"]
    assert "this session" in result.stdout


def test_refused_removal_is_reported_and_never_forced(tmp_path):
    result, calls = run_agents(
        tmp_path,
        [session(id="aaaa1111"), session(id="bbbb2222")],
        "prune",
        "--yes",
        FAKE_RM_REFUSES="aaaa1111",
    )

    assert result.returncode == 1
    assert "aaaa1111" in result.stdout + result.stderr
    assert "unpushed" in result.stdout + result.stderr
    joined = " ".join(calls)
    assert "--discard-unpushed" not in joined
    assert "--force-remove-worktree" not in joined
    # a refusal must not stop the rest of the sweep
    assert "stop bbbb2222" in calls and "rm bbbb2222" in calls


def test_already_exited_session_is_removed_even_when_stop_fails(tmp_path):
    result, calls = run_agents(
        tmp_path,
        [session(id="aaaa1111", pid=None)],
        "prune",
        "--yes",
        FAKE_STOP_FAILS="1",
    )

    assert result.returncode == 0
    assert calls[1:] == ["stop aaaa1111", "rm aaaa1111"]


def test_older_than_filters_by_age(tmp_path):
    result, calls = run_agents(
        tmp_path,
        [
            session(id="aaaa1111", startedAt=minutes_ago(5)),
            session(id="bbbb2222", startedAt=minutes_ago(600)),
        ],
        "prune",
        "--older-than",
        "60",
        "--yes",
    )

    assert result.returncode == 0
    assert calls[1:] == ["stop bbbb2222", "rm bbbb2222"]


def test_nothing_to_prune_is_a_clean_no_op(tmp_path):
    result, calls = run_agents(
        tmp_path, [session(id="aaaa1111", kind="interactive", state=None)], "prune"
    )

    assert result.returncode == 0
    assert "nothing" in result.stdout.lower()
    assert calls[1:] == []


def test_dashboard_shows_finished_background_sessions(tmp_path):
    result, _ = run_agents(
        tmp_path,
        [
            session(id="aaaa1111", name="book covers", pid=1234),
            session(id="bbbb2222", name="old fork", startedAt=minutes_ago(60 * 24 * 40)),
            session(id="cccc3333", state="working", status="busy", name="still going"),
        ],
        "dashboard",
    )

    assert "book covers" in result.stdout
    assert "old fork" in result.stdout
    assert "still going" in result.stdout
    assert "agents prune" in result.stdout


def test_dashboard_survives_without_the_claude_cli(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bindir}:/usr/bin:/bin",
            "AGENTS_STATE_DIR": str(tmp_path / "state"),
            "AGENTS_CLAUDE_BIN": str(bindir / "definitely-missing"),
            "HOME": str(tmp_path),
            "TMUX_TMPDIR": str(tmp_path),
        }
    )
    env.pop("TMUX", None)
    result = subprocess.run(
        [str(SCRIPT), "dashboard"], env=env, text=True, capture_output=True
    )

    assert result.returncode in (0, 1)
    assert "Traceback" not in result.stderr


def test_prune_rejects_a_bad_older_than_value(tmp_path):
    result, calls = run_agents(
        tmp_path, [session()], "prune", "--older-than", "soon", "--yes"
    )

    assert result.returncode == 2
    assert calls[1:] == []


def test_cron_entrypoint_never_prunes(tmp_path):
    result, calls = run_agents(tmp_path, [session(id="aaaa1111")], "once")

    assert "stop aaaa1111" not in calls
    assert "rm aaaa1111" not in calls


def test_prune_fails_closed_when_the_session_list_cannot_be_read(tmp_path):
    result, calls = run_agents(
        tmp_path, [session()], "prune", "--yes", FAKE_LIST_FAILS="1"
    )

    assert result.returncode == 1
    assert "cannot list" in result.stderr
    assert "nothing to prune" not in result.stdout
    assert [c for c in calls if c.startswith(("stop", "rm"))] == []


def test_prune_fails_closed_on_an_unreadable_answer(tmp_path):
    result, calls = run_agents(
        tmp_path, [session()], "prune", "--yes", FAKE_LIST_GARBAGE="1"
    )

    assert result.returncode == 1
    assert [c for c in calls if c.startswith(("stop", "rm"))] == []


def test_dashboard_says_so_when_the_session_list_cannot_be_read(tmp_path):
    result, _ = run_agents(tmp_path, [session()], "dashboard", FAKE_LIST_FAILS="1")

    assert "could not read claude sessions" in result.stdout


def test_dashboard_keeps_the_tmux_exit_status(tmp_path):
    # no tmux server in the test environment -> dashboard fails -> status survives
    result, _ = run_agents(tmp_path, [session()], "dashboard")

    assert result.returncode == 1
    assert "no tmux server running" in result.stdout


def test_dry_run_preview_uses_the_configured_cli(tmp_path):
    stub = tmp_path / "bin" / "claude"
    result, _ = run_agents(
        tmp_path, [session(id="aaaa1111")], "prune", AGENTS_CLAUDE_BIN=str(stub)
    )

    assert f"{stub} stop aaaa1111" in result.stdout


def test_a_hung_removal_is_bounded_and_reported(tmp_path):
    result, calls = run_agents(
        tmp_path,
        [session(id="aaaa1111"), session(id="bbbb2222")],
        "prune",
        "--yes",
        FAKE_RM_HANGS="aaaa1111",
        AGENTS_CLAUDE_TIMEOUT_SEC="1",
    )

    assert result.returncode == 1
    assert "timed out" in result.stdout
    # the sweep carries on past the hung one
    assert "rm bbbb2222" in calls


def test_a_session_id_with_an_odd_shape_is_ignored(tmp_path):
    result, calls = run_agents(
        tmp_path,
        [session(id="aaaa1111\tbbbb"), session(id="ok22"), session(id="a b; rm -rf /")],
        "prune",
        "--yes",
    )

    assert result.returncode == 0
    assert calls[1:] == ["stop ok22", "rm ok22"]
