import subprocess
from pathlib import Path
from types import SimpleNamespace
from diem.config import DiemConfig
from diem.queue import new_item
from diem.runners import run_item

NOW = "2026-07-03T21:00:00"

class FakeRun:
    """Records subprocess calls; scripted (returncode, stdout) per call."""
    def __init__(self, results=None, raise_timeout=False):
        self.calls = []
        self.results = list(results or [])
        self.raise_timeout = raise_timeout
    def __call__(self, argv, **kw):
        self.calls.append({"argv": argv, **kw})
        if self.raise_timeout:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        rc, out = self.results.pop(0) if self.results else (0, "ok-output")
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")

def _cfg(tmp_path):
    return DiemConfig(daily_diem=100.0, repos=[],
                      state_dir=tmp_path / "state", outputs_dir=tmp_path / "out",
                      loom_repo=tmp_path / "loomrepo",
                      loom_cmd=["python", "-m", "loom.cli", "backfill"],
                      cmd_whitelist={"teasers": {"repo": str(tmp_path / "re"),
                                                 "argv": ["python", "make.py"]}})

def test_ask_invokes_council_and_saves_output(tmp_path):
    fr = FakeRun()
    it = new_item("ask", {"question": "q?", "panel": "decision"}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {"VENICE_API_KEY": "k"},
                   deadline_epoch=10_000.0, run=fr, clock=lambda: 0.0)
    assert res.ok
    assert fr.calls[0]["argv"] == ["council", "ask", "q?", "--panel", "decision",
                                   "--format", "md"]
    assert fr.calls[0]["env"]["VENICE_API_KEY"] == "k"
    assert Path(res.output_path).read_text() == "ok-output"

