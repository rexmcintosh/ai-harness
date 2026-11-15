"""The merge helper on throwaway git repos: a bare origin, a main checkout, a fix worktree."""
import dataclasses
import json
import subprocess
import time
from pathlib import Path

import pytest

from alertwatch import ledger, merge
from alertwatch.config import load_config


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def world(tmp_path, monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                 "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                 "ALERT_WATCH_RUN_ID": "run-test"}.items():
        monkeypatch.setenv(k, v)
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    projects = tmp_path / "projects"
    repo = projects / "repo"
    git(tmp_path, "clone", "-q", str(origin), str(repo))
    git(repo, "checkout", "-q", "-b", "main")
    (repo / "app.py").write_text("x = 1\n")
    (repo / "web").mkdir()
    (repo / "web" / "page.ts").write_text("a\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    git(repo, "push", "-q", "origin", "main")
    state = tmp_path / "state"
    cfg = load_config(env={"ALERT_WATCH_STATE": str(state)})
    cfg = dataclasses.replace(cfg, mode="act", projects_dir=projects, repos=("repo",))
    state.mkdir()
    (state / "worktrees").mkdir()
    return {"cfg": cfg, "repo": repo, "origin": origin, "state": state, "tmp": tmp_path}


def fix_worktree(w, name="alertwatch/run-test-fix", change=("app.py", "x = 2\n"), base="origin/main"):
    wt = w["state"] / "worktrees" / name.replace("/", "-")
    git(w["repo"], "fetch", "-q", "origin")
    git(w["repo"], "worktree", "add", "-q", "-b", name, str(wt), base)
    path, text = change
    (wt / path).parent.mkdir(parents=True, exist_ok=True)
    (wt / path).write_text(text)
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "fix")
    return wt


def run(w, *argv, cfg=None):
    return merge.main(list(argv), cfg=cfg or w["cfg"])


def test_merge_ok(world, capsys):
    wt = fix_worktree(world)
    head = git(wt, "rev-parse", "HEAD")
    base = git(world["repo"], "rev-parse", "origin/main")
    assert run(world, "merge", "repo", str(wt)) == 0
    assert git(world["origin"], "rev-parse", "main") == head
    assert git(world["repo"], "rev-parse", "main") == head
    intent, act = ledger.actions(world["state"])
    assert intent["kind"] == "merge_intent" and act["intent"] == intent["id"]
    assert act["kind"] == "merge" and act["repo"] == "repo" and act["to_sha"] == head
    assert act["from_sha"] == base and act["run_id"] == "run-test" and act["files"] == ["app.py"]
    assert json.loads(capsys.readouterr().out)["status"] == "merged"


def test_refused_in_diagnose_mode(world):
    wt = fix_worktree(world)
    assert run(world, "merge", "repo", str(wt), cfg=dataclasses.replace(world["cfg"], mode="diagnose")) == 2
    assert ledger.actions(world["state"]) == []


def test_refused_repo_not_listed(world):
    wt = fix_worktree(world)
    assert run(world, "merge", "other", str(wt)) == 2


def test_refused_worktree_outside_state(world):
    wt = world["tmp"] / "elsewhere"
    git(world["repo"], "worktree", "add", "-q", "-b", "alertwatch/x", str(wt), "origin/main")
    (wt / "app.py").write_text("x = 3\n")
    git(wt, "commit", "-qam", "fix")
    assert run(world, "merge", "repo", str(wt)) == 2


def test_refused_branch_not_alertwatch(world):
    wt = fix_worktree(world, name="claude/fix")
    assert run(world, "merge", "repo", str(wt)) == 2


def test_refused_dirty_main_checkout(world):
    wt = fix_worktree(world)
    (world["repo"] / "app.py").write_text("local edit\n")
    assert run(world, "merge", "repo", str(wt)) == 2
    assert git(world["origin"], "rev-parse", "main") != git(wt, "rev-parse", "HEAD")


def test_refused_dirty_worktree(world):
    wt = fix_worktree(world)
    (wt / "app.py").write_text("uncommitted\n")
    assert run(world, "merge", "repo", str(wt)) == 2


def test_refused_when_not_a_fast_forward(world):
    wt = fix_worktree(world)
    other = world["tmp"] / "other-clone"
    git(world["tmp"], "clone", "-q", str(world["origin"]), str(other))
    (other / "new.py").write_text("y\n")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "someone else")
    git(other, "push", "-q", "origin", "main")
    git(world["repo"], "pull", "-q", "--ff-only", "origin", "main")
    assert run(world, "merge", "repo", str(wt)) == 2


