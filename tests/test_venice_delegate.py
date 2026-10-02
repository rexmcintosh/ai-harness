"""Purpose isolation, bounded requests, and honest completion at the API boundary."""
import importlib.util
import json
from pathlib import Path

import pytest
import requests


@pytest.fixture
def mod():
    path = Path(__file__).parents[1] / "setup/delegate-venice/venice_delegate.py"
    assert path.exists(), "Venice delegate dispatch is not implemented"
    spec = importlib.util.spec_from_file_location("venice_delegate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def response(content="Reviewed", finish="stop", status=200):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps({"choices": [{"finish_reason": finish,
        "message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4}}).encode()
    return r


def test_role_key_never_falls_back_to_generic_or_other_role(mod, tmp_path):
    env = tmp_path / ".env"
    env.write_text("VENICE_API_KEY=generic\nVENICE_CODE_HELPER_KEY=helper\n")
    with pytest.raises(mod.DispatchError, match="VENICE_SECOND_OPINION_KEY"):
        mod.load_key("review", {}, env)
    assert mod.load_key("code", {}, env) == "helper"


def test_key_loader_does_not_execute_shell_and_honors_explicit_empty(mod, tmp_path):
    env = tmp_path / ".env"
    marker = tmp_path / "executed"
    env.write_text(f"OTHER=$(touch {marker})\nexport VENICE_CODE_HELPER_KEY='file-key'\n")
    assert mod.load_key("code", {}, env) == "file-key"
    assert not marker.exists()
    assert mod.load_key("code", {"VENICE_CODE_HELPER_KEY": "env-key"}, env) == "env-key"
    with pytest.raises(mod.DispatchError):
        mod.load_key("code", {"VENICE_CODE_HELPER_KEY": ""}, env)


def test_role_auth_and_bounded_payload_reach_only_venice(mod):
    seen = []
    def post(url, **kw):
        seen.append((url, kw))
        return response()
    assert mod.complete("code", "code-secret", "model-id", "a bounded brief",
                        2000, 30, post=post) == "Reviewed"
    url, kw = seen[0]
    assert url == "https://api.venice.ai/api/v1/chat/completions"
    assert kw["headers"]["Authorization"] == "Bearer code-secret"
    assert kw["json"]["max_completion_tokens"] == 2000
    assert kw["timeout"] == 30
    assert kw["allow_redirects"] is False
    assert "tools" not in kw["json"]


@pytest.mark.parametrize("content,finish,status", [
    ("partial", "length", 200), ("", "stop", 200),
    ("partial", "tool_calls", 200), ("error", "stop", 401),
    ("error", "stop", 429), ("error", "stop", 302),
])
def test_incomplete_or_failed_response_is_not_success_or_retried(mod, content, finish, status):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return response(content, finish, status)
    with pytest.raises(mod.DispatchError):
        mod.complete("review", "secret", "model", "brief", 2000, 30, post=post)
    assert len(calls) == 1


def test_key_cannot_escape_in_prompt_output_or_error(mod):
    seen = []
    def post(*args, **kwargs):
        seen.append(kwargs["json"])
        return response("secret")
    result = mod.complete("code", "secret", "model", "my secret", 2000, 30, post=post)
    assert "secret" not in json.dumps(seen)
    assert "secret" not in result
    def fail(*args, **kwargs):
        raise requests.ConnectionError("secret inside exception")
    with pytest.raises(mod.DispatchError) as error:
        mod.complete("code", "secret", "model", "brief", 2000, 30, post=fail)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("cap,timeout", [(0,30),(-1,30),(40000,30),(2000,0),(2000,301)])
def test_unbounded_calls_rejected_before_network(mod, cap, timeout):
    def post(*a, **kw):
        pytest.fail("invalid budget reached the network")
    with pytest.raises(mod.DispatchError):
        mod.complete("code", "secret", "model", "brief", cap, timeout, post=post)


def test_council_wrapper_overrides_only_child_review_key(mod, monkeypatch, tmp_path):
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/test/bin/council")
    monkeypatch.setenv("VENICE_COUNCIL_KEY", "legacy")
    monkeypatch.setenv("VENICE_API_KEY", "generic")
    monkeypatch.setenv("VENICE_CODE_HELPER_KEY", "helper")
    captured = []
    def run(argv, **kwargs):
        captured.append((argv, kwargs))
        return type("Result", (), {"returncode": 7})()
    assert mod.council_review(["--diff", "--format", "md"], "review-secret", run=run) == 7
    argv, kwargs = captured[0]
    assert argv[1:] == ["review", "--diff", "--format", "md"]
    assert kwargs["env"]["VENICE_COUNCIL_KEY"] == "review-secret"
    assert kwargs["env"]["VENICE_API_KEY"] == "review-secret"
    assert "VENICE_CODE_HELPER_KEY" not in kwargs["env"]
    assert mod.os.environ["VENICE_COUNCIL_KEY"] == "legacy"


def test_sensitive_or_oversized_brief_fails_without_truncation(mod, tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("private")
    with pytest.raises(mod.DispatchError):
        mod.read_brief([str(secret)])
    large = tmp_path / "large.txt"
    large.write_text("x" * 100001)
    with pytest.raises(mod.DispatchError):
        mod.read_brief([str(large)])


def test_empty_file_is_not_mistaken_for_context_due_to_its_filename(mod, tmp_path):
    empty = tmp_path / "brief.md"
    empty.write_text("  \n")
    with pytest.raises(mod.DispatchError, match="nonempty"):
        mod.read_brief([str(empty)])


def test_symlink_cannot_bypass_brief_file_rules(mod, tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("private source")
    link = tmp_path / "brief.md"
    link.symlink_to(target)
    with pytest.raises(mod.DispatchError):
        mod.read_brief([str(link)])


def test_symlinked_parent_cannot_bypass_brief_file_rules(mod, tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    (private / "brief.md").write_text("secret data")
    link = tmp_path / "context"
    link.symlink_to(private, target_is_directory=True)
    with pytest.raises(mod.DispatchError):
        mod.read_brief([str(link / "brief.md")])


def test_cli_honors_explicit_model_and_reports_failure(mod, monkeypatch, tmp_path, capsys):
    brief = tmp_path / "brief.md"
    brief.write_text("Fix this one function")
    monkeypatch.setenv("VENICE_CODE_HELPER_KEY", "role-secret")
    requests_seen = []
    def post(url, **kwargs):
        requests_seen.append(kwargs)
        return response()
    monkeypatch.setattr(mod.requests, "post", post)
    assert mod.main(["code", "--model", "chosen-model", "--file", str(brief)]) == 0
    assert requests_seen[0]["json"]["model"] == "chosen-model"
    assert "Reviewed" in capsys.readouterr().out
    monkeypatch.setenv("VENICE_CODE_HELPER_KEY", "")
    assert mod.main(["code", "--file", str(brief)]) == 2
    assert len(requests_seen) == 1


def test_council_short_help_does_not_require_credentials(mod, monkeypatch):
    monkeypatch.setattr(mod, "load_key", lambda *args: pytest.fail("help loaded credentials"))
    monkeypatch.setattr(mod, "council_review", lambda args, key: 0)
    assert mod.main(["council-review", "-h"]) == 0


@pytest.fixture
def install_env(tmp_path):
    import os
    import sys
    source = Path(__file__).parents[1] / "setup/delegate-venice"
    agreement = tmp_path / ".claude/CLAUDE.md"
    skill = tmp_path / ".codex/skills/delegate/SKILL.md"
    python = tmp_path / ".local/share/pipx/venvs/council/bin/python"
    for path, content in [(agreement, "Owner agreement\n"),
                          (skill, "---\nname: delegate\ndescription: route work\n---\nKeep these routes\n"),
                          (python, "placeholder interpreter")]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    python.unlink()
    python.symlink_to(sys.executable)
    env = {**os.environ, "HOME": str(tmp_path)}
    args = ["python3", str(source / "install.py")]
    return source, agreement, skill, env, args


def test_installer_preserves_owner_text_and_is_idempotent(tmp_path, install_env):
    import subprocess
    source, agreement, skill, env, args = install_env
    plan = subprocess.run(args, env=env, capture_output=True, text=True, check=True)
    assert "Would install" in plan.stdout
    assert agreement.read_text() == "Owner agreement\n"
    subprocess.run([*args, "--apply"], env=env, capture_output=True, text=True, check=True)
    assert agreement.read_text().startswith("Owner agreement\n")
    assert "Keep these routes\n" in skill.read_text()
    assert (tmp_path / ".local/lib/venice-delegate/venice_delegate.py").read_bytes() == (source / "venice_delegate.py").read_bytes()
    again = subprocess.run(args, env=env, capture_output=True, text=True, check=True)
    assert again.stdout.strip() == "No changes"


def test_installer_refuses_concurrent_apply_before_writes(tmp_path, install_env):
    import fcntl
    import subprocess
    _, agreement, _, env, args = install_env
    lock = tmp_path / ".local/state/venice-delegate/install.lock"
    lock.parent.mkdir(parents=True)
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run([*args, "--apply"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "install" in result.stderr and "running" in result.stderr
    assert agreement.read_text() == "Owner agreement\n"


def test_installer_reports_broken_marker_without_partial_writes(install_env):
    import subprocess
    _, agreement, skill, env, args = install_env
    skill.write_text(skill.read_text() + "\n<!-- owner-venice-delegate:start -->\npartial")
    result = subprocess.run([*args, "--apply"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert str(skill) in result.stderr
    assert "Traceback" not in result.stderr
    assert agreement.read_text() == "Owner agreement\n"


def test_installer_repairs_launcher_permissions(tmp_path, install_env):
    import subprocess
    _, _, _, env, args = install_env
    subprocess.run([*args, "--apply"], env=env, check=True, capture_output=True)
    launcher = tmp_path / ".local/bin/venice-delegate"
    launcher.chmod(0o600)
    subprocess.run([*args, "--apply"], env=env, check=True, capture_output=True)
    assert launcher.stat().st_mode & 0o777 == 0o755


def test_installer_rejects_plugin_path_redirect_before_any_write(tmp_path, install_env):
    import subprocess
    _, agreement, _, env, args = install_env
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    target = outside / "skills/delegate/SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("Unrelated owner content\n")
    link = tmp_path / "plugin-link"
    link.symlink_to(outside, target_is_directory=True)
    metadata = tmp_path / ".claude/plugins/installed_plugins.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"plugins": {"delegate@test": [{"installPath": str(link)}]}}))
    result = subprocess.run([*args, "--apply"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert agreement.read_text() == "Owner agreement\n"
    assert target.read_text() == "Unrelated owner content\n"
