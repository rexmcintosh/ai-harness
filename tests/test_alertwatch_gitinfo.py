import dataclasses
import subprocess

from alertwatch import gitinfo
from alertwatch.config import load_config


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_history_without_contents(tmp_path, monkeypatch, capsys):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": "/dev/null"}.items():
        monkeypatch.setenv(k, v)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / ".env").write_text("TOKEN=HISTORICAL-SECRET\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "oops")
    git(repo, "rm", "-q", ".env"); git(repo, "commit", "-qm", "remove it")
    cfg = dataclasses.replace(load_config(env={}), projects_dir=tmp_path, repos=("repo",))
    assert gitinfo.main(["repo"], cfg=cfg) == 0
    out = capsys.readouterr().out
    assert "remove it" in out and ".env" in out
    assert "HISTORICAL-SECRET" not in out


def test_only_listed_repos_and_bounded_count(tmp_path, capsys):
    cfg = dataclasses.replace(load_config(env={}), projects_dir=tmp_path, repos=("repo",))
    assert gitinfo.main(["other"], cfg=cfg) == 2
    assert gitinfo.main(["repo", "--count", "500"], cfg=cfg) == 2