def test_refused_main_checkout_behind_origin(world):
    wt = fix_worktree(world)
    other = world["tmp"] / "other-clone"
    git(world["tmp"], "clone", "-q", str(world["origin"]), str(other))
    (other / "new.py").write_text("y\n")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "someone else")
    git(other, "push", "-q", "origin", "main")
    assert run(world, "merge", "repo", str(wt)) == 2


def test_refused_denied_path(world, capsys):
    wt = fix_worktree(world, change=("web/page.ts", "b\n"))
    assert run(world, "merge", "repo", str(wt)) == 2
    assert "web/page.ts" in capsys.readouterr().err


def test_refused_when_daily_cap_reached(world):
    for i in range(3):
        ledger.append_action(world["state"], {"kind": "merge", "at": time.time() - 60, "repo": "repo"})
    wt = fix_worktree(world)
    assert run(world, "merge", "repo", str(wt)) == 2


def test_old_merges_and_reverts_do_not_count(world):
    for i in range(3):
        ledger.append_action(world["state"], {"kind": "merge", "at": time.time() - 90000})
        ledger.append_action(world["state"], {"kind": "revert", "at": time.time() - 60})
    wt = fix_worktree(world)
    assert run(world, "merge", "repo", str(wt)) == 0


def test_revert_own_merge(world):
    wt = fix_worktree(world)
    assert run(world, "merge", "repo", str(wt)) == 0
    head = git(wt, "rev-parse", "HEAD")
    assert run(world, "revert", "repo", head) == 0
    assert (world["repo"] / "app.py").read_text() == "x = 1\n"
    assert git(world["origin"], "rev-parse", "main") == git(world["repo"], "rev-parse", "main")
    kinds = [a["kind"] for a in ledger.actions(world["state"])]
    assert kinds == ["merge_intent", "merge", "revert_intent", "revert"]
    assert ledger.actions(world["state"])[3]["reverts"] == head
    assert run(world, "revert", "repo", head) == 2  # never twice


def test_revert_refuses_a_commit_alert_watch_did_not_merge(world):
    sha = git(world["repo"], "rev-parse", "HEAD")
    assert run(world, "revert", "repo", sha) == 2


def test_denied_matching():
    rules = ("web/", ".env", "watchdog/", ".sql")
    assert merge.denied(["web/a.ts", "x/.env.local", "watchdog/run.py", "db/m.sql", "db.py"], rules) == [
        "web/a.ts", "x/.env.local", "watchdog/run.py", "db/m.sql"]
    assert merge.denied(["src/web/a.ts"], ("web/",)) == []


def test_crash_after_push_still_counts_and_stays_revertable(world, monkeypatch):
    wt = fix_worktree(world)
    head = git(wt, "rev-parse", "HEAD")
    real = ledger.append_action
    calls = []

    def flaky(state, action):
        calls.append(action["kind"])
        if action["kind"] == "merge":
            raise OSError("disk full")  # the push already happened
        real(state, action)
    monkeypatch.setattr(ledger, "append_action", flaky)
    with pytest.raises(OSError):
        run(world, "merge", "repo", str(wt))
    monkeypatch.setattr(ledger, "append_action", real)
    assert git(world["origin"], "rev-parse", "main") == head
    assert ledger.merges_in_window(world["state"], time.time()) == 1
    git(world["repo"], "merge", "-q", "--ff-only", "origin/main")
    assert run(world, "revert", "repo", head) == 0


def test_failed_push_is_abandoned_and_not_counted(world, monkeypatch):
    wt = fix_worktree(world)
    real = merge._git

    def no_push(cwd, *args):
        if args and args[0] == "push":
            raise subprocess.CalledProcessError(1, ["git", *args], "", "rejected")
        return real(cwd, *args)
    monkeypatch.setattr(merge, "_git", no_push)
    assert run(world, "merge", "repo", str(wt)) == 1
    assert [a["kind"] for a in ledger.actions(world["state"])] == ["merge_intent", "merge_abandoned"]
    assert ledger.merges_in_window(world["state"], time.time()) == 0


def test_pushed_but_main_checkout_did_not_move(world, monkeypatch, capsys):
    wt = fix_worktree(world)
    real = merge._git

    def stuck(cwd, *args):
        if args[:2] == ("merge", "-q"):
            raise subprocess.CalledProcessError(1, ["git", *args], "", "would overwrite")
        return real(cwd, *args)
    monkeypatch.setattr(merge, "_git", stuck)
    assert run(world, "merge", "repo", str(wt)) == 1
    assert "pushed, needs Rex" in capsys.readouterr().err
    assert [a["kind"] for a in ledger.actions(world["state"])] == ["merge_intent", "merge"]


