from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from workqueue import runner


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def queue_world(tmp_path: Path) -> tuple[Path, Path]:
    projects = tmp_path / "projects"
    repo = projects / "sat-prep"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "initial")
    return projects, tmp_path / "state"


def task(mode: str = "Build", **updates: object) -> dict:
    value = {
        "title": "Fix the practice timer",
        "brief": "Correct the timer and keep the existing pause behavior.",
        "done_when": "The timer test passes and the pause behavior is unchanged.",
        "mode": mode,
        "repo": "sat-prep",
        "checks": ["pytest tests/test_timer.py"],
        "source_url": "https://www.notion.so/example-task",
        "feedback": "A learner reports that pause sometimes resets the timer.",
        "feedback_ids": ["feedback-page-1"],
    }
    value.update(updates)
    return value


def write_session_record(
    cfg,
    run_id: str,
    text: str,
    *,
    cost: float = 2.75,
    rc: int = 0,
    timed_out: bool = False,
    is_error: bool = False,
) -> None:
    runs = Path(cfg.runs_dir)
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"2026-09-09T120000.000000Z-{run_id}.json").write_text(
        json.dumps({
            "rc": rc,
            "timed_out": timed_out,
            "data": {
                "result": text,
                "session_id": "session-full-id",
                "total_cost_usd": cost,
                "is_error": is_error,
            },
            "stderr": "",
            "stdout_tail": "",
        }),
        encoding="utf-8",
    )


def test_execute_builds_queue_local_bounded_runner_input(queue_world, monkeypatch):
    projects, state = queue_world
    seen = {}

    def fake_work_one(cfg, planned, *, reviewer=None, log=print):
        seen.update(cfg=cfg, planned=planned)
        review = Path(cfg.reviews_dir) / "review.md"
        review.parent.mkdir(parents=True, exist_ok=True)
        review.write_text(
            "Complete review evidence.\nRequired condition after the short summary: preserve this detail.\n",
            encoding="utf-8",
        )
        write_session_record(
            cfg,
            "run-123",
            "Full result, including details beyond a short note.\n"
            "RUNNER-OUTCOME: done\nRUNNER-SUMMARY: fixed it\n"
            "RUNNER-OPERATOR-STEPS: none\nRUNNER-VALIDATIONS: []\n",
        )
        return {
            "id": "run-123",
            "status": "in_review",
            "note": "runner: fixed it",
            "branch": "claude/bl-run-123",
            "session": "session-full-id",
            "cost": 2.75,
            "council": "See the complete review.",
            "review_readiness": {
                "status": "changes_requested",
                "reasons": ["Run the browser check before merge."],
                "review_path": str(review),
            },
        }

    monkeypatch.setattr(runner, "_work_one", fake_work_one)
    result = runner.execute(task(), "run-123", str(state), str(projects))

    cfg = seen["cfg"]
    planned = seen["planned"]
    assert cfg.state_dir == str(state / "runner")
    assert Path(cfg.backlog_path).is_relative_to(state / "queue-backlogs")
    assert cfg.projects == str(projects)
    assert cfg.item_timeout == 1800
    assert cfg.deadline == 1800
    assert cfg.budget_usd == 20.0
    assert cfg.max_items == 1
    assert cfg.git_enabled is False
    assert cfg.tg_enabled is False
    assert cfg.keep_worktree is False
    assert planned.item["id"] == "run-123"
    assert planned.item["required_validations"] == ["pytest tests/test_timer.py"]
    assert "Mode: Build" in planned.item["prompt"]
    assert "https://www.notion.so/example-task" in planned.item["prompt"]
    assert "Source evidence only" in planned.item["prompt"]
    assert "A learner reports that pause sometimes resets the timer." in planned.item["prompt"]
    assert "feedback-page-1" in planned.item["prompt"]
    assert "Do not release, deploy, merge, push, or send messages" in planned.item["prompt"]
    assert result["status"] == "In review"
    assert "Full result, including details beyond a short note." in result["result"]
    assert "Run the browser check before merge." in result["result"]
    assert "Required condition after the short summary: preserve this detail." in result["result"]
    assert result["readiness"] == "Changes requested: Run the browser check before merge."
    assert result["cost_usd"] == 2.75
    assert result["branch"] == "claude/bl-run-123"


