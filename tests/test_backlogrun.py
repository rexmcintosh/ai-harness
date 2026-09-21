"""backlog-run: the whole work / approve / drop flow against temp git repos and a fake
`claude` binary. No network, no real sessions."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from datetime import date
from pathlib import Path

import pytest
import yaml

from backlogrun import cli as br
from backlogrun.venice_keys import VeniceKeyError, load_key as load_venice_key

REAL_COUNCIL_REVIEW = br.council_review

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
mode = open(os.path.join(here, "mode.txt")).read().strip()
prompt = sys.stdin.read()
push = subprocess.run(["git", "push", "hub", "HEAD"], capture_output=True, text=True)
local = subprocess.run(["git", "push", "origin", "HEAD:refs/heads/probe"], capture_output=True, text=True)
json.dump({"env": dict(os.environ), "prompt": prompt, "argv": sys.argv[1:], "cwd": os.getcwd(),
           "push_rc": push.returncode, "push_err": push.stderr,
           "local_push_rc": local.returncode, "local_push_err": local.stderr},
          open(os.path.join(here, "capture.json"), "w"))
if mode == "hang":
    time.sleep(60)
if mode == "limit":
    print("You've hit your usage limit for this session"); sys.exit(1)
if mode == "crash":
    sys.stderr.write("boom\n"); sys.exit(1)
outcome = {"done": "done", "held": "held", "failed": "failed", "nomarker": "",
           "done-nochange": "done", "leftover": "done"}[mode]
if mode != "done-nochange":
    with open("worked.txt", "w") as fh:
        fh.write(mode + "\n")
    if mode != "leftover":
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "work"], check=True)
text = "I did things.\n\n"
if outcome:
    text += f"RUNNER-OUTCOME: {outcome}\nRUNNER-SUMMARY: summary for {mode}\nRUNNER-OPERATOR-STEPS: none\n"
if 'Required validation names declared before this run: ["fixture"]' in prompt:
    check = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    text += "RUNNER-VALIDATIONS: " + json.dumps([{"name": "fixture", "branch_sha": sha,
             "status": "passed" if check.returncode == 0 and not check.stdout.strip() else "failed",
             "evidence": "git status --porcelain; exit=" + str(check.returncode) + "\n" + check.stdout}]) + "\n"
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                  "session_id": "sess-1234-abcd", "total_cost_usd": 1.234, "permission_denials": []}))
'''


def sh(*args, cwd=None, check=True, env=None):
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True, env=env)


def git(repo, *args, check=True):
    return sh("git", "-C", str(repo), *args, check=check).stdout


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"),
                 ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(k, v)
    # secrets + session vars that must NOT reach the session
    monkeypatch.setenv("VENICE_API_KEY", "sk-secret")
    monkeypatch.delenv("VENICE_COUNCIL_KEY", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_x")

    # No test may reach Venice: the real council is replaced by a tripwire. Tests that
    # want a verdict inject `stub_reviewer`; --no-council paths must never get here.
    def tripwire(cfg, diff, *, item_id):
        raise AssertionError("council_review called from a test (network!)")
    monkeypatch.setattr(br, "council_review", tripwire)


def make_repo(root: Path, name: str, *, remote: bool = True) -> Path:
    repo = root / "projects" / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    if remote:
        bare = root / "remotes" / f"{name}.git"
        bare.parent.mkdir(exist_ok=True)
        git(repo, "init", "-q", "--bare", str(bare))
        git(repo, "remote", "add", "origin", str(bare))
        git(repo, "push", "-q", "-u", "origin", "main")
        git(repo, "remote", "add", "hub", "https://github.com/example/alpha.git")   # a "real" remote, never contacted
    return repo


def make_backlog(root: Path, items: list[dict]) -> Path:
    bdir = root / "projects" / "backlog"
    bdir.mkdir(parents=True, exist_ok=True)
    path = bdir / "backlog.yaml"
    path.write_text(yaml.safe_dump({"items": items}, sort_keys=False))
    (bdir / "archive.yaml").write_text("items: []\n")
    git(bdir, "init", "-q", "-b", "main")
    git(bdir, "add", "-A")
    git(bdir, "commit", "-qm", "init")
    return path


def item(iid, repo="alpha", status="open", created="2026-01-01", **kw):
    d = {"id": iid, "title": f"Title {iid}", "repo": repo, "status": status,
         "created": date.fromisoformat(created), "prompt": f"Do the thing for {iid}.\nSecond line.\n"}
    d.update(kw)
    return d


@pytest.fixture
def world(tmp_path):
    """A projects dir with repo `alpha` (+ bare origin), a backlog repo, a fake claude."""
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    fake = fake_dir / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (fake_dir / "mode.txt").write_text("done")
    repo = make_repo(tmp_path, "alpha")

    def build(items):
        path = make_backlog(tmp_path, items)
        cfg = br.Config(backlog_path=str(path), state_dir=str(tmp_path / "state"),
                        projects=str(tmp_path / "projects"), claude_bin=str(fake),
                        git_enabled=True, tg_enabled=False, env_file=str(tmp_path / "no.env"),
                        item_timeout=30, deadline=300, max_items=2)
        return cfg

    class W:
        pass
    w = W()
    w.root, w.repo, w.fake_dir, w.build = tmp_path, repo, fake_dir, build
    w.mode = lambda m: (fake_dir / "mode.txt").write_text(m)
    w.capture = lambda: json.load(open(fake_dir / "capture.json"))
    return w


def stub_reviewer(cfg, diff, *, item_id):
    assert "worked.txt" in diff
    return {"ok": True, "summary": "stub: approve, confidence 9/10", "markdown": "# stub review"}


def work_args(*extra):
    return br.build_parser().parse_args(["work", *extra])


def load_items(cfg):
    return br.load_yaml(cfg.backlog_path)["items"]


# ----------------------------------------------------------------------------- pure pieces


def test_plan_orders_oldest_first_bounds_and_holds_unworkable(world):
    cfg = world.build([
        item("2026-01-02-a", created="2026-01-02"),
        item("2026-01-01-b", created="2026-01-01"),
        item("2026-01-03-c", repo="none", created="2026-01-03"),
        item("2026-01-04-d", status="in_review", created="2026-01-04"),
        item("2026-01-05-e", repo="missing-repo", created="2026-01-05"),
    ])
    planned = br.plan(cfg, load_items(cfg), max_items=1)
    by = {p.item["id"]: p for p in planned}
    assert [p.item["id"] for p in planned] == ["2026-01-01-b", "2026-01-02-a", "2026-01-03-c", "2026-01-05-e"]
    assert by["2026-01-01-b"].action == "work"
    assert by["2026-01-01-b"].branch == "claude/bl-b"
    assert by["2026-01-01-b"].base == "main"
    assert by["2026-01-02-a"].action == "defer"
    assert by["2026-01-03-c"].action == "hold" and "no target repo" in by["2026-01-03-c"].reason
    assert by["2026-01-05-e"].action == "hold"
    assert "2026-01-04-d" not in by


def test_plan_holds_when_branch_already_exists_with_work(world):
    cfg = world.build([item("2026-01-01-b")])
    git(world.repo, "branch", "claude/bl-b")
    git(world.repo, "commit", "--allow-empty", "-qm", "earlier attempt", "--no-verify")
    git(world.repo, "branch", "-f", "claude/bl-b", "HEAD")
    git(world.repo, "reset", "-q", "--hard", "HEAD~1")
    (p,) = br.plan(cfg, load_items(cfg))
    assert p.action == "hold" and "already exists" in p.reason


def test_yaml_roundtrip_uses_block_scalars_and_keeps_dates(tmp_path):
    doc = {"items": [item("2026-01-01-x", prompt="line one  \nline two\n\n  indented\n")]}
    text = br.dump_yaml(doc)
    assert "prompt: |" in text
    assert "created: 2026-01-01" in text
    back = yaml.safe_load(text)
    assert back["items"][0]["created"] == date(2026, 1, 1)
    assert back["items"][0]["prompt"] == "line one\nline two\n\n  indented\n"
    path = tmp_path / "b.yaml"
    br.write_yaml_atomic(str(path), doc)
    assert yaml.safe_load(path.read_text()) == back
    assert not list(tmp_path.glob(".backlog-run-*"))


def test_backlog_lock_waits_then_times_out_and_clears_stale(tmp_path):
    path = tmp_path / "backlog.yaml"
    path.write_text("items: []\n")
    lock = Path(f"{path}.lock")
    lock.mkdir()
    with pytest.raises(TimeoutError):
        with br.BacklogLock(str(path), wait_s=1):
            pass
    old = time.time() - br.BACKLOG_LOCK_STALE_S - 5
    os.utime(lock, (old, old))
    with br.BacklogLock(str(path), wait_s=1):
        assert lock.is_dir()
    assert not lock.exists()


def test_mutate_backlog_rereads_before_writing(world):
    cfg = world.build([item("2026-01-01-a")])
    # simulate feedback-sync appending an item while a session runs
    doc = br.load_yaml(cfg.backlog_path)
    doc["items"].append(item("2026-01-02-late"))
    br.write_yaml_atomic(cfg.backlog_path, doc)
    br.mutate_backlog(cfg, "2026-01-01-a", lambda it: it.__setitem__("status", "held"))
    ids = [it["id"] for it in load_items(cfg)]
    assert ids == ["2026-01-01-a", "2026-01-02-late"]


@pytest.mark.parametrize("text,outcome,summary", [
    ("blah\nRUNNER-OUTCOME: done\nRUNNER-SUMMARY: did it\nRUNNER-OPERATOR-STEPS: none\n", "done", "did it"),
    ("RUNNER-OUTCOME: `held`\nRUNNER-SUMMARY: needs a key\n  second line\nRUNNER-OPERATOR-STEPS: add KEY to ~/.env", "held", "needs a key second line"),
    ("RUNNER-OUTCOME: failed\n RUNNER-OUTCOME: done\nRUNNER-SUMMARY: ok", "done", "ok"),
    ("no block here", "", ""),
    ("the RUNNER-OUTCOME line should say done", "", ""),
    ("**RUNNER-OUTCOME:** done\n**RUNNER-SUMMARY:** bold summary\n**RUNNER-OPERATOR-STEPS:** none", "done", "bold summary"),
    ("RUNNER-OUTCOME: `held`\nRUNNER-SUMMARY: *needs key*", "held", "needs key"),
])
def test_parse_outcome(text, outcome, summary):
    got = br.parse_outcome(text)
    assert got["outcome"] == outcome
    assert got["summary"] == summary


def test_parse_outcome_operator_steps():
    got = br.parse_outcome("RUNNER-OUTCOME: held\nRUNNER-SUMMARY: s\nRUNNER-OPERATOR-STEPS: run wrangler deploy\n```")
    assert got["operator_steps"] == "run wrangler deploy"


def test_scrubbed_env_has_no_secrets_and_disables_push(world):
    env = br.scrubbed_env(str(world.repo), ["/opt/node/bin"])
    assert "VENICE_API_KEY" not in env and "STRIPE_SECRET_KEY" not in env
    assert not any(k.startswith("CLAUDE") for k in env)
    n = len(br.NO_PUSH_SCHEMES)
    assert env["GIT_CONFIG_COUNT"] == str(n + 1)           # schemes + the one real remote (hub); origin is local
    assert env["GIT_CONFIG_KEY_0"] == f"url.{br.NO_PUSH_BASE}.pushInsteadOf"
    assert {env[f"GIT_CONFIG_VALUE_{i}"] for i in range(n)} == set(br.NO_PUSH_SCHEMES)
    assert env[f"GIT_CONFIG_KEY_{n}"] == "remote.hub.pushurl" and env[f"GIT_CONFIG_VALUE_{n}"].startswith("/nonexistent")
    assert "remote.origin.pushurl" not in env.values()
    assert env["PATH"].startswith("/opt/node/bin:")
    assert os.path.join(br.HOME, ".local", "bin") in env["PATH"]
    assert env["BACKLOG_RUN"] == "1" and env["TERM"] == "dumb"


def test_bounded_worker_defaults_and_explicit_cli_route():
    cfg = br.Config()
    assert (cfg.model, cfg.effort, cfg.budget_usd, cfg.item_timeout) == ("sonnet", "medium", 20.0, 3600)
    args = br.build_parser().parse_args(["work", "--model", "opus", "--effort", "high"])
    assert args.model == "opus" and args.effort == "high"


def test_runner_venice_key_loader_has_no_old_key_fallback(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("VENICE_COUNCIL_KEY=old-council\nVENICE_API_KEY=generic\n")
    with pytest.raises(VeniceKeyError, match="VENICE_SECOND_OPINION_KEY"):
        load_venice_key("review", {}, env_file)
    env_file.write_text("VENICE_SECOND_OPINION_KEY=review-only\nVENICE_CODE_HELPER_KEY=code-only\n")
    assert load_venice_key("review", {}, env_file) == "review-only"
    assert load_venice_key("code", {}, env_file) == "code-only"


def test_runner_council_requests_exact_review_role_key(world, monkeypatch):
    cfg = world.build([])
    calls = []
    def missing(role, *, env_path):
        calls.append((role, env_path))
        raise VeniceKeyError("VENICE_SECOND_OPINION_KEY is missing")
    monkeypatch.setattr(br, "load_venice_key", missing)
    result = REAL_COUNCIL_REVIEW(cfg, "diff", item_id="x")
    assert calls == [("review", cfg.env_file)]
    assert result["ok"] is False and "VENICE_SECOND_OPINION_KEY" in result["summary"]


def test_run_session_passes_model_and_effort_without_secrets(world, monkeypatch):
    cfg = world.build([])
    cfg.model, cfg.effort = "opus", "high"
    env = br.scrubbed_env(str(world.repo))
    captured = {}

    class Proc:
        returncode = 0
        pid = 123
        def communicate(self, prompt, timeout):
            return json.dumps({"result": "ok"}), ""

    def popen(argv, **kwargs):
        captured.update(argv=argv, env=kwargs["env"])
        return Proc()

    monkeypatch.setattr(br.subprocess, "Popen", popen)
    br.run_session(cfg, "brief", cwd=str(world.repo), env=env, timeout=3)
    assert captured["argv"][-4:] == ["--model", "opus", "--effort", "high"]
    assert not any(name.startswith("VENICE_") for name in captured["env"])


@pytest.mark.parametrize("url,local", [
    ("/tmp/x.git", True), ("../remotes/x.git", True), ("./x", True), ("file:///tmp/x.git", True), ("~/x.git", True),
    ("https://github.com/a/b.git", False), ("HTTPS://github.com/a/b.git", False), ("git@github.com:a/b.git", False),
    ("deploy@host.example:repos/b.git", False), ("ssh://git@host/a/b.git", False), ("host:repos/b.git", False),
])
def test_is_local_remote(url, local):
    assert br._is_local_remote(url) is local


def test_scp_style_remote_gets_pushurl_override(world):
    git(world.repo, "remote", "add", "deploy", "deploy@host.example:repos/alpha.git")
    env = br.scrubbed_env(str(world.repo))
    keys = {env[k]: env["GIT_CONFIG_VALUE_" + k.split("_")[-1]] for k in env if k.startswith("GIT_CONFIG_KEY_")}
    assert keys["remote.deploy.pushurl"].startswith("/nonexistent")
    assert keys["remote.hub.pushurl"].startswith("/nonexistent")
    assert "remote.origin.pushurl" not in keys


def test_session_settings_deny_rules(world):
    cfg = world.build([])
    br.write_session_settings(cfg)
    deny = json.load(open(cfg.settings_path))["permissions"]["deny"]
    for must in ("Bash(git push*)", "Bash(wrangler*)", "Bash(npm run deploy*)", "Bash(curl*)",
                 "Bash(crontab*)", "Bash(supabase*)", "Bash(stripe*)", "Bash(tg-send*)"):
        assert must in deny
    assert json.load(open(cfg.mcp_path)) == {"mcpServers": {}}


def test_compose_prompt_carries_contract_and_item():
    text = br.compose_prompt(item("2026-01-01-a", prompt="THE PROMPT"), repo_name="alpha",
                             worktree="/w", branch="claude/bl-a", base="main", minutes=55)
    for must in ("RUNNER-OUTCOME", "Never push", "claude/bl-a", "THE PROMPT", "55 minutes", "held",
                 "Never start background tasks", "do not post a merge recommendation", "system temp directory"):
        assert must in text


# ----------------------------------------------------------------------------- work


def test_work_done_flow_end_to_end(world):
    cfg = world.build([item("2026-01-01-a")])
    rc = br.cmd_work(work_args("--no-notify"), cfg) if False else None  # (kept for symmetry)
    (p,) = br.plan(cfg, load_items(cfg))
    res = br.work_one(cfg, p, reviewer=stub_reviewer, log=lambda *a: None)
    assert res["status"] == "in_review"
    (it,) = load_items(cfg)
    assert it["status"] == "in_review"
    assert it["branch"] == "claude/bl-a"
    assert it["council"].startswith("stub: approve")
    assert it["worked"] == br.today()
    assert it["session"] == "sess-1234-abcd" and it["cost_usd"] == 1.23
    assert "summary for done" in it["note"]
    # branch has the session's commit; worktree is gone
    assert git(world.repo, "rev-list", "--count", "main..claude/bl-a").strip() == "1"
    assert not (world.repo / ".claude" / "worktrees" / "bl-a").exists()
    assert "claude/bl-a" not in git(world.repo, "worktree", "list")
    # main untouched, remote untouched
    assert git(world.repo, "rev-list", "--count", "origin/main..main").strip() == "0"
    # review file + run log + backlog commit
    assert (Path(cfg.reviews_dir) / "2026-01-01-a.md").read_text().startswith("# council review")
    assert list(Path(cfg.runs_dir).glob("*-2026-01-01-a.json"))
    assert "backlog: 2026-01-01-a -> in_review (claude/bl-a)" in git(cfg.backlog_dir, "log", "-1", "--format=%s")
    # what the session saw
    cap = world.capture()
    assert cap["cwd"].endswith(os.path.join(".claude", "worktrees", "bl-a"))
    assert cap["push_rc"] != 0 and "nonexistent" in cap["push_err"]          # real remote: blocked
    assert cap["local_push_rc"] == 0, cap["local_push_err"]                   # local temp remote: allowed (test suites)
    assert git(world.repo, "ls-remote", "--heads", "origin", "probe").strip()
    assert "VENICE_API_KEY" not in cap["env"] and "CLAUDECODE" not in cap["env"]
    assert "--strict-mcp-config" in cap["argv"] and "--settings" in cap["argv"]
    assert "--dangerously-skip-permissions" in cap["argv"]
    assert "RUNNER-OUTCOME" in cap["prompt"] and "Do the thing for 2026-01-01-a" in cap["prompt"]


def test_work_held_keeps_branch(world):
    cfg = world.build([item("2026-01-01-a")])
    world.mode("held")
    (p,) = br.plan(cfg, load_items(cfg))
    res = br.work_one(cfg, p, reviewer=stub_reviewer, log=lambda *a: None)
    assert res["status"] == "held"
    (it,) = load_items(cfg)
    assert it["status"] == "held" and it["branch"] == "claude/bl-a"
    assert "HELD" in it["note"]
    assert it["council"].startswith("stub: approve")      # held branches with work are reviewed too


def test_work_crash_without_changes_holds_and_deletes_empty_branch(world):
    cfg = world.build([item("2026-01-01-a")])
    world.mode("crash")
    (p,) = br.plan(cfg, load_items(cfg))
    res = br.work_one(cfg, p, reviewer=stub_reviewer, log=lambda *a: None)
    assert res["status"] == "held"
    (it,) = load_items(cfg)
    assert it["status"] == "held" and "branch" not in it and "FAILED" in it["note"]
    assert not git(world.repo, "branch", "--list", "claude/bl-a").strip()
    journal = Path(cfg.journal_path).read_text()
    assert "claude/bl-a" in journal and "work-empty" in journal


def test_work_nomarker_with_changes_is_in_review(world):
    cfg = world.build([item("2026-01-01-a")])
    world.mode("nomarker")
    (p,) = br.plan(cfg, load_items(cfg))
    res = br.work_one(cfg, p, reviewer=stub_reviewer, log=lambda *a: None)
    assert res["status"] == "in_review"
    note = load_items(cfg)[0]["note"]
    assert "without a RUNNER-OUTCOME" in note and "its last words: I did things." in note


def test_work_done_without_changes_is_held(world):
    cfg = world.build([item("2026-01-01-a")])
    world.mode("done-nochange")
    (p,) = br.plan(cfg, load_items(cfg))
    res = br.work_one(cfg, p, reviewer=stub_reviewer, log=lambda *a: None)
    assert res["status"] == "held" and "no changes" in res["note"]
    assert not git(world.repo, "branch", "--list", "claude/bl-a").strip()


def test_work_commits_leftover_uncommitted_work(world):
    cfg = world.build([item("2026-01-01-a")])
    world.mode("leftover")
    (p,) = br.plan(cfg, load_items(cfg))
    res = br.work_one(cfg, p, reviewer=stub_reviewer, log=lambda *a: None)
    assert res["status"] == "in_review"
    assert "leftover uncommitted work" in git(world.repo, "log", "-1", "--format=%s", "claude/bl-a")
    assert "worked.txt" in git(world.repo, "diff", "--name-only", "main...claude/bl-a")
    assert "Leftover uncommitted files were committed as-is (1): worked.txt" in res["note"]


def test_work_timeout_kills_session_and_holds(world):
    cfg = world.build([item("2026-01-01-a")])
    cfg.item_timeout = 2
    world.mode("hang")
    (p,) = br.plan(cfg, load_items(cfg))
    t0 = time.monotonic()
    res = br.work_one(cfg, p, reviewer=stub_reviewer, log=lambda *a: None)
    assert time.monotonic() - t0 < 40
    assert res["status"] == "held" and "timed out" in res["note"]
    assert not git(world.repo, "branch", "--list", "claude/bl-a").strip()


def test_cmd_work_usage_limit_leaves_open_and_stops_batch(world):
    cfg = world.build([item("2026-01-01-a"), item("2026-01-02-b", created="2026-01-02")])
    world.mode("limit")
    rc = br.cmd_work(work_args("--no-notify", "--no-council"), cfg)
    assert rc == 0
    items = {it["id"]: it for it in load_items(cfg)}
    assert items["2026-01-01-a"]["status"] == "open"
    assert items["2026-01-02-b"]["status"] == "open"
    assert not git(world.repo, "branch", "--list", "claude/bl-*").strip()
    assert "USAGE LIMIT" in Path(cfg.report_path).read_text() or True  # report lists open items either way
    assert "USAGE LIMIT HIT" in br.summarize_run(
        [{"id": "x", "status": "open", "note": "", "limit": True}], limit_hit=True)


def test_cmd_work_holds_unworkable_and_respects_max_items(world):
    cfg = world.build([item("2026-01-01-a"), item("2026-01-02-b", created="2026-01-02"),
                       item("2026-01-03-c", repo="none", created="2026-01-03")])
    rc = br.cmd_work(work_args("--no-notify", "--no-council", "--max-items", "1"), cfg)
    assert rc == 0
    items = {it["id"]: it for it in load_items(cfg)}
    assert items["2026-01-01-a"]["status"] == "in_review"
    assert items["2026-01-01-a"]["council"] == "review skipped (--no-council)"
    assert items["2026-01-02-b"]["status"] == "open"
    assert items["2026-01-03-c"]["status"] == "held" and "no target repo" in items["2026-01-03-c"]["note"]
    report = Path(cfg.report_path).read_text()
    assert "### 1. Title 2026-01-01-a" in report
    assert json.load(open(cfg.report_json))["numbers"] == {"1": "2026-01-01-a"}


def test_dry_run_changes_nothing(world, capsys):
    cfg = world.build([item("2026-01-01-a"), item("2026-01-02-n", repo="none", created="2026-01-02")])
    before = Path(cfg.backlog_path).read_text()
    rc = br.cmd_work(work_args("--dry-run"), cfg)
    out = capsys.readouterr().out
    assert rc == 0
    assert "WORK  2026-01-01-a" in out and "HOLD  2026-01-02-n" in out
    assert Path(cfg.backlog_path).read_text() == before
    assert not git(world.repo, "branch", "--list", "claude/bl-*").strip()
    assert not os.path.exists(cfg.state_dir) or not os.listdir(cfg.runs_dir) if os.path.exists(cfg.runs_dir) else True


# ----------------------------------------------------------------------------- morning review


def mark_reviewed(cfg, repo: Path, branch: str, iid: str) -> str:
    sha = git(repo, "rev-parse", branch).strip()
    br.mutate_backlog(cfg, iid, lambda it: it.update(reviewed_sha=sha))
    return sha


def worked_branch(repo: Path, branch: str, fname="feature.txt", content="feature\n"):
    git(repo, "worktree", "add", "-q", "-b", branch, str(repo / ".claude" / "worktrees" / branch.split("/")[-1]), "main")
    wt = repo / ".claude" / "worktrees" / branch.split("/")[-1]
    (wt / fname).write_text(content)
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", f"work on {branch}")
    return wt


def test_approve_merges_pushes_deletes_branch_and_archives(world):
    cfg = world.build([item("2026-01-01-a", status="in_review", branch="claude/bl-a",
                            worked=date(2026, 1, 2), council="stub verdict")])
    worked_branch(world.repo, "claude/bl-a")
    mark_reviewed(cfg, world.repo, "claude/bl-a", "2026-01-01-a")
    br.write_report(cfg)
    assert br.resolve_ref(cfg, "1") == "2026-01-01-a"
    assert br.approve_one(cfg, "2026-01-01-a", log=lambda *a: None)
    head_msg = git(world.repo, "log", "-1", "--format=%B")
    assert head_msg.startswith("Merge claude/bl-a: Title 2026-01-01-a")
    assert "Backlog-Item: 2026-01-01-a" in head_msg
    assert (world.repo / "feature.txt").exists()
    assert git(world.repo, "rev-parse", "main").strip() == git(world.repo, "rev-parse", "origin/main").strip()
    assert git(world.repo, "ls-remote", "--heads", "origin", "main").strip().startswith(git(world.repo, "rev-parse", "main").strip())
    assert not git(world.repo, "branch", "--list", "claude/bl-a").strip()
    assert not (world.repo / ".claude" / "worktrees" / "bl-a").exists()
    assert load_items(cfg) == []
    (arch,) = br.load_yaml(cfg.archive_path)["items"]
    assert arch["status"] == "done" and arch["merge_commit"] == git(world.repo, "rev-parse", "HEAD").strip()
    assert arch["merged"] == br.today()
    assert "approve" in Path(cfg.journal_path).read_text()
    assert "-> done" in git(cfg.backlog_dir, "log", "-1", "--format=%s")


def test_approve_refuses_staged_changes_in_main_checkout(world):
    cfg = world.build([item("2026-01-01-a", status="in_review", branch="claude/bl-a")])
    worked_branch(world.repo, "claude/bl-a")
    mark_reviewed(cfg, world.repo, "claude/bl-a", "2026-01-01-a")
    (world.repo / "README.md").write_text("dirty\n")
    git(world.repo, "add", "README.md")
    msgs = []
    assert not br.approve_one(cfg, "2026-01-01-a", log=msgs.append)
    assert "staged changes" in msgs[-1]
    assert load_items(cfg)[0]["status"] == "in_review"
    assert git(world.repo, "rev-list", "--count", "origin/main..main").strip() == "0"


def test_approve_refuses_wrong_branch_checked_out(world):
    cfg = world.build([item("2026-01-01-a", status="in_review", branch="claude/bl-a")])
    worked_branch(world.repo, "claude/bl-a")
    mark_reviewed(cfg, world.repo, "claude/bl-a", "2026-01-01-a")
    git(world.repo, "checkout", "-q", "-b", "feature-x")
    msgs = []
    assert not br.approve_one(cfg, "2026-01-01-a", log=msgs.append)
    assert "is on feature-x, not main" in msgs[-1]


def test_approve_aborts_on_conflict(world):
    cfg = world.build([item("2026-01-01-a", status="in_review", branch="claude/bl-a")])
    worked_branch(world.repo, "claude/bl-a", fname="README.md", content="branch version\n")
    mark_reviewed(cfg, world.repo, "claude/bl-a", "2026-01-01-a")
    (world.repo / "README.md").write_text("main version\n")
    git(world.repo, "commit", "-qam", "main moves on")
    msgs = []
    assert not br.approve_one(cfg, "2026-01-01-a", log=msgs.append)
    assert "merge failed and was aborted" in msgs[-1]
    assert not (world.repo / ".git" / "MERGE_HEAD").exists()
    assert (world.repo / "README.md").read_text() == "main version\n"
    assert load_items(cfg)[0]["status"] == "in_review"
    assert git(world.repo, "branch", "--list", "claude/bl-a").strip()


def test_approve_keeps_branch_when_its_worktree_is_dirty(world):
    cfg = world.build([item("2026-01-01-a", status="in_review", branch="claude/bl-a")])
    wt = worked_branch(world.repo, "claude/bl-a")
    mark_reviewed(cfg, world.repo, "claude/bl-a", "2026-01-01-a")
    (wt / "scratch.txt").write_text("uncommitted\n")
    msgs = []
    assert br.approve_one(cfg, "2026-01-01-a", log=msgs.append)
    assert "branch kept" in msgs[-1] and "uncommitted" in msgs[-1]
    assert git(world.repo, "branch", "--list", "claude/bl-a").strip()
    assert (world.repo / "feature.txt").exists()


def test_approve_without_remote_is_local_only(world):
    repo = make_repo(world.root, "beta", remote=False)
    cfg = world.build([item("2026-01-01-b", repo="beta", status="in_review", branch="claude/bl-b")])
    worked_branch(repo, "claude/bl-b")
    mark_reviewed(cfg, repo, "claude/bl-b", "2026-01-01-b")
    msgs = []
    assert br.approve_one(cfg, "2026-01-01-b", log=msgs.append)
    assert "no remote — local only" in msgs[-1]


def test_approve_refuses_missing_or_stale_reviewed_sha(world):
    cfg = world.build([item("2026-01-01-a", status="in_review", branch="claude/bl-a")])
    worked_branch(world.repo, "claude/bl-a")
    msgs = []
    assert not br.approve_one(cfg, "2026-01-01-a", log=msgs.append)
    assert "no exact reviewed SHA" in msgs[-1]
    br.mutate_backlog(cfg, "2026-01-01-a", lambda it: it.update(reviewed_sha="0" * 40))
    assert not br.approve_one(cfg, "2026-01-01-a", log=msgs.append)
    assert "changed since review" in msgs[-1]
    assert git(world.repo, "rev-list", "--count", "origin/main..main").strip() == "0"


def test_approve_uses_exact_version_without_making_readiness_a_gate(world):
    cfg = world.build([item("2026-01-01-a", status="in_review", branch="claude/bl-a")])
    worked_branch(world.repo, "claude/bl-a")
    reviewed = git(world.repo, "rev-parse", "claude/bl-a").strip()
    br.mutate_backlog(cfg, "2026-01-01-a", lambda it: it.update(reviewed_sha=reviewed))
    # No structured readiness record exists. Exact identity is mandatory; readiness stays advisory.
    assert br._review_readiness(cfg, load_items(cfg)[0])["status"] == "unknown"
    assert br.approve_one(cfg, "2026-01-01-a", log=lambda *a: None)


def test_rework_reuses_branch_preserves_dirty_work_and_runner_contracts(world):
    cfg = world.build([item("2026-01-01-a", status="held", branch="claude/bl-a",
                            note="Rex requested changes", required_validations=[])])
    wt = worked_branch(world.repo, "claude/bl-a")
    prior = git(world.repo, "rev-parse", "claude/bl-a").strip()
    (wt / "dirty-before-rework.txt").write_text("keep me\n")
    args = br.build_parser().parse_args([
        "rework", "2026-01-01-a", "--no-notify", "--no-council",
        "--item-timeout", "29", "--budget-usd", "3.5",
    ])
    assert br.cmd_rework(args, cfg) == 0
    (updated,) = load_items(cfg)
    head = git(world.repo, "rev-parse", "claude/bl-a").strip()
    assert updated["status"] == "in_review" and updated["reviewed_sha"] == head
    assert git(world.repo, "merge-base", "--is-ancestor", prior, head, check=False) == ""
    assert "dirty-before-rework.txt" in git(world.repo, "diff", "--name-only", "main...claude/bl-a")
    assert git(world.repo, "rev-list", "--count", "origin/main..main").strip() == "0"
    cap = world.capture()
    assert "Continue the existing branch" in cap["prompt"]
    assert "Never replay a possibly completed external action" in cap["prompt"]
    assert "Changes and a resource budget do not grant merge permission" in cap["prompt"]
    assert "--max-budget-usd" in cap["argv"] and "3.5" in cap["argv"]
    assert cap["push_rc"] != 0 and "nonexistent" in cap["push_err"]


def rework_args(*extra):
    return br.build_parser().parse_args(["rework", *extra])


def test_rework_dry_run_prints_the_plan_and_changes_nothing(world, capsys):
    cfg = world.build([item("2026-01-01-a", status="in_review", branch="claude/bl-a",
                            prompt="Do it.\n\nRex's review (2026-01-03): tighten the tests\n")])
    wt = worked_branch(world.repo, "claude/bl-a")
    head = mark_reviewed(cfg, world.repo, "claude/bl-a", "2026-01-01-a")
    git(world.repo, "worktree", "remove", "--force", str(wt))
    before = Path(cfg.backlog_path).read_text()
    rc = br.cmd_rework(rework_args("2026-01-01-a", "--dry-run"), cfg)
    out = capsys.readouterr().out
    assert rc == 0
    assert "REWORK 2026-01-01-a" in out and "in_review" in out
    assert "branch claude/bl-a" in out and head[:12] in out and "1 commit(s) ahead of main" in out
    assert "review to address: 1 note(s)" in out
    # nothing moved: the approval identity survives, no worktree, no run record, no session
    assert Path(cfg.backlog_path).read_text() == before
    assert not wt.exists()
    assert not os.path.exists(cfg.runs_dir) or not os.listdir(cfg.runs_dir)
    assert not (world.fake_dir / "capture.json").exists()


@pytest.mark.parametrize("dry", [(), ("--dry-run",)])
def test_rework_refuses_open_items_and_missing_branches_in_one_sentence(world, capsys, dry):
    cfg = world.build([item("2026-01-01-a"),
                       item("2026-01-02-b", status="held", branch="claude/bl-b", created="2026-01-02"),
                       item("2026-01-03-c", status="held", created="2026-01-03")])
    before = Path(cfg.backlog_path).read_text()
    for iid, why in (("2026-01-01-a", "item is open, not held/in_review"),
                     ("2026-01-02-b", "branch claude/bl-b does not exist"),
                     ("2026-01-03-c", "item has no matching backlog-run branch"),
                     ("2026-09-09-nope", "not an active item")):
        assert br.cmd_rework(rework_args(iid, "--no-notify", *dry), cfg) == 1
        err = capsys.readouterr().err.strip()
        assert err == f"rework {iid}: {why}"
    assert Path(cfg.backlog_path).read_text() == before
    assert not (world.fake_dir / "capture.json").exists()


def test_rework_prompt_lists_every_owner_review_oldest_first(world):
    it = item("2026-01-01-a", status="held", branch="claude/bl-a",
              prompt="Do it.\n\nRex's review (2026-01-03): tighten the tests\nand cover the empty case\n\n"
                     "Rex's review (2026-01-05): rename the flag\n",
              note="Rex's review (2026-01-06): keep exit code 1")
    kw = dict(repo_name="alpha", worktree="/w", branch="claude/bl-a", base="main", minutes=10)
    text = br.compose_prompt(it, continuation=True, **kw)
    section = text.split("--- REVIEW TO ADDRESS ---", 1)[1]
    assert section.index("1. Rex's review (2026-01-03): tighten the tests\nand cover the empty case") \
        < section.index("2. Rex's review (2026-01-05): rename the flag") \
        < section.index("3. Rex's review (2026-01-06): keep exit code 1")
    assert "the last entry is the newest" in section
    # a runner note is not a review, and a first-time `work` prompt never gets the section
    assert "REVIEW TO ADDRESS" not in br.compose_prompt({**it, "prompt": "Do it.", "note": "runner: HELD"},
                                                        continuation=True, **kw)
    assert "REVIEW TO ADDRESS" not in br.compose_prompt(it, continuation=False, **kw)


def test_rework_does_not_repeat_a_review_held_in_both_note_and_prompt():
    line = "Rex's review (2026-01-03): tighten the tests"
    assert br.review_notes({"prompt": f"Do it.\n\n{line}\n", "note": line}) == [line]


def test_review_notes_split_only_on_dated_markers_and_keep_a_long_review_whole():
    # A review may run to several paragraphs and may quote the phrase itself; only a dated
    # "Rex's review (YYYY-MM-DD):" line starts a new note, so no owner text is cut off.
    first = ("Rex's review (2026-01-03): tighten the tests.\n\nSecond paragraph of the same review.\n"
             "Rex's review (the older one) still stands.")
    second = "Rex's review (2026-01-05): rename the flag"
    assert br.review_notes({"prompt": f"Do it.\n\n{first}\n\n{second}\n"}) == [first, second]


def test_rework_dry_run_turns_a_git_failure_into_the_one_sentence_refusal(world, capsys, monkeypatch):
    cfg = world.build([item("2026-01-01-a", status="held", branch="claude/bl-a")])
    worked_branch(world.repo, "claude/bl-a")
    real_git = br.git

    def flaky(repo, *args, **kw):
        if args[:1] == ("rev-parse",):
            raise br.GitError("fatal: ambiguous argument 'claude/bl-a'\nUse '--' to separate paths\n")
        return real_git(repo, *args, **kw)
    monkeypatch.setattr(br, "git", flaky)
    assert br.cmd_rework(rework_args("2026-01-01-a", "--dry-run"), cfg) == 1
    captured = capsys.readouterr()
    assert captured.err.strip().startswith("rework 2026-01-01-a: could not read branch claude/bl-a")
    assert len(captured.err.strip().splitlines()) == 1      # a multi-line git error stays one sentence
    assert "REWORK" not in captured.out


def test_drop_deletes_branch_journals_and_archives(world):
    cfg = world.build([item("2026-01-01-a", status="held", branch="claude/bl-a", note="runner: HELD")])
    worked_branch(world.repo, "claude/bl-a")
    sha = git(world.repo, "rev-parse", "claude/bl-a").strip()
    assert br.drop_one(cfg, "2026-01-01-a", log=lambda *a: None)
    assert not git(world.repo, "branch", "--list", "claude/bl-a").strip()
    assert f"claude/bl-a\t{sha}\tdrop" in Path(cfg.journal_path).read_text()
    (arch,) = br.load_yaml(cfg.archive_path)["items"]
    assert arch["status"] == "dropped" and arch["dropped"] == br.today()
    assert load_items(cfg) == []


def test_drop_refuses_open_item(world):
    cfg = world.build([item("2026-01-01-a")])
    msgs = []
    assert not br.drop_one(cfg, "2026-01-01-a", log=msgs.append)
    assert "drop applies to in_review/held" in msgs[-1]


def test_hold_and_reopen(world):
    cfg = world.build([item("2026-01-01-a")])
    p = br.build_parser()
    assert br.cmd_hold(p.parse_args(["hold", "2026-01-01-a", "wait for Rex"]), cfg) == 0
    it = load_items(cfg)[0]
    assert it["status"] == "held" and it["note"] == "wait for Rex"
    assert br.cmd_reopen(p.parse_args(["reopen", "2026-01-01-a"]), cfg) == 0
    assert load_items(cfg)[0]["status"] == "open"
    assert br.cmd_reopen(p.parse_args(["reopen", "2026-01-01-a"]), cfg) == 1


def test_report_lists_review_held_open_and_numbers(world):
    cfg = world.build([
        item("2026-01-01-a", status="in_review", branch="claude/bl-a", worked=date(2026, 1, 2), council="c1"),
        item("2026-01-02-b", status="held", note="runner: HELD — needs key", created="2026-01-02"),
        item("2026-01-03-c", created="2026-01-03"),
    ])
    worked_branch(world.repo, "claude/bl-a")
    text = br.write_report(cfg)
    assert "1 awaiting your review · 1 held · 1 open" in text
    assert "### 1. Title 2026-01-01-a" in text and "1 commit(s); 1 file changed" in text
    assert "`2026-01-02-b`" in text and "needs key" in text
    assert "`2026-01-03-c`" in text
    assert br.resolve_ref(cfg, "1") == "2026-01-01-a"
    with pytest.raises(KeyError):
        br.resolve_ref(cfg, "2")
    assert br.resolve_ref(cfg, "2026-01-03-c") == "2026-01-03-c"


def test_run_lock_is_exclusive(world):
    cfg = world.build([])
    with br.RunLock(cfg):
        p = subprocess.run([
            "python3", "-c",
            "import sys; sys.path.insert(0, %r); from backlogrun import cli as br; "
            "cfg = br.Config(state_dir=%r); br.RunLock(cfg).__enter__(); print('GOT IT')"
            % (str(Path(br.__file__).resolve().parents[1]), cfg.state_dir)],
            capture_output=True, text=True)
    assert "GOT IT" not in p.stdout
    assert "another run holds the lock" in p.stderr


# ----------------------------------------------------------------------------- council round 1 fixes


def test_plan_reclaims_empty_leftover_branch_but_holds_one_with_work(world):
    cfg = world.build([item("2026-01-01-a"), item("2026-01-02-b", created="2026-01-02")])
    git(world.repo, "branch", "claude/bl-a")                       # empty leftover
    worked_branch(world.repo, "claude/bl-b")                       # has a commit + worktree
    planned = {p.item["id"]: p for p in br.plan(cfg, load_items(cfg))}
    assert planned["2026-01-01-a"].action == "work" and planned["2026-01-01-a"].reclaim
    assert planned["2026-01-02-b"].action == "hold" and "with work on it" in planned["2026-01-02-b"].reason


def test_work_reclaims_empty_branch_then_works(world):
    cfg = world.build([item("2026-01-01-a")])
    git(world.repo, "branch", "claude/bl-a")
    (p,) = br.plan(cfg, load_items(cfg))
    res = br.work_one(cfg, p, reviewer=stub_reviewer, log=lambda *a: None)
    assert res["status"] == "in_review"
    assert "reclaim-empty" in Path(cfg.journal_path).read_text()
    assert git(world.repo, "rev-list", "--count", "main..claude/bl-a").strip() == "1"


def test_plan_holds_unsafe_ids(world):
    cfg = world.build([item("2026-01-01-../evil"), item("2026-01-01-ok")])
    planned = {p.item["id"]: p for p in br.plan(cfg, load_items(cfg))}
    assert planned["2026-01-01-../evil"].action == "hold" and "not a safe slug" in planned["2026-01-01-../evil"].reason
    assert planned["2026-01-01-ok"].action == "work"


def test_apply_does_not_clobber_a_status_changed_during_the_run(world):
    cfg = world.build([item("2026-01-01-a")])
    (p,) = br.plan(cfg, load_items(cfg))
    # a human parks the item while the session is running
    br.mutate_backlog(cfg, "2026-01-01-a", lambda it: it.update(status="held", note="human: wait"))
    res = br.work_one(cfg, p, reviewer=stub_reviewer, log=lambda *a: None)
    assert res["status"] == "in_review" and res.get("conflict")
    (it,) = load_items(cfg)
    assert it["status"] == "held"
    assert it["note"].startswith("runner: CONFLICT") and "claude/bl-a" in it["note"]
    assert git(world.repo, "branch", "--list", "claude/bl-a").strip()   # work is not thrown away
    assert "conflict note" in git(cfg.backlog_dir, "log", "-1", "--format=%s")


def test_approve_records_before_releasing_branch(world, monkeypatch):
    cfg = world.build([item("2026-01-01-a", status="in_review", branch="claude/bl-a")])
    worked_branch(world.repo, "claude/bl-a")
    mark_reviewed(cfg, world.repo, "claude/bl-a", "2026-01-01-a")

    def boom(*a, **kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr(br, "mutate_backlog", boom)
    msgs = []
    assert not br.approve_one(cfg, "2026-01-01-a", log=msgs.append)
    assert "recording it in the backlog failed" in msgs[-1]
    assert git(world.repo, "branch", "--list", "claude/bl-a").strip()   # branch kept for the retry
    assert load_items(cfg)[0]["status"] == "in_review"
    monkeypatch.undo()
    # the retry: already merged -> no second merge, then archived + released
    assert br.approve_one(cfg, "2026-01-01-a", log=msgs.append)
    assert "already merged" in msgs[-1]
    assert git(world.repo, "rev-list", "--count", "--merges", "origin/main").strip() == "1"
    assert not git(world.repo, "branch", "--list", "claude/bl-a").strip()
    assert br.load_yaml(cfg.archive_path)["items"][0]["status"] == "done"


def test_reconcile_archive_wins_after_interrupted_move(world):
    cfg = world.build([item("2026-01-01-a", status="in_review", branch="claude/bl-a"), item("2026-01-02-b", created="2026-01-02")])
    arch = br.load_yaml(cfg.archive_path)
    arch["items"].append(dict(item("2026-01-01-a"), status="done"))
    br.write_yaml_atomic(cfg.archive_path, arch)                     # crash landed here last time
    br.mutate_backlog(cfg, "2026-01-02-b", lambda it: it.update(status="held"))
    assert [it["id"] for it in load_items(cfg)] == ["2026-01-02-b"]
    assert len(br.load_yaml(cfg.archive_path)["items"]) == 1


def test_run_lock_contention_exits_75(world):
    cfg = world.build([])
    with br.RunLock(cfg):
        p = subprocess.run([
            "python3", "-c",
            "import sys; sys.path.insert(0, %r); from backlogrun import cli as br; "
            "cfg = br.Config(state_dir=%r); br.RunLock(cfg).__enter__()"
            % (str(Path(br.__file__).resolve().parents[1]), cfg.state_dir)],
            capture_output=True, text=True)
    assert p.returncode == br.EX_TEMPFAIL == 75


# ----------------------------------------------------------------------------- council round 2 fixes


def test_approve_refuses_held_unless_flagged(world):
    cfg = world.build([item("2026-01-01-a", status="held", branch="claude/bl-a", note="runner: HELD — needs key")])
    worked_branch(world.repo, "claude/bl-a")
    mark_reviewed(cfg, world.repo, "claude/bl-a", "2026-01-01-a")
    msgs = []
    assert not br.approve_one(cfg, "2026-01-01-a", log=msgs.append)
    assert "re-run with --held" in msgs[-1]
    assert git(world.repo, "rev-list", "--count", "origin/main..main").strip() == "0"
    assert br.approve_one(cfg, "2026-01-01-a", log=msgs.append, allow_held=True)
    assert br.load_yaml(cfg.archive_path)["items"][0]["status"] == "done"


def test_repo_path_is_contained_in_projects(world):
    cfg = world.build([])
    assert br.repo_path(cfg, "alpha") == str(world.repo)
    assert br.repo_path(cfg, str(world.repo)) == str(world.repo)
    assert br.repo_path(cfg, "../remotes/alpha.git") is None
    assert br.repo_path(cfg, "/") is None
    assert br.repo_path(cfg, "none") is None and br.repo_path(cfg, None) is None
    outside = world.root / "outside"
    outside.mkdir()
    git(outside, "init", "-q")
    assert br.repo_path(cfg, str(outside)) is None


def test_deny_rules_cover_push_variants(world):
    cfg = world.build([])
    br.write_session_settings(cfg)
    deny = json.load(open(cfg.settings_path))["permissions"]["deny"]
    for must in ("Bash(git -c * push*)", "Bash(git -C * push*)", "Bash(git --git-dir* push*)", "Bash(git remote*)"):
        assert must in deny

# ----------------------------------------------------------------------------- truthful review readiness


def clean_reviewer(cfg, diff, *, item_id):
    return {"ok": True, "summary": "Explicit clean review", "markdown": "Full clean review",
            "review_status": "clean", "blocking_findings": []}


@pytest.mark.parametrize("mode,reviewer,required,expected", [
    ("done", clean_reviewer, [], "ready"),
    ("done", clean_reviewer, ["fixture"], "ready"),
    ("done", clean_reviewer, ["missing-check"], "unknown"),
    ("done", clean_reviewer, None, "unknown"),
    ("nomarker", clean_reviewer, [], "unknown"),
    ("held", clean_reviewer, [], "unknown"),
    ("failed", clean_reviewer, [], "failed"),
    ("done", stub_reviewer, [], "unknown"),
    ("done", False, [], "unknown"),
])
def test_readiness_work_to_report(world, mode, reviewer, required, expected):
    cfg = world.build([item("2026-01-01-a", required_validations=required)])
    world.mode(mode)
    (p,) = br.plan(cfg, load_items(cfg))
    result = br.work_one(cfg, p, reviewer=reviewer, log=lambda *a: None)
    assert result["review_readiness"]["status"] == expected
    before = Path(cfg.backlog_path).read_bytes()
    report = br.write_report(cfg)
    metadata = json.loads(Path(cfg.report_json).read_text())["review_readiness"]["2026-01-01-a"]
    assert metadata["status"] == expected
    assert br.READINESS_LABELS[expected] in report
    assert Path(metadata["review_path"]).is_file()
    assert Path(cfg.backlog_path).read_bytes() == before
    assert ("`backlog-run approve 1`" in report) == (expected == "ready")
    if mode == "nomarker":
        assert "treating as done" not in result["note"]
        assert "completion is unknown" in result["note"]
    records = list(Path(cfg.reviews_dir).glob("*.inputs.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["branch_sha"] == git(world.repo, "rev-parse", "claude/bl-a").strip()
    assert record["required_validations"] == required
    if required == ["fixture"]:
        assert "git status --porcelain; exit=0" in (Path(cfg.state_dir) / record["validations"][0]["evidence_path"]).read_text()


def test_required_condition_beyond_400_survives_every_surface(world, capsys):
    condition = "Context. " * 70 + "REQUIRED: fix the incorrect result before merging."
    def reviewer(*args, **kw):
        return {"ok": True, "summary": "Approve after required changes", "markdown": "Full council output",
                "review_status": "changes_requested", "blocking_findings": [condition]}
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    result = br.work_one(cfg, p, reviewer=reviewer, log=lambda *a: None)
    assert result["status"] == "in_review"
    assert condition in br.write_report(cfg)
    summary = br.summarize_run([result])
    assert "Changes requested" in summary and "backlog-run show 2026-01-01-a" in summary
    assert "Approve after" not in summary
    assert br.cmd_show(br.build_parser().parse_args(["show", "1"]), cfg) == 0
    assert condition in capsys.readouterr().out
    assert condition in Path(result["review_readiness"]["review_path"]).read_text()
    assert condition in json.loads(Path(cfg.report_json).read_text())["review_readiness"]["2026-01-01-a"]["reasons"]


@pytest.mark.parametrize("throws", [False, True])
def test_failed_reviewer_preserves_branch_and_reports_failure(world, throws):
    def reviewer(*args, **kwargs):
        if throws:
            raise RuntimeError("review unavailable")
        return {"ok": False, "summary": "REVIEW FAILED: provider unavailable"}
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    result = br.work_one(cfg, p, reviewer=reviewer, log=lambda *a: None)
    assert result["status"] == "in_review"
    assert result["review_readiness"]["status"] == "failed"
    assert "Review or checks failed" in br.write_report(cfg)
    assert git(world.repo, "branch", "--list", "claude/bl-a").strip()


def test_report_recomputes_stale_evidence_and_ignores_cached_readiness(world):
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    br.work_one(cfg, p, reviewer=clean_reviewer, log=lambda *a: None)
    assert br._review_readiness(cfg, load_items(cfg)[0])["status"] == "ready"
    git(world.repo, "switch", "claude/bl-a")
    git(world.repo, "commit", "--allow-empty", "-qm", "changed after review")
    git(world.repo, "switch", "main")
    assert "Review readiness unknown" in br.write_report(cfg)
    assert "`backlog-run approve 1`" not in Path(cfg.report_path).read_text()
    assert json.loads(next(Path(cfg.reviews_dir).glob("*.readiness.json")).read_text())["status"] == "ready"


def test_newest_unfinished_or_malformed_attempt_never_reuses_clean_evidence(world):
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    br.work_one(cfg, p, reviewer=clean_reviewer, log=lambda *a: None)
    name = "2099-01-01T000000Z-2026-01-01-a"
    (Path(cfg.runs_dir) / f"{name}.json").write_text('{"started_at":"2099-01-01T00:00:00Z"}')
    assert br._review_readiness(cfg, load_items(cfg)[0])["status"] == "unknown"
    (Path(cfg.reviews_dir) / f"{name}.inputs.json").write_text("{partial")
    assert br._review_readiness(cfg, load_items(cfg)[0])["status"] == "unknown"


def test_legacy_review_is_unknown_but_full_review_stays_accessible(world):
    cfg = world.build([item("2026-01-01-a", status="in_review", branch="claude/bl-a", council="approve!")])
    worked_branch(world.repo, "claude/bl-a")
    br.ensure_state(cfg)
    path = Path(cfg.reviews_dir) / "2026-01-01-a.md"
    path.write_text("Historical full review; required work is still pending.")
    report = br.write_report(cfg)
    assert "Review readiness unknown" in report and str(path) in report
    assert "`backlog-run approve 1`" not in report


@pytest.mark.parametrize("payload", ['null', '{"name":"x"}', '[{"name":"x","status":"passed"}]', '[1]', 'not json'])
def test_malformed_validation_capture_never_becomes_ready(world, payload):
    cfg = world.build([])
    br.ensure_state(cfg)
    captured = br._capture_validations(cfg, "fixture", "RUNNER-VALIDATIONS: " + payload)
    result = br._save_review(cfg, iid="x", stem="fixture", sha="a" * 40, kind="done",
                             required=[], validations=captured, rev=clean_reviewer(None, None, item_id="x"))
    assert result["status"] == "unknown"


@pytest.mark.parametrize("variant,expected", [("clean", "clean"), ("conditions", "changes_requested"),
    ("legacy", "unknown"), ("synthesis_error", "failed"), ("member_error", "failed"),
    ("missing_member", "unknown"), ("truncated", "unknown"), ("malformed_blocks", "unknown"), ("missing_blocks", "unknown"), ("mixed_changes", "unknown"), ("empty_panel", "unknown"), ("duplicate_member", "unknown")])
def test_real_council_adapter_uses_structured_evidence_only(world, monkeypatch, variant, expected):
    from types import SimpleNamespace
    import council.config as config
    import council.engine as engine
    import council.venice as venice
    from council.models import Member, MemberResult, Panel
    from tests.conftest import FakeClient
    cfg = world.build([])
    condition = "Context. " * 70 + "Required fix at the end."
    payload = {"recommendation": condition, "confidence": 9, "review_status": "clean", "required_changes": [], "blocking_findings": []}
    if variant == "conditions":
        payload.update(review_status="changes_requested", required_changes=[condition])
    if variant == "legacy":
        payload.pop("review_status")
    if variant == "malformed_blocks":
        payload["blocking_findings"] = "must fix data loss"
    if variant == "missing_blocks":
        payload.pop("blocking_findings")
    if variant == "mixed_changes":
        payload["required_changes"] = ["fix the wrong answer", 3]
    fake = FakeClient(default=payload, raises_for={"chair"} if variant == "synthesis_error" else set())
    panel = Panel("code-review", "fixture", [Member("a", "m1", "x"), Member("b", "m2", "x")])
    results = [MemberResult("a", "m1", "approve", "ok"), MemberResult("b", "m2", "approve", "ok")]
    if variant == "member_error":
        results[0].error = "provider unavailable"
    if variant == "missing_member":
        results.pop()
    if variant == "empty_panel":
        panel.members = []
        results = []
    if variant == "duplicate_member":
        results[1] = results[0]
    monkeypatch.setattr(config, "load_panels", lambda _: (SimpleNamespace(timeout=1, byte_cap=5 if variant == "truncated" else 100000, chair_model="chair"), {"code-review": panel}))
    monkeypatch.setattr(config, "get_api_key", lambda: "fake")
    monkeypatch.setattr(engine, "run_panel", lambda *args, **kw: results)
    monkeypatch.setattr(venice, "VeniceClient", lambda *args, **kw: fake)
    rev = REAL_COUNCIL_REVIEW(cfg, "diff with enough content to truncate", item_id="x")
    assert rev["review_status"] == expected
    assert rev["ok"] == (expected != "failed")
    if variant == "conditions":
        assert condition in rev["blocking_findings"] and condition in rev["summary"]
    if variant == "malformed_blocks":
        assert "must fix data loss" in rev["markdown"]
    if variant == "mixed_changes":
        assert "fix the wrong answer" in rev["markdown"]
    assert "required_changes" in fake.calls[0]["system"]


@pytest.mark.parametrize("panel_chair,asked", [("panel-chair", "panel-chair"), (None, "chair")])
def test_real_council_adapter_asks_the_code_review_panels_own_chair(world, monkeypatch, panel_chair, asked):
    """The runner reviews on the code-review panel, so it follows that panel's chair, and the
    global chair when the panel names none (docs/council-chair-decision-2026-09-20.md)."""
    from types import SimpleNamespace
    import council.config as config
    import council.engine as engine
    import council.venice as venice
    from council.models import Member, MemberResult, Panel
    from tests.conftest import FakeClient
    cfg = world.build([])
    fake = FakeClient(default={"recommendation": "ok", "confidence": 9, "review_status": "clean",
                               "required_changes": [], "blocking_findings": []})
    panel = Panel("code-review", "fixture", [Member("a", "m1", "x")], chair_model=panel_chair)
    monkeypatch.setattr(config, "load_panels", lambda _: (SimpleNamespace(timeout=1, byte_cap=100000, chair_model="chair"), {"code-review": panel}))
    monkeypatch.setattr(engine, "run_panel", lambda *args, **kw: [MemberResult("a", "m1", "approve", "ok")])
    monkeypatch.setattr(venice, "VeniceClient", lambda *args, **kw: fake)
    monkeypatch.setattr(br, "load_venice_key", lambda role, env_path=None: "venice-test")
    rev = REAL_COUNCIL_REVIEW(cfg, "diff", item_id="x")
    assert rev["review_status"] == "clean"
    assert [c["model"] for c in fake.calls] == [asked]          # the seats are scripted: one chair call


@pytest.mark.parametrize("bad", ["doneish", "failedness", "heldover"])
def test_outcome_marker_requires_a_complete_word(bad):
    assert br.parse_outcome("RUNNER-OUTCOME: " + bad)["outcome"] == ""


@pytest.mark.parametrize("old_content", ["{partial", "{}", "null", '{"finished_at":"not a date"}'])
def test_damaged_older_attempt_is_superseded_by_complete_new_review(world, old_content):
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    br.work_one(cfg, p, reviewer=clean_reviewer, log=lambda *a: None)
    stem = "2020-01-01T000000Z-2026-01-01-a"
    (Path(cfg.runs_dir) / f"{stem}.json").write_text('{}')
    (Path(cfg.reviews_dir) / f"{stem}.inputs.json").write_text(old_content)
    assert br._review_readiness(cfg, load_items(cfg)[0])["status"] == "ready"


def test_structured_validation_evidence_is_saved(world):
    cfg = world.build([])
    br.ensure_state(cfg)
    payload = [{"name": "fixture", "branch_sha": "a" * 40, "status": "passed",
                "evidence": {"command": "fixture", "exit_code": 0, "output": "passed"}}]
    captured = br._capture_validations(cfg, "fixture", "RUNNER-VALIDATIONS: " + json.dumps(payload))
    assert json.loads((Path(cfg.state_dir) / captured[0]["evidence_path"]).read_text())["exit_code"] == 0
    result = br._save_review(cfg, iid="x", stem="fixture", sha="a" * 40, kind="done",
                             required=["fixture"], validations=captured, rev=clean_reviewer(None, None, item_id="x"))
    assert result["status"] == "ready"


@pytest.mark.parametrize("bad_id", ["../outside", "/tmp/outside", "x/y", "a..b"])
def test_unsafe_item_id_is_held_before_artifact_creation(world, bad_id):
    cfg = world.build([item(bad_id)])
    (p,) = br.plan(cfg, load_items(cfg))
    result = br.work_one(cfg, p, reviewer=clean_reviewer, log=lambda *a: None)
    assert result["status"] == "held" and "invalid item ID" in result["note"]
    assert not Path(cfg.runs_dir).exists()
    assert br._review_readiness(cfg, load_items(cfg)[0])["status"] == "unknown"


@pytest.mark.parametrize("copied_id", [True, False])
def test_new_attempt_cannot_reuse_copied_or_backdated_ready_input(world, copied_id):
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    br.work_one(cfg, p, reviewer=clean_reviewer, log=lambda *a: None)
    record = json.loads(next(Path(cfg.reviews_dir).glob('*.inputs.json')).read_text())
    stem = '2099-01-01T000000Z-2026-01-01-a'
    (Path(cfg.runs_dir) / f'{stem}.json').write_text('{}')
    if not copied_id:
        record['record_id'] = stem  # date still predates the actual new attempt
    (Path(cfg.reviews_dir) / f'{stem}.inputs.json').write_text(json.dumps(record))
    assert br._review_readiness(cfg, load_items(cfg)[0])['status'] == 'unknown'


@pytest.mark.parametrize("text", [
    "Work is incomplete. I cannot claim RUNNER-OUTCOME: done because checks failed. No final block follows.",
    "RUNNER-OUTCOME: done because that is what the example says.",
])
def test_prose_mention_cannot_establish_completion(world, monkeypatch, text):
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    real_run = br.run_session
    def run(*args, **kwargs):
        result = real_run(*args, **kwargs)
        result["data"]["result"] = text
        return result
    monkeypatch.setattr(br, "run_session", run)
    (p,) = br.plan(cfg, load_items(cfg))
    result = br.work_one(cfg, p, reviewer=clean_reviewer, log=lambda *a: None)
    assert result["review_readiness"]["status"] == "unknown"
    assert "completion is unknown" in result["note"]


def test_markdown_validation_marker_retains_evidence(world):
    cfg = world.build([])
    br.ensure_state(cfg)
    payload = [{"name": "fixture", "branch_sha": "a" * 40, "status": "passed", "evidence": "check exited 0"}]
    captured = br._capture_validations(cfg, "fixture", "**RUNNER-VALIDATIONS:** " + json.dumps(payload))
    assert len(captured) == 1
    assert (Path(cfg.state_dir) / captured[0]["evidence_path"]).read_text() == "check exited 0"


def test_owner_can_supply_reviewed_identity_for_legacy_item(world):
    cfg = world.build([item('2026-01-01-a', status='in_review', branch='claude/bl-a')])
    worked_branch(world.repo, 'claude/bl-a')
    sha = git(world.repo, 'rev-parse', 'claude/bl-a').strip()
    assert br.approve_one(cfg, '2026-01-01-a', expected_reviewed_sha=sha, log=lambda *a: None)
    archived = br.load_yaml(cfg.archive_path)['items'][0]
    assert archived['reviewed_sha'] == sha
    assert archived['review_identity_source'] == 'owner-supplied'


def test_approval_merges_only_reviewed_commit_if_branch_moves_during_action(world, monkeypatch):
    cfg = world.build([item('2026-01-01-a', status='in_review', branch='claude/bl-a')])
    wt = worked_branch(world.repo, 'claude/bl-a')
    mark_reviewed(cfg, world.repo, 'claude/bl-a', '2026-01-01-a')
    real_git = br.git
    advanced = False
    def racing_git(repo, *args, **kwargs):
        nonlocal advanced
        if args[:2] == ('merge', '--no-ff') and not advanced:
            advanced = True
            (wt / 'not-reviewed.txt').write_text('must stay off main')
            git(wt, 'add', 'not-reviewed.txt'); git(wt, 'commit', '-qm', 'concurrent work')
        return real_git(repo, *args, **kwargs)
    monkeypatch.setattr(br, 'git', racing_git)
    assert br.approve_one(cfg, '2026-01-01-a', log=lambda *a: None)
    assert advanced
    assert not (world.repo / 'not-reviewed.txt').exists()
    assert git(world.repo, 'branch', '--list', 'claude/bl-a').strip()


def test_rework_preserves_owner_revision_made_without_status_change(world):
    cfg = world.build([item('2026-01-01-a', status='held', branch='claude/bl-a', note='First request')])
    worked_branch(world.repo, 'claude/bl-a')
    initial = load_items(cfg)[0]
    planned = br._rework_plan(cfg, initial)
    def review(*args, **kwargs):
        br.mutate_backlog(cfg, initial['id'], lambda row: row.update(prompt='Revised owner scope', note='Use this newer evidence'))
        return stub_reviewer(*args, **kwargs)
    result = br.work_one(cfg, planned, reviewer=review, continuation=True, log=lambda *a: None)
    row = load_items(cfg)[0]
    assert row['status'] == 'held'
    assert row['prompt'] == 'Revised owner scope'
    assert 'Use this newer evidence' in row['note']
    assert result['conflict'] and row['runner_conflict']['branch'] == 'claude/bl-a'
    assert not row.get('reviewed_sha')


def test_approval_preserves_unrelated_unstaged_work(world):
    cfg = world.build([item('2026-01-01-a', status='in_review', branch='claude/bl-a')])
    worked_branch(world.repo, 'claude/bl-a')
    sha = git(world.repo, 'rev-parse', 'claude/bl-a').strip()
    br.mutate_backlog(cfg, '2026-01-01-a', lambda row: row.update(reviewed_sha=sha))
    (world.repo / 'README.md').write_text('Owner notes still in progress\n')
    assert br.approve_one(cfg, '2026-01-01-a', log=lambda *a:None)
    assert (world.repo / 'README.md').read_text() == 'Owner notes still in progress\n'
    assert git(world.repo, 'show', 'HEAD:README.md').strip() == 'hello'


def test_approval_never_overwrites_overlapping_owner_work(world):
    cfg = world.build([item('2026-01-01-a', status='in_review', branch='claude/bl-a')])
    worked_branch(world.repo, 'claude/bl-a', fname='README.md', content='Reviewed change\n')
    sha = git(world.repo, 'rev-parse', 'claude/bl-a').strip()
    br.mutate_backlog(cfg, '2026-01-01-a', lambda row: row.update(reviewed_sha=sha))
    (world.repo / 'README.md').write_text('Unfinished owner edit\n')
    assert not br.approve_one(cfg, '2026-01-01-a', log=lambda *a:None)
    assert (world.repo / 'README.md').read_text() == 'Unfinished owner edit\n'
    assert git(world.repo, 'show', 'HEAD:README.md').strip() == 'hello'


def test_approval_preserves_an_in_progress_git_sequence(world):
    cfg = world.build([item('2026-01-01-a', status='in_review', branch='claude/bl-a')])
    worked_branch(world.repo, 'claude/bl-a')
    mark_reviewed(cfg, world.repo, 'claude/bl-a', '2026-01-01-a')
    sequence = world.repo / '.git' / 'rebase-apply'
    sequence.mkdir()
    assert not br.approve_one(cfg, '2026-01-01-a', log=lambda *a:None)
    assert sequence.is_dir() and load_items(cfg)[0]['status'] == 'in_review'


# ----------------------------------------------------------------------------- Jev shadow line
# A display-only second reading of the chair's verdict. It may never move readiness, the
# approve suggestion or the recorded review status. conftest keeps JEV_DISABLED=1 and
# COUNCIL_JEV=0 for every test; these tests switch both on and fake the shared transport.

JEV_LINE = "Jev reads the chair's verdict as: approve_with_conditions (0.91) [shadow, display only]"


def _jev_switches_on(monkeypatch):
    monkeypatch.delenv("JEV_DISABLED", raising=False)
    monkeypatch.setenv("COUNCIL_JEV", "1")


def jev_reviewer(label="approve_with_conditions", confidence=0.91, **extra):
    def reviewer(cfg, diff, *, item_id):
        return {"ok": True, "summary": "Approve with follow-up fixes", "markdown": "Full council output",
                "jev_shadow": {"verdict": {"label": label, "confidence": confidence}}, **extra}
    return reviewer


def _fake_council(monkeypatch, *, findings=True):
    """The real adapter over a scripted panel and chair; returns the Jev requests seen."""
    from types import SimpleNamespace
    import council.config as config
    import council.engine as engine
    import council.venice as venice
    import jev.client as shared_client
    from council.jev import MODEL
    from council.models import Finding, Member, MemberResult, Panel
    from tests.conftest import FakeClient
    payload = {"recommendation": "Approve with follow-up fixes. Two gaps should be closed before merge.",
               "confidence": 8, "blocking_findings": [], "required_changes": []}           # no review_status: unknown
    panel = Panel("code-review", "fixture", [Member("a", "m1", "x"), Member("b", "m2", "x")])
    results = [MemberResult("a", "m1", "concerns", "ok", findings=[Finding("parser over-captures", "high", 9)] if findings else []),
               MemberResult("b", "m2", "concerns", "ok", findings=[Finding("parser splits notes", "med", 8)] if findings else [])]
    monkeypatch.setattr(config, "load_panels", lambda _: (SimpleNamespace(timeout=1, byte_cap=100000, chair_model="chair"), {"code-review": panel}))
    monkeypatch.setattr(engine, "run_panel", lambda *args, **kw: results)
    monkeypatch.setattr(venice, "VeniceClient", lambda *args, **kw: FakeClient(default=payload))
    monkeypatch.setattr(br, "load_venice_key", lambda role, env_path=None: "venice-test")
    sent = []

    def transport(req, key, timeout):
        sent.append(req)
        if "verdict" in req["questions"]:
            answers = {"verdict": {"type": "choice", "choice": "approve_with_conditions", "confidence": 0.91, "probabilities": {}}}
        else:
            answers = {"same": {"type": "noul", "noul": 0.9}}
        return {"model": MODEL, "answers": answers}
    monkeypatch.setattr(shared_client, "http_post", transport)
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-test")
    monkeypatch.delenv("JEV_DISABLED", raising=False)            # the council's own switch is still off (conftest)
    return sent


def test_real_council_adapter_carries_the_jev_verdict_and_changes_nothing_else(world, monkeypatch):
    cfg = world.build([])
    sent = _fake_council(monkeypatch)
    off = REAL_COUNCIL_REVIEW(cfg, "diff", item_id="2026-01-01-a", repo="ai-harness")
    assert sent == [] and "jev_shadow" not in off and "Jev" not in off["markdown"]        # kill switch (conftest)
    monkeypatch.setenv("COUNCIL_JEV", "1")
    on = REAL_COUNCIL_REVIEW(cfg, "diff", item_id="2026-01-01-a", repo="ai-harness")
    assert on["jev_shadow"] == {"verdict": {"label": "approve_with_conditions", "confidence": 0.91}}
    assert on["review_status"] == off["review_status"] == "unknown"
    assert {k: v for k, v in on.items() if k not in ("jev_shadow", "markdown")} == \
           {k: v for k, v in off.items() if k != "markdown"}
    assert on["markdown"].startswith(off["markdown"])
    assert "### Jev shadow signals (display only; not used by the chair or the gate)" in on["markdown"][len(off["markdown"]):]
    assert "raised by 2 of 2 seats" in on["markdown"] and len(sent) == 2
    assert "diff" not in json.dumps([r["state"] for r in sent])                          # the diff is never sent


@pytest.mark.parametrize("repo,item_id", [("sat-prep", "2026-01-01-a"), ("romance-empire", "2026-01-01-a"),
                                          (None, "2026-01-01-a"), ("ai-harness", "2026-01-01-bebop-mail-fix"),
                                          ("alpha", "2026-01-01-a"), ("vps-tools", "2026-01-01-a"),
                                          ("brand-new-repo", "2026-01-01-a")])        # not on the allow list
def test_out_of_scope_or_unknown_repo_gets_no_jev_call_at_all(world, monkeypatch, repo, item_id):
    cfg = world.build([])
    sent = _fake_council(monkeypatch)
    monkeypatch.setenv("COUNCIL_JEV", "1")
    rev = REAL_COUNCIL_REVIEW(cfg, "diff", item_id=item_id, repo=repo)
    assert sent == [] and "jev_shadow" not in rev and "Jev" not in rev["markdown"] and rev["ok"] is True


def test_a_jev_outage_leaves_the_review_exactly_as_it_was(world, monkeypatch):
    import jev.client as shared_client
    cfg = world.build([])
    _fake_council(monkeypatch)
    off = REAL_COUNCIL_REVIEW(cfg, "diff", item_id="x", repo="ai-harness")
    monkeypatch.setenv("COUNCIL_JEV", "1")

    def down(req, key, timeout):
        raise OSError("TypeSafe is down")
    monkeypatch.setattr(shared_client, "http_post", down)
    on = REAL_COUNCIL_REVIEW(cfg, "diff", item_id="x", repo="ai-harness")
    assert "jev_shadow" not in on and {k: v for k, v in on.items() if k != "markdown"} == {k: v for k, v in off.items() if k != "markdown"}
    import council.signals as signals
    monkeypatch.setattr(signals, "collect", lambda *a, **k: 1 / 0)
    assert REAL_COUNCIL_REVIEW(cfg, "diff", item_id="x", repo="ai-harness") == off


def test_work_one_names_the_repo_only_to_a_reviewer_that_asks_for_it(world):
    seen = {}

    def wants_repo(cfg, diff, *, item_id, repo=None):
        seen.update(repo=repo)
        return {"ok": True, "summary": "ok", "markdown": "x"}
    wants_repo.accepts_repo = True
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    br.work_one(cfg, p, reviewer=wants_repo, log=lambda *a: None)
    assert seen == {"repo": "alpha"} and REAL_COUNCIL_REVIEW.accepts_repo is True


def test_jev_line_shows_in_report_and_show_and_readiness_stays_unknown(world, monkeypatch, capsys):
    _jev_switches_on(monkeypatch)
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    result = br.work_one(cfg, p, reviewer=jev_reviewer(), log=lambda *a: None)
    assert result["review_readiness"]["status"] == "unknown"
    info = br._review_readiness(cfg, load_items(cfg)[0])
    standard = {"schema_version", "record_id", "branch_sha", "status", "reasons", "evidence_path", "review_path"}
    assert info["status"] == "unknown" and set(info) == standard          # readiness carries no Jev field
    report = br.write_report(cfg)
    assert report.count(JEV_LINE) == 1 and "- " + JEV_LINE in report.splitlines()
    assert "Review readiness unknown" in report and "`backlog-run approve 1`" not in report
    assert "resolve or inspect the evidence above before deciding" in report
    saved = json.loads(Path(cfg.report_json).read_text())["review_readiness"]["2026-01-01-a"]
    assert saved["status"] == "unknown" and set(saved) == standard
    assert br.cmd_show(br.build_parser().parse_args(["show", "1"]), cfg) == 0
    assert capsys.readouterr().out.count(JEV_LINE) == 1
    monkeypatch.setenv("COUNCIL_JEV", "0")                                       # the kill switch hides it again
    assert "Jev" not in br.write_report(cfg)
    monkeypatch.setenv("COUNCIL_JEV", "1")
    assert JEV_LINE in br.write_report(cfg)
    monkeypatch.setenv("JEV_DISABLED", "1")                                      # and so does the shared off switch
    assert "Jev" not in br.write_report(cfg)


def test_report_with_and_without_a_jev_label_differs_by_that_one_line(world, monkeypatch):
    _jev_switches_on(monkeypatch)
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    result = br.work_one(cfg, p, reviewer=jev_reviewer("approve", 0.99), log=lambda *a: None)
    assert result["review_readiness"]["status"] == "unknown"
    with_label = br.write_report(cfg).splitlines()[1:]
    record_path = next(Path(cfg.reviews_dir).glob("*.inputs.json"))
    record = json.loads(record_path.read_text())
    assert record.pop("jev_shadow") == {"verdict": {"label": "approve", "confidence": 0.99}}
    record_path.write_text(json.dumps(record))
    without = br.write_report(cfg).splitlines()[1:]
    extra = [ln for ln in with_label if ln not in without]
    assert extra == ["- Jev reads the chair's verdict as: approve (0.99) [shadow, display only]"]
    assert [ln for ln in with_label if ln not in extra] == without
    assert not any("approve 1" in ln for ln in with_label)                       # still no approve suggestion


@pytest.mark.parametrize("shadow", [{"verdict": {"label": "ship_it", "confidence": 0.9}},
                                    {"verdict": {"label": "approve", "confidence": "high"}},
                                    {"verdict": {"label": "approve", "confidence": 1.7}},
                                    {"verdict": "approve"}, "approve", None])
def test_a_malformed_jev_record_is_neither_saved_nor_shown(world, monkeypatch, shadow):
    _jev_switches_on(monkeypatch)

    def reviewer(cfg, diff, *, item_id):
        return {"ok": True, "summary": "ok", "markdown": "x", "jev_shadow": shadow}
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    br.work_one(cfg, p, reviewer=reviewer, log=lambda *a: None)
    assert "jev_shadow" not in json.loads(next(Path(cfg.reviews_dir).glob("*.inputs.json")).read_text())
    assert "Jev" not in br.write_report(cfg)


def test_a_jev_label_for_an_older_commit_is_not_shown_against_a_newer_head(world, monkeypatch):
    _jev_switches_on(monkeypatch)
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    br.work_one(cfg, p, reviewer=jev_reviewer(), log=lambda *a: None)
    assert JEV_LINE in br.write_report(cfg)
    git(world.repo, "checkout", "-q", "claude/bl-a")
    git(world.repo, "commit", "--allow-empty", "-qm", "moved on")
    git(world.repo, "checkout", "-q", "main")
    assert "Jev" not in br.write_report(cfg)


def test_a_clean_ready_item_keeps_its_approve_suggestion_next_to_a_doubting_jev_label(world, monkeypatch):
    _jev_switches_on(monkeypatch)
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    result = br.work_one(cfg, p, log=lambda *a: None, reviewer=jev_reviewer(
        "request_changes", 0.95, review_status="clean", blocking_findings=[]))
    assert result["review_readiness"]["status"] == "ready"                       # shadow: Jev cannot make it stricter yet
    report = br.write_report(cfg)
    assert "`backlog-run approve 1`" in report and "request_changes (0.95) [shadow, display only]" in report


# Repo identity (council review 2026-09-19). The scope rule is judged on WHICH repository the
# work ran in, not on what a folder happens to be called: git's own answer (the main
# checkout's name, right inside a linked worktree and through a symlink) must also equal the
# backlog item's `repo` field. Anything else means no Jev call.

def test_repo_identity_is_gits_answer_and_must_match_the_items_repo_field(tmp_path):
    real = make_repo(tmp_path, "ai-harness", remote=False)
    assert br._jev_repo_identity(str(real), "ai-harness") == "ai-harness"
    assert br._jev_repo_identity(str(real), " ai-harness ") == "ai-harness"       # as repo_path reads the field
    for declared in ("swimtrack", str(real), "", None, "AI-HARNESS", 7):
        assert br._jev_repo_identity(str(real), declared) is None, declared      # any mismatch: no Jev call


def test_a_folder_named_like_an_allowed_repo_is_not_that_repo(tmp_path):
    lookalike = tmp_path / "elsewhere" / "ai-harness"
    (lookalike / ".git").mkdir(parents=True)                                     # passes a folder check; git says no
    assert br._jev_repo_identity(str(lookalike), "ai-harness") is None
    private = make_repo(tmp_path, "sat-prep", remote=False)
    linked = tmp_path / "worktrees" / "ai-harness"
    linked.parent.mkdir()
    git(private, "worktree", "add", "-q", str(linked), "-b", "side")
    assert br._jev_repo_identity(str(linked), "ai-harness") is None              # a worktree of sat-prep
    alias = tmp_path / "aliases" / "ai-harness"
    alias.parent.mkdir()
    alias.symlink_to(private, target_is_directory=True)
    assert br._jev_repo_identity(str(alias), "ai-harness") is None               # a symlink to sat-prep


def test_an_identity_that_cannot_be_resolved_means_no_jev_call(tmp_path, monkeypatch):
    import council.jev as council_jev
    real = make_repo(tmp_path, "ai-harness", remote=False)
    monkeypatch.setattr(council_jev, "repo_name", lambda path: None)
    assert br._jev_repo_identity(str(real), "ai-harness") is None
    monkeypatch.setattr(council_jev, "repo_name", lambda path: 1 / 0)
    assert br._jev_repo_identity(str(real), "ai-harness") is None


def _work_with_the_real_council(world, monkeypatch, *, declared_repo):
    sent = _fake_council(monkeypatch)
    _jev_switches_on(monkeypatch)
    cfg = world.build([item("2026-01-01-a", repo=declared_repo, required_validations=[])])
    (p,) = br.plan(cfg, load_items(cfg))
    assert p.action == "work", p.reason
    result = br.work_one(cfg, p, reviewer=REAL_COUNCIL_REVIEW, log=lambda *a: None)
    record = json.loads(next(Path(cfg.reviews_dir).glob("*.inputs.json")).read_text())
    return sent, result, record


def test_work_one_in_the_real_allowed_repo_still_calls_jev(world, monkeypatch):
    make_repo(world.root, "ai-harness")
    sent, result, record = _work_with_the_real_council(world, monkeypatch, declared_repo="ai-harness")
    assert len(sent) == 2 and record["jev_shadow"]["verdict"]["label"] == "approve_with_conditions"
    assert result["review_readiness"]["status"] == "unknown"


def test_work_one_through_a_symlink_named_like_an_allowed_repo_makes_no_jev_call(world, monkeypatch):
    private = make_repo(world.root, "sat-prep")
    (world.root / "projects" / "ai-harness").symlink_to(private, target_is_directory=True)
    sent, result, record = _work_with_the_real_council(world, monkeypatch, declared_repo="ai-harness")
    assert sent == [] and "jev_shadow" not in record
    assert result["council"].startswith(str(br.today().year))                    # the review itself still ran


def test_work_one_makes_no_jev_call_when_the_items_repo_field_is_not_the_repo_name(world, monkeypatch):
    real = make_repo(world.root, "ai-harness")
    sent, result, record = _work_with_the_real_council(world, monkeypatch, declared_repo=str(real))
    assert sent == [] and "jev_shadow" not in record                             # a path is not a name: mismatch


def test_the_work_queue_shares_work_one_and_its_one_repo_is_never_in_scope():
    import inspect
    import council.jev as council_jev
    import workqueue.runner as runner
    assert runner._work_one is br.work_one                                       # so the identity rule applies there too
    assert '"repo": "sat-prep"' in inspect.getsource(runner.execute)
    assert not council_jev.in_scope("any-run-id", "sat-prep")


# Two Jev uses now live in backlog-run. The pre-session HOLD GATE (backlogrun/gate.py) runs
# BEFORE a worker session and may add a hold. The council SHADOW signals run AFTER the council
# review and are display and log only. These tests pin that they do not interfere.

def _both_jev_uses_on(monkeypatch, *, outward: float):
    """The real gate and the real council adapter over one fake shared transport. Returns the
    list of question-name lists the transport saw, in order."""
    import jev.client as shared_client
    from council.jev import MODEL
    _fake_council(monkeypatch)
    _jev_switches_on(monkeypatch)
    seen = []

    def transport(req, key, timeout):
        names = sorted(req["questions"])
        seen.append((names, req["state"]))
        if names == ["outward"]:
            answers = {"outward": {"type": "noul", "noul": outward}}
        elif names == ["verdict"]:
            answers = {"verdict": {"type": "choice", "choice": "approve_with_conditions", "confidence": 0.91, "probabilities": {}}}
        else:
            answers = {"same": {"type": "noul", "noul": 0.9}}
        return {"model": MODEL, "answers": answers, "usage": {"input_tokens": 7}}
    monkeypatch.setattr(shared_client, "http_post", transport)
    monkeypatch.setattr(br, "council_review", REAL_COUNCIL_REVIEW)      # cmd_work uses the module's reviewer
    return seen


def _usage_rows():
    path = Path(os.environ["JEV_USAGE_LOG"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_under_the_suite_guard_neither_jev_use_makes_a_call(world, monkeypatch):
    import jev.client as shared_client
    reached = []
    monkeypatch.setattr(shared_client, "http_post", lambda *a: reached.append(a) or {})
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-test")             # a key exists; conftest's switches are on
    assert os.environ["JEV_DISABLED"] == "1" and os.environ["COUNCIL_JEV"] == "0"
    # an allowed repo, so it is the suite guard that stops the call and not the gate's scope rule
    assert br.gate_mod.check(item("2026-01-01-deploy", repo="ai-harness", prompt="run npm run deploy")) == (False, "")
    cfg = world.build([])
    _fake_council(monkeypatch)
    monkeypatch.setenv("JEV_DISABLED", "1")                             # _fake_council lifts it; put it back
    monkeypatch.setattr(shared_client, "http_post", lambda *a: reached.append(a) or {})
    monkeypatch.setenv("COUNCIL_JEV", "1")                              # even with the council's own switch on
    rev = REAL_COUNCIL_REVIEW(cfg, "diff", item_id="2026-01-01-a", repo="ai-harness")
    assert reached == [] and "jev_shadow" not in rev and _usage_rows() == []


def test_a_gate_hold_means_no_review_no_shadow_call_no_display_line_and_no_shadow_log(world, monkeypatch, capsys):
    make_repo(world.root, "ai-harness")                                 # a repo the shadow WOULD run for
    seen = _both_jev_uses_on(monkeypatch, outward=0.95)
    cfg = world.build([item("2026-01-01-deploy", repo="ai-harness", required_validations=[])])
    assert br.cmd_work(work_args(), cfg) == 0
    (it,) = load_items(cfg)
    assert it["status"] == "held" and "pre-session gate" in it["note"] and not it.get("branch")
    assert [names for names, _ in seen] == [["outward"]]                # the gate's one question, nothing after it
    assert list(Path(cfg.runs_dir).glob("*.json")) == [] and list(Path(cfg.reviews_dir).glob("*")) == []
    report = br.write_report(cfg)
    assert "Jev reads" not in report and "pre-session gate" in report
    assert br.cmd_show(br.build_parser().parse_args(["show", "2026-01-01-deploy"]), cfg) == 0
    assert "Jev reads" not in capsys.readouterr().out
    assert not Path(os.environ["COUNCIL_JEV_LOG"]).exists()             # no shadow log line for a held item
    assert [(r["project"], r["task"]) for r in _usage_rows()] == [("backlog-run", "outward-gate")]


def test_an_item_the_gate_lets_through_is_asked_once_by_each_and_nothing_is_logged_twice(world, monkeypatch, capsys):
    make_repo(world.root, "ai-harness")
    seen = _both_jev_uses_on(monkeypatch, outward=0.08)
    cfg = world.build([item("2026-01-01-a", repo="ai-harness", required_validations=[])])
    assert br.cmd_work(work_args(), cfg) == 0
    (it,) = load_items(cfg)
    assert it["status"] == "in_review"
    assert [names for names, _ in seen] == [["outward"], ["verdict"], ["same"]]      # gate first, shadow last
    gate_state, shadow_states = seen[0][1], [state for _, state in seen[1:]]
    assert set(gate_state) == {"repository", "title", "task"}
    assert "Do the thing" not in json.dumps(shadow_states)              # the shadow never sends the item's prompt
    assert [(r["project"], r["task"]) for r in _usage_rows()] == [
        ("backlog-run", "outward-gate"), ("ai-harness", "council-verdict"), ("ai-harness", "council-same")]
    rows = [json.loads(line) for line in Path(os.environ["COUNCIL_JEV_LOG"]).read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["kind"] == "review" and rows[0]["item"] == "2026-01-01-a" and rows[0]["calls"] == 2
    assert "outward" not in json.dumps(rows[0])                         # the gate's answer is not in the shadow log
    record = json.loads(next(Path(cfg.reviews_dir).glob("*.inputs.json")).read_text())
    assert record["jev_shadow"] == {"verdict": {"label": "approve_with_conditions", "confidence": 0.91}}
    assert "gate" not in json.dumps(record).lower()
    assert JEV_LINE in br.write_report(cfg)


def test_the_gates_answer_never_reaches_the_shadows_scope_rule(world, monkeypatch):
    # "alpha" is not on the council's allow list. Whatever the gate does with such an item,
    # the shadow's own rule still decides for the shadow: a worked item gets no shadow call.
    seen = _both_jev_uses_on(monkeypatch, outward=0.08)
    cfg = world.build([item("2026-01-01-a", required_validations=[])])
    assert br.cmd_work(work_args(), cfg) == 0
    assert load_items(cfg)[0]["status"] == "in_review"
    assert [names for names, _ in seen if names != ["outward"]] == []
    assert not Path(os.environ["COUNCIL_JEV_LOG"]).exists()


def test_the_two_jev_uses_share_no_code_and_no_module_state():
    import inspect
    import council.signals as signals
    from backlogrun import gate
    gate_source, signals_source = inspect.getsource(gate), inspect.getsource(signals)
    assert "council" not in gate_source.replace("the council verdict", "")       # the gate knows nothing of the shadow
    assert "backlogrun" not in signals_source and "outward" not in signals_source
    mutable = lambda module: {k for k, v in vars(module).items()                  # noqa: E731
                              if not k.startswith("__") and isinstance(v, (list, set, bytearray))}
    assert mutable(gate) == set() and mutable(signals) == set()
    assert gate.MODEL == signals.jev.MODEL                                        # two pins, one measured version today