def test_a_lost_push_reply_counts_as_pushed(world, monkeypatch):
    wt = fix_worktree(world)
    head = git(wt, "rev-parse", "HEAD")
    real = merge._git

    def lost_reply(cwd, *args):
        if args and args[0] == "push":
            real(cwd, *args)
            raise subprocess.CalledProcessError(128, ["git", *args], "", "connection reset")
        return real(cwd, *args)
    monkeypatch.setattr(merge, "_git", lost_reply)
    assert run(world, "merge", "repo", str(wt)) == 0
    assert git(world["repo"], "rev-parse", "main") == head


def test_untracked_file_in_the_way_is_refused_before_the_push(world):
    wt = fix_worktree(world, change=("new_module.py", "y = 1\n"))
    (world["repo"] / "new_module.py").write_text("someone's scratch\n")
    before = git(world["origin"], "rev-parse", "main")
    assert run(world, "merge", "repo", str(wt)) == 2
    assert git(world["origin"], "rev-parse", "main") == before
    assert ledger.actions(world["state"]) == []


def test_revert_crash_before_push_is_recovered(world, monkeypatch):
    wt = fix_worktree(world)
    assert run(world, "merge", "repo", str(wt)) == 0
    head = git(wt, "rev-parse", "HEAD")
    real = merge._git

    def die_on_push(cwd, *args):
        if args and args[0] == "push":
            raise KeyboardInterrupt  # the process dies; nothing reached origin
        return real(cwd, *args)
    monkeypatch.setattr(merge, "_git", die_on_push)
    with pytest.raises(KeyboardInterrupt):
        run(world, "revert", "repo", head)
    monkeypatch.setattr(merge, "_git", real)
    # the revert was built in a temporary worktree: the main checkout never moved
    assert git(world["repo"], "rev-parse", "main") == git(world["origin"], "rev-parse", "main") == head
    assert run(world, "revert", "repo", head) == 0
    assert not list((world["state"] / "worktrees").glob("revert-*"))
    assert (world["repo"] / "app.py").read_text() == "x = 1\n"
    assert git(world["repo"], "rev-parse", "main") == git(world["origin"], "rev-parse", "main")
    kinds = [a["kind"] for a in ledger.actions(world["state"])]
    assert kinds == ["merge_intent", "merge", "revert_intent", "revert_abandoned", "revert_intent", "revert"]


def test_revert_crash_after_push_counts_as_reverted(world, monkeypatch):
    wt = fix_worktree(world)
    assert run(world, "merge", "repo", str(wt)) == 0
    head = git(wt, "rev-parse", "HEAD")
    real = ledger.append_action

    def die_after_push(state, action):
        if action["kind"] == "revert":
            raise KeyboardInterrupt
        real(state, action)
    monkeypatch.setattr(ledger, "append_action", die_after_push)
    with pytest.raises(KeyboardInterrupt):
        run(world, "revert", "repo", head)
    monkeypatch.setattr(ledger, "append_action", real)
    assert run(world, "revert", "repo", head) == 2  # already reverted on origin


def test_merge_intent_that_never_reached_origin_stops_counting(world, monkeypatch):
    wt = fix_worktree(world)
    real = merge._git

    def die_on_push(cwd, *args):
        if args and args[0] == "push":
            raise KeyboardInterrupt
        return real(cwd, *args)
    monkeypatch.setattr(merge, "_git", die_on_push)
    with pytest.raises(KeyboardInterrupt):
        run(world, "merge", "repo", str(wt))
    monkeypatch.setattr(merge, "_git", real)
    assert ledger.merges_in_window(world["state"], time.time()) == 1  # counted until settled
    assert run(world, "merge", "repo", str(wt)) == 0
    assert ledger.merges_in_window(world["state"], time.time()) == 1


def test_never_pushed_intents_do_not_use_up_the_cap(world, monkeypatch):
    for i in range(3):  # three intents in another repo that never reached its origin
        ledger.append_action(world["state"], {"kind": "merge_intent", "id": f"x{i}", "at": time.time() - 60,
                                              "repo": "repo", "to_sha": f"{i}" * 40, "from_sha": "0" * 40})
    wt = fix_worktree(world)
    assert run(world, "merge", "repo", str(wt)) == 0