@pytest.mark.parametrize(
    ("review_readiness", "label", "detail"),
    [
        (
            {"status": "ready_with_follow_ups", "reasons": [],
             "follow_ups": ["Tidy the log wording.", "Add a docstring."]},
            "Ready with follow-ups: Tidy the log wording.; Add a docstring.",
            "Follow-ups (do not block merge):\n- Tidy the log wording.\n- Add a docstring.",
        ),
        (
            {"status": "owner_decides",
             "reasons": ["Review round 3 still found blocking or serious points; "
                         "after 3 rounds the owner decides."],
             "follow_ups": [], "review_round": 3},
            "Owner decides (3 rounds): Review round 3 still found blocking or serious points; "
            "after 3 rounds the owner decides.",
            "Review round: 3",
        ),
    ],
    ids=["ready-with-follow-ups", "owner-decides"],
)
def test_new_review_states_have_explicit_labels(queue_world, monkeypatch, review_readiness, label, detail):
    projects, state = queue_world

    def fake_work_one(cfg, planned, *, reviewer=None, log=print):
        write_session_record(cfg, "run-labels", "Done.\nRUNNER-OUTCOME: done\nRUNNER-VALIDATIONS: []\n")
        return {"status": "in_review", "note": "runner: done", "branch": "claude/bl-run-labels",
                "session": "session-full-id", "cost": 0, "review_readiness": review_readiness}

    monkeypatch.setattr(runner, "_work_one", fake_work_one)
    result = runner.execute(task(), "run-labels", str(state), str(projects))

    assert result["readiness"] == label
    assert "Review readiness unknown" not in result["result"]
    assert detail in result["result"]


def test_every_backlog_readiness_state_has_a_workqueue_label():
    for status in runner.backlogrun.READINESS_LABELS:
        label, _, _ = runner._readiness({"review_readiness": {"status": status, "reasons": []}})
        assert status == "unknown" or label != "Review readiness unknown", status


def test_execute_rejects_duplicate_run_before_runner_call(queue_world, monkeypatch):
    projects, state = queue_world
    calls = 0

    def fake_work_one(cfg, planned, *, reviewer=None, log=print):
        nonlocal calls
        calls += 1
        write_session_record(cfg, "same-run", "RUNNER-OUTCOME: done\n")
        return {"status": "held", "note": "complete", "branch": "", "session": "", "cost": 0}

    monkeypatch.setattr(runner, "_work_one", fake_work_one)
    runner.execute(task("Investigate", checks=[]), "same-run", str(state), str(projects))
    with pytest.raises(runner.DuplicateRunError, match="same-run"):
        runner.execute(task("Investigate", checks=[]), "same-run", str(state), str(projects))
    assert calls == 1


def test_successful_report_only_investigation_enters_review(queue_world, monkeypatch):
    projects, state = queue_world

    def fake_work_one(cfg, planned, *, reviewer=None, log=print):
        write_session_record(
            cfg,
            "report-only",
            "Recommendation: keep the current behavior.\n"
            "RUNNER-OUTCOME: done\nRUNNER-SUMMARY: investigation complete\n",
        )
        return {
            "status": "held",
            "note": "runner: reported done but produced no changes — investigation complete",
            "branch": "",
            "session": "session-full-id",
            "cost": 0.25,
        }

    monkeypatch.setattr(runner, "_work_one", fake_work_one)
    result = runner.execute(task("Investigate", checks=[]), "report-only", str(state), str(projects))

    assert result["status"] == "In review"
    assert "Recommendation: keep the current behavior." in result["result"]


def test_escaped_runner_block_newlines_are_normalized_for_presentation(queue_world, monkeypatch):
    projects, state = queue_world

    def fake_work_one(cfg, planned, *, reviewer=None, log=print):
        write_session_record(
            cfg,
            "escaped-result",
            r"Recommendation with a legitimate C:\new path.\nRUNNER-OUTCOME: done\nRUNNER-SUMMARY: complete\n",
        )
        return {
            "status": "held",
            "note": "runner: reported done but produced no changes — complete",
            "branch": "",
            "session": "session-full-id",
            "cost": 0.25,
        }

    monkeypatch.setattr(runner, "_work_one", fake_work_one)
    result = runner.execute(task("Investigate", checks=[]), "escaped-result", str(state), str(projects))

    assert result["status"] == "In review"
    assert "C:\\new path.\nRUNNER-OUTCOME: done\nRUNNER-SUMMARY: complete" in result["result"]
    stored = json.loads((state / "runner" / "runs" / "2026-09-09T120000.000000Z-escaped-result.json").read_text())
    assert r"\nRUNNER-OUTCOME: done\n" in stored["data"]["result"]