def test_review_diff_runs_in_repo(tmp_path):
    fr = FakeRun()
    it = new_item("review", {"repo": "/r/swim", "diff": True}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok and fr.calls[0]["cwd"] == "/r/swim"
    assert fr.calls[0]["argv"] == ["council", "review", "--diff", "--format", "md"]

def test_review_range_pipes_git_diff_to_stdin(tmp_path):
    # call 0: cat-file base-exists check (ok) — baseline intact, no heal
    fr = FakeRun(results=[(0, ""), (0, "THE DIFF"), (0, "verdict")])
    it = new_item("review", {"repo": "/r/swim", "range": "a..b", "head": "b"},
                  created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok and res.note is None
    assert fr.calls[0]["argv"] == ["git", "-C", "/r/swim", "cat-file", "-e",
                                   "a^{commit}"]
    assert fr.calls[1]["argv"] == ["git", "-C", "/r/swim", "diff", "a..b"]
    assert fr.calls[2]["argv"] == ["council", "review", "-", "--format", "md"]
    assert fr.calls[2]["input"] == "THE DIFF"

def test_review_range_empty_diff_short_circuits(tmp_path):
    fr = FakeRun(results=[(0, ""), (0, "")])  # cat-file ok, empty diff
    it = new_item("review", {"repo": "/r/swim", "range": "a..b", "head": "b"},
                  created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok and len(fr.calls) == 2  # council never called on empty diff

def test_review_range_vanished_base_heals_to_merge_base(tmp_path):
    """A base SHA rewritten out of history (rebase/squash/force-push) must not
    produce the perpetual Invalid-revision-range failure: the runner re-anchors
    the diff to the merge-base with the default branch and reviews that."""
    fr = FakeRun(results=[
        (1, ""),                              # cat-file base: GONE
        (0, ""),                              # rev-parse HEAD: repo healthy
        (0, ""),                              # cat-file head: exists
        (0, "refs/remotes/origin/main\n"),    # symbolic-ref origin/HEAD
        (0, "mbase\n"),                       # merge-base
        (0, "HEALED DIFF"),                   # git diff mbase..b
        (0, "verdict"),                       # council review
    ])
    it = new_item("review", {"repo": "/r/swim", "range": "gone..b", "head": "b"},
                  created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok
    assert res.note and "healed" in res.note and "gone" in res.note
    assert fr.calls[4]["argv"] == ["git", "-C", "/r/swim", "merge-base",
                                   "refs/remotes/origin/main", "b"]
    assert fr.calls[5]["argv"] == ["git", "-C", "/r/swim", "diff", "mbase..b"]
    assert fr.calls[6]["input"] == "HEALED DIFF"

def test_review_range_vanished_base_no_default_branch_resets_to_head(tmp_path):
    """No resolvable default branch: heal degrades to head..head — an empty
    diff, returned ok so the drain advances the baseline to head."""
    fr = FakeRun(results=[
        (1, ""),   # cat-file base: GONE
        (0, ""),   # rev-parse HEAD: repo healthy
        (0, ""),   # cat-file head: exists
        (1, ""),   # symbolic-ref origin/HEAD: none
        (1, ""),   # merge-base refs/heads/main: fails
        (1, ""),   # merge-base refs/heads/master: fails
        (0, ""),   # git diff b..b: empty
    ])
    it = new_item("review", {"repo": "/r/swim", "range": "gone..b", "head": "b"},
                  created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok and res.note and "healed" in res.note
    assert fr.calls[6]["argv"] == ["git", "-C", "/r/swim", "diff", "b..b"]
    assert len(fr.calls) == 7  # nothing to review — council never called

def test_review_range_unreachable_repo_stays_fail_closed(tmp_path):
    fr = FakeRun(results=[
        (1, ""),   # cat-file base: fails
        (1, ""),   # rev-parse HEAD: fails — repo is genuinely broken/gone
    ])
    it = new_item("review", {"repo": "/r/gone", "range": "a..b", "head": "b"},
                  created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert not res.ok and "unreachable" in res.error
    assert len(fr.calls) == 2  # never attempted a diff or a heal

def test_review_range_vanished_head_fails_for_rediscovery(tmp_path):
    """Head rewritten too: fail the item (an ok would record the dead head as
    the new baseline); the next discovery re-mints from the current HEAD and
    the base-side heal then applies."""
    fr = FakeRun(results=[
        (1, ""),   # cat-file base: GONE
        (0, ""),   # rev-parse HEAD: repo healthy
        (1, ""),   # cat-file head: GONE too
    ])
    it = new_item("review", {"repo": "/r/swim", "range": "a..b", "head": "b"},
                  created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert not res.ok and "re-discover" in res.error
    assert len(fr.calls) == 3

def test_images_payload_command_ignored(tmp_path):
    """Payload-supplied command must never be honored — argv comes solely
    from the target repo's standing order, even when a (malicious) payload
    command is present."""
    repo = tmp_path / "re"; (repo / ".diem").mkdir(parents=True)
    (repo / ".diem" / "standing-order.json").write_text(
        '{"target": 9, "candidates_dir": "c", "command": ["python", "make.py"]}')
    fr = FakeRun()
    it = new_item("images", {"repo": str(repo), "count": 5,
                             "command": ["evil"]}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok
    assert fr.calls[0]["argv"] == ["python", "make.py", "--count", "5"]
    assert fr.calls[0]["cwd"] == str(repo)

def test_backfill_uses_loom_cmd(tmp_path):
    fr = FakeRun()
    it = new_item("backfill", {"max_targets": 2}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok
    assert fr.calls[0]["argv"] == ["python", "-m", "loom.cli", "backfill",
                                   "--max-targets", "2"]

def test_cmd_requires_whitelist(tmp_path):
    fr = FakeRun()
    ok = new_item("cmd", {"name": "teasers"}, created=NOW)
    bad = new_item("cmd", {"name": "rm-rf"}, created=NOW)
    assert run_item(ok, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                    run=fr, clock=lambda: 0.0).ok
    res = run_item(bad, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert not res.ok and "whitelist" in res.error

def test_timeout_and_nonzero_are_failures_not_exceptions(tmp_path):
    it = new_item("ask", {"question": "q", "panel": "decision"}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=FakeRun(raise_timeout=True), clock=lambda: 0.0)
    assert not res.ok and "timeout" in res.error.lower()
    res2 = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                    run=FakeRun(results=[(2, "boom")]), clock=lambda: 0.0)
    assert not res2.ok and "exit 2" in res2.error

def test_images_falls_back_to_standing_order(tmp_path):
    repo = tmp_path / "re"; (repo / ".diem").mkdir(parents=True)
    (repo / ".diem" / "standing-order.json").write_text(
        '{"target": 9, "candidates_dir": "c", "command": ["python", "so.py"]}')
    fr = FakeRun()
    it = new_item("images", {"repo": str(repo), "count": 2}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=fr, clock=lambda: 0.0)
    assert res.ok and fr.calls[0]["argv"] == ["python", "so.py", "--count", "2"]

def test_images_no_command_no_standing_order_fails_cleanly(tmp_path):
    it = new_item("images", {"repo": str(tmp_path / "nowhere"), "count": 2}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=FakeRun(), clock=lambda: 0.0)
    assert not res.ok and res.error == "images item has no command and no standing order"

def test_images_standing_order_without_command_fails_cleanly(tmp_path):
    repo = tmp_path / "re"; (repo / ".diem").mkdir(parents=True)
    (repo / ".diem" / "standing-order.json").write_text('{"target": 9}')
    it = new_item("images", {"repo": str(repo), "count": 2}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=FakeRun(), clock=lambda: 0.0)
    assert not res.ok and res.error == "images item has no command and no standing order"

def test_images_string_command_rejected(tmp_path):
    it = new_item("images", {"repo": "/r", "count": 2, "command": "python x.py"}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=FakeRun(), clock=lambda: 0.0)
    assert not res.ok and "no command" in res.error

def test_images_non_dict_standing_order_fails_cleanly(tmp_path):
    repo = tmp_path / "re"; (repo / ".diem").mkdir(parents=True)
    (repo / ".diem" / "standing-order.json").write_text('["not", "a", "dict"]')
    it = new_item("images", {"repo": str(repo), "count": 2}, created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0,
                   run=FakeRun(), clock=lambda: 0.0)
    assert not res.ok and res.error == "images item has no command and no standing order"

def test_review_range_heals_against_real_git_repo(tmp_path):
    """End-to-end with real git: a recorded base SHA that no longer exists in
    the repo (rewritten history) yields a healed ok result, not the
    Invalid-revision-range failure."""
    repo = tmp_path / "repo"
    repo.mkdir()
    def g(*args):
        return subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, check=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (repo / "f.txt").write_text("one\n")
    g("add", "f.txt")
    g("commit", "-qm", "c1")
    head = g("rev-parse", "HEAD").stdout.strip()
    vanished = "0" * 40  # a SHA that never existed here — as after a rebase
    it = new_item("review", {"repo": str(repo),
                             "range": f"{vanished}..{head}", "head": head},
                  created=NOW)
    res = run_item(it, _cfg(tmp_path), {}, deadline_epoch=10_000.0)
    assert res.ok
    assert res.note and "healed" in res.note and vanished[:12] in res.note
