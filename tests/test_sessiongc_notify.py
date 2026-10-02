"""Tests for session-gc Telegram notifications."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest


def _session_gc_args(sender=None, notify=True, notify_strict=False):
    return SimpleNamespace(
        apply=False,
        delete_tier_b=False,
        repo=None,
        notify=notify,
        notify_strict=notify_strict,
        _sender=sender,
    )


def _patch_session_gc_sweep(monkeypatch, tmp_path, branches):
    from sessiongc import cli

    monkeypatch.setattr(cli, "RunLock", lambda: nullcontext())
    monkeypatch.setattr(cli, "ensure_state", lambda: None)
    monkeypatch.setattr(cli, "REPORT_PATH", str(tmp_path / "report.md"))
    monkeypatch.setattr(cli, "discover_repos",
                        lambda: ["/projects/example"] if branches else [])
    monkeypatch.setattr(cli, "parse_worktrees", lambda repo: [])
    monkeypatch.setattr(cli, "default_branch_ref", lambda repo: "main")
    monkeypatch.setattr(cli, "claude_branches", lambda repo: branches)
    monkeypatch.setattr(cli, "branch_age_seconds",
                        lambda repo, branch: cli.GRACE_SECONDS + 1)
    monkeypatch.setattr(cli, "classify", lambda repo, branch, base: "C")

    def fake_git(repo, *args, **kwargs):
        if args[:1] == ("rev-parse",):
            return "0123456789abcdef\n"
        if args[:3] == ("show", "-s", "--format=%s"):
            return f"work from {args[3][:12]}\n"
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(cli, "git", fake_git)
    return cli


def test_send_telegram_prefers_configured_sender(monkeypatch):
    from sessiongc import cli

    monkeypatch.setenv("SESSION_GC_TG_SEND", "/configured/tg-send")
    monkeypatch.setattr(
        cli.shutil, "which",
        lambda name: (_ for _ in ()).throw(
            AssertionError("PATH lookup should not run when env is configured")))
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli._send_telegram("chat", "message") is True
    assert calls[0][0] == ["/configured/tg-send", "chat", "-"]
    assert calls[0][1]["input"] == "message"


def test_send_telegram_uses_path_sender(monkeypatch):
    from sessiongc import cli

    monkeypatch.delenv("SESSION_GC_TG_SEND", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/path/tg-send")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli._send_telegram("chat", "message") is True
    assert commands == [["/path/tg-send", "chat", "-"]]


def test_send_telegram_missing_sender_prints_once_and_skips(monkeypatch, capsys):
    from sessiongc import cli

    monkeypatch.delenv("SESSION_GC_TG_SEND", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("subprocess should not run")))

    assert cli._send_telegram("chat", "message") is False
    assert capsys.readouterr().err.splitlines() == [
        "session-gc: tg-send not found; skipping Telegram notification"
    ]


def test_send_telegram_nonzero_exit_raises(monkeypatch):
    from sessiongc import cli

    monkeypatch.setenv("SESSION_GC_TG_SEND", "/configured/tg-send")
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=7, stderr="synthetic failure\n"))

    with pytest.raises(RuntimeError, match="synthetic failure"):
        cli._send_telegram("chat", "message")


def test_session_gc_sweep_clean_run_does_not_send(monkeypatch, tmp_path):
    cli = _patch_session_gc_sweep(monkeypatch, tmp_path, [])
    sent = []

    rc = cli.cmd_sweep(_session_gc_args(lambda chat_id, message:
                                        sent.append((chat_id, message))))

    assert rc == 0
    assert sent == []


def test_session_gc_sweep_sends_one_message_for_stranded_wip(monkeypatch,
                                                             tmp_path):
    cli = _patch_session_gc_sweep(
        monkeypatch, tmp_path, ["claude/first", "claude/second"])
    monkeypatch.setenv("SESSION_GC_TG_CHAT_ID", "test-chat")
    sent = []

    rc = cli.cmd_sweep(_session_gc_args(lambda chat_id, message:
                                        sent.append((chat_id, message))))

    assert rc == 0
    assert sent == [(
        "test-chat",
        "example claude/first: work from 0123456789ab\n"
        "example claude/second: work from 0123456789ab",
    )]


def test_session_gc_sweep_missing_chat_id_skips_send(monkeypatch, tmp_path,
                                                     capsys):
    cli = _patch_session_gc_sweep(monkeypatch, tmp_path, ["claude/stranded"])
    monkeypatch.delenv("SESSION_GC_TG_CHAT_ID", raising=False)
    sent = []

    rc = cli.cmd_sweep(_session_gc_args(lambda chat_id, message:
                                        sent.append((chat_id, message))))

    assert rc == 0
    assert sent == []
    assert "--notify: SESSION_GC_TG_CHAT_ID not set; skipping send" in (
        capsys.readouterr().err)


def test_session_gc_sweep_subject_failure_uses_short_sha(monkeypatch, tmp_path):
    cli = _patch_session_gc_sweep(monkeypatch, tmp_path, ["claude/stranded"])
    monkeypatch.setenv("SESSION_GC_TG_CHAT_ID", "test-chat")
    sent = []

    def failing_git(repo, *args, **kwargs):
        if args[:1] == ("rev-parse",):
            return "0123456789abcdef\n"
        if args[:3] == ("show", "-s", "--format=%s"):
            raise cli.GitError("synthetic subject failure")
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(cli, "git", failing_git)

    rc = cli.cmd_sweep(_session_gc_args(lambda chat_id, message:
                                        sent.append((chat_id, message))))

    assert rc == 0
    assert sent == [(
        "test-chat",
        "example claude/stranded: 0123456789ab",
    )]


def test_session_gc_sweep_uses_full_repo_path_for_subject(monkeypatch,
                                                          tmp_path):
    from sessiongc import cli

    repos = ["/projects/one/example", "/projects/two/example"]
    monkeypatch.setattr(cli, "RunLock", lambda: nullcontext())
    monkeypatch.setattr(cli, "ensure_state", lambda: None)
    monkeypatch.setattr(cli, "REPORT_PATH", str(tmp_path / "report.md"))
    monkeypatch.setattr(cli, "discover_repos", lambda: repos)
    monkeypatch.setattr(cli, "parse_worktrees", lambda repo: [])
    monkeypatch.setattr(cli, "default_branch_ref", lambda repo: "main")
    monkeypatch.setattr(cli, "claude_branches",
                        lambda repo: [f"claude/{repo.split('/')[-2]}"])
    monkeypatch.setattr(cli, "branch_age_seconds",
                        lambda repo, branch: cli.GRACE_SECONDS + 1)
    monkeypatch.setattr(cli, "classify", lambda repo, branch, base: "C")

    def fake_git(repo, *args, **kwargs):
        if args[:1] == ("rev-parse",):
            return ("1" if "/one/" in repo else "2") * 40 + "\n"
        if args[:3] == ("show", "-s", "--format=%s"):
            return f"subject from {repo}\n"
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(cli, "git", fake_git)
    monkeypatch.setenv("SESSION_GC_TG_CHAT_ID", "test-chat")
    sent = []

    rc = cli.cmd_sweep(_session_gc_args(
        lambda chat_id, message: sent.append((chat_id, message))))

    assert rc == 0
    assert sent == [(
        "test-chat",
        "example claude/one: subject from /projects/one/example\n"
        "example claude/two: subject from /projects/two/example",
    )]


def test_session_gc_sweep_chunks_large_notifications(monkeypatch, tmp_path):
    branches = [f"claude/branch-{i:04d}-{'x' * 40}" for i in range(100)]
    cli = _patch_session_gc_sweep(monkeypatch, tmp_path, branches)
    monkeypatch.setenv("SESSION_GC_TG_CHAT_ID", "test-chat")
    sent = []

    rc = cli.cmd_sweep(_session_gc_args(
        lambda chat_id, message: sent.append((chat_id, message))))

    assert rc == 0
    assert len(sent) > 1
    assert all(chat_id == "test-chat" for chat_id, message in sent)
    assert all(len(message) <= cli.TELEGRAM_MESSAGE_LIMIT
               for chat_id, message in sent)
    combined = "\n".join(message for chat_id, message in sent)
    assert "claude/branch-0000-" in combined
    assert "claude/branch-0099-" in combined


def test_session_gc_sweep_without_notify_skips_subject_lookup(monkeypatch,
                                                              tmp_path):
    cli = _patch_session_gc_sweep(monkeypatch, tmp_path, ["claude/stranded"])
    original_git = cli.git

    def no_show_git(repo, *args, **kwargs):
        if args[:1] == ("show",):
            raise AssertionError("subject lookup should require --notify")
        return original_git(repo, *args, **kwargs)

    monkeypatch.setattr(cli, "git", no_show_git)

    rc = cli.cmd_sweep(_session_gc_args(notify=False))

    assert rc == 0


def test_session_gc_sweep_sender_failure_still_succeeds(monkeypatch, tmp_path,
                                                        capsys):
    cli = _patch_session_gc_sweep(monkeypatch, tmp_path, ["claude/stranded"])
    monkeypatch.setenv("SESSION_GC_TG_CHAT_ID", "test-chat")

    def fail_sender(chat_id, message):
        raise RuntimeError("synthetic send failure")

    rc = cli.cmd_sweep(_session_gc_args(fail_sender))

    assert rc == 0
    assert "Telegram notification failed: synthetic send failure" in (
        capsys.readouterr().err)


def test_session_gc_sweep_strict_sender_failure_returns_nonzero(monkeypatch,
                                                                tmp_path):
    cli = _patch_session_gc_sweep(monkeypatch, tmp_path, ["claude/stranded"])
    monkeypatch.setenv("SESSION_GC_TG_CHAT_ID", "test-chat")

    def fail_sender(chat_id, message):
        raise RuntimeError("synthetic send failure")

    rc = cli.cmd_sweep(_session_gc_args(
        fail_sender, notify=False, notify_strict=True))

    assert rc == 1


def test_session_gc_sweep_sender_timeout_still_succeeds(monkeypatch, tmp_path,
                                                        capsys):
    cli = _patch_session_gc_sweep(monkeypatch, tmp_path, ["claude/stranded"])
    monkeypatch.setenv("SESSION_GC_TG_CHAT_ID", "test-chat")
    monkeypatch.setenv("SESSION_GC_TG_SEND", "/fake/tg-send")

    def timeout_run(command, **kwargs):
        assert command == ["/fake/tg-send", "test-chat", "-"]
        assert kwargs["timeout"] == 30
        raise cli.subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(cli.subprocess, "run", timeout_run)

    rc = cli.cmd_sweep(_session_gc_args())

    assert rc == 0
    assert "Telegram notification failed:" in capsys.readouterr().err