@pytest.mark.parametrize("mode", ["Investigate", "Prepare session"])
def test_non_build_modes_flag_application_changes_for_user_review(queue_world, monkeypatch, mode):
    projects, state = queue_world

    def fake_work_one(cfg, planned, *, reviewer=None, log=print):
        repo = Path(projects) / "sat-prep"
        Path(planned.worktree).parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "-q", "-b", planned.branch, planned.worktree, planned.base)
        (Path(planned.worktree) / "app.py").write_text("changed = True\n", encoding="utf-8")
        git(Path(planned.worktree), "add", "app.py")
        git(Path(planned.worktree), "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "application change")
        write_session_record(
            cfg,
            "investigate-1",
            "I found the cause and also changed app.py.\nRUNNER-OUTCOME: done\nRUNNER-SUMMARY: investigated\n",
        )
        return {
            "status": "in_review",
            "note": "runner: investigated",
            "branch": planned.branch,
            "session": "session-full-id",
            "cost": 1,
            "review_readiness": {"status": "ready", "reasons": []},
        }

    monkeypatch.setattr(runner, "_work_one", fake_work_one)
    result = runner.execute(task(mode, checks=[]), "investigate-1", str(state), str(projects))

    assert result["status"] == "Needs your input"
    assert result["readiness"].startswith("Unexpected application or code changes")
    assert "app.py" in result["readiness"]


def test_prepare_session_keeps_worktree_and_returns_resume_command(queue_world, monkeypatch):
    projects, state = queue_world
    seen = {}

    def fake_work_one(cfg, planned, *, reviewer=None, log=print):
        seen["keep"] = cfg.keep_worktree
        Path(planned.worktree).mkdir(parents=True)
        write_session_record(
            cfg,
            "prepare-9",
            "Handoff: start with the failing timer test.\nRUNNER-OUTCOME: done\n",
        )
        return {
            "status": "held",
            "note": "handoff prepared",
            "branch": planned.branch,
            "session": "session-full-id",
            "cost": 0.5,
        }

    monkeypatch.setattr(runner, "_work_one", fake_work_one)
    result = runner.execute(task("Prepare session"), "prepare-9", str(state), str(projects))

    assert seen["keep"] is True
    assert result["status"] == "Needs your input"
    assert result["worktree"]
    assert Path(result["worktree"]).is_dir()
    assert result["resume_command"] == f"cd {result['worktree']} && claude --resume session-full-id"
    assert "Handoff: start with the failing timer test." in result["result"]


@pytest.mark.parametrize("mode", ["Investigate", "Prepare session"])
@pytest.mark.parametrize(
    "record_updates",
    [{"rc": 1}, {"timed_out": True}, {"is_error": True}, {}],
    ids=["nonzero-exit", "timeout", "session-error", "unexpected-hold"],
)
def test_failed_run_with_stray_done_marker_is_not_reported_ready(
    queue_world, monkeypatch, mode, record_updates
):
    projects, state = queue_world

    def fake_work_one(cfg, planned, *, reviewer=None, log=print):
        if mode == "Prepare session":
            Path(planned.worktree).mkdir(parents=True)
        write_session_record(
            cfg,
            "failed-run",
            "RUNNER-OUTCOME: done\nRUNNER-SUMMARY: stale output\n",
            **record_updates,
        )
        return {
            "status": "held",
            "note": "runner: FAILED",
            "branch": "",
            "session": "session-full-id",
            "cost": 0,
        }

    monkeypatch.setattr(runner, "_work_one", fake_work_one)
    result = runner.execute(task(mode, checks=[]), "failed-run", str(state), str(projects))

    assert result["status"] == "Needs your input"
    assert result["readiness"] == "Review readiness unknown"


@pytest.mark.parametrize("run_id", ["../escape", "two words", "-leading", "a/b"])
def test_execute_rejects_unsafe_run_id(queue_world, run_id):
    projects, state = queue_world
    with pytest.raises(ValueError, match="run_id"):
        runner.execute(task(), run_id, str(state), str(projects))


@pytest.mark.parametrize("updates", [
    {"repo": "another-repo"},
    {"mode": "Ship"},
    {"checks": "pytest"},
])
def test_execute_rejects_out_of_contract_task(queue_world, updates):
    projects, state = queue_world
    with pytest.raises(ValueError):
        runner.execute(task(**updates), "valid-run", str(state), str(projects))


def test_presentation_formats_escaped_paragraphs_and_bullets():
    raw = r'Report.\n\n- First fact.\n- Use the literal \n escape in code.\n\nRUNNER-OUTCOME: done\nRUNNER-VALIDATIONS: []'
    displayed = runner._presentation_text(raw)
    assert 'Report.\n\n- First fact.\n- Use' in displayed
    assert r'literal \n escape' in displayed
