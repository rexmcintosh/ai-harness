"""The backup client: same complete() as VeniceClient, run through the `claude` and `codex`
command-line tools on the owner's plans. Subprocess is faked here; the live proof is in
docs/council-backup-mode-2026-11-04.md."""
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from council import backup
from council.backup import BackupClient, BackupConfig, BackupError
from council.jsonparse import loads_lenient

ROUTES = [
    {"match": "openai-gpt-56-sol", "tool": "codex", "model": "gpt-chair", "effort": "high", "timeout": 600},
    {"match": "claude-opus-4-8", "tool": "claude", "model": "claude-chair", "effort": "high", "timeout": 500},
    {"match": "claude-", "tool": "claude", "model": "claude-seat", "effort": "medium", "timeout": 400},
    {"match": "*", "tool": "codex", "model": "gpt-seat", "effort": "medium", "timeout": 300},
]
CATALOG = {"models": [{"slug": "gpt-seat", "tool_mode": "code_mode_only", "multi_agent_version": "v2",
                       "shell_type": "unified_exec", "apply_patch_tool_type": "freeform",
                       "web_search_tool_type": "text_and_image", "supports_search_tool": True,
                       "experimental_supported_tools": ["clock"], "multi_agent_reasoning_effort": "xhigh",
                       "base_instructions": "keep me"},
                      {"slug": "gpt-chair", "tool_mode": "code_mode_only"}]}


def claude_stream(text="{\"ok\": true}", *, is_error=False, tools=(), mcp=(), plugins=(), skills=(),
                  key_source="none", overage=False, subtype="success", limit="allowed"):
    events = [
        {"type": "system", "subtype": "init", "tools": list(tools), "mcp_servers": list(mcp),
         "plugins": list(plugins), "skills": list(skills), "apiKeySource": key_source},
        {"type": "rate_limit_event", "rate_limit_info": {"status": limit, "isUsingOverage": overage}},
        {"type": "result", "subtype": subtype, "is_error": is_error, "result": text},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


def codex_stream(text="{\"ok\": true}", *, extra_items=(), failed=None):
    events = [{"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"},
              {"type": "item.completed", "item": {"id": "w", "type": "error", "message": "a warning"}}]
    events += [{"type": "item.completed", "item": item} for item in extra_items]
    if failed:
        events += [{"type": "error", "message": failed}, {"type": "turn.failed", "error": {"message": failed}}]
    else:
        events += [{"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": text}},
                   {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}]
    return "\n".join(json.dumps(e) for e in events) + "\n"


def clean_prompt_input(system="sys"):
    return json.dumps([{"type": "message", "role": "developer", "content": [{"text": system}]},
                       {"type": "message", "role": "user", "content": [{"text": "hello"}]}])


class FakeRun:
    """Stands in for the real child runner. Records what each child would have seen, while
    the temp directories still exist."""

    def __init__(self, *, claude=None, codex=None, catalog=None, prompt_input=None, on_exec=None, auth_status=None):
        self.auth_status = (json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"})
                            if auth_status is None else auth_status)
        self.claude = claude if claude is not None else claude_stream()
        self.codex = codex if codex is not None else codex_stream()
        self.catalog = json.dumps(CATALOG) if catalog is None else catalog
        self.prompt_input = clean_prompt_input() if prompt_input is None else prompt_input
        self.on_exec = on_exec
        self.calls = []
        self.lock = threading.Lock()

    def _files(self, argv, env):
        seen = {}
        for arg in argv:
            for prefix, name in (('model_instructions_file="', "codex_system"),
                                 ('model_catalog_json="', "codex_catalog")):
                if arg.startswith(prefix):
                    seen[name] = Path(arg[len(prefix):-1]).read_text()
        if "--system-prompt-file" in argv:
            seen["claude_system"] = Path(argv[argv.index("--system-prompt-file") + 1]).read_text()
        home = env.get("CODEX_HOME")
        if home:
            seen["codex_home"] = {p.name: (os.readlink(p) if p.is_symlink() else None)
                                  for p in Path(home).iterdir()}
        return seen

    def __call__(self, argv, *, input=None, env=None, cwd=None, timeout=None):
        call = SimpleNamespace(argv=list(argv), input=input, env=dict(env), cwd=cwd, timeout=timeout,
                               cwd_listing=os.listdir(cwd), files=self._files(argv, env))
        with self.lock:
            self.calls.append(call)
        tool = Path(argv[0]).name
        if argv[1:3] == ["debug", "models"]:
            return SimpleNamespace(returncode=0, stdout=self.catalog, stderr="")
        if argv[1:3] == ["debug", "prompt-input"]:
            return SimpleNamespace(returncode=0, stdout=self.prompt_input, stderr="")
        if argv[1:3] == ["auth", "status"]:
            return SimpleNamespace(returncode=0, stdout=self.auth_status, stderr="")
        if self.on_exec:
            result = self.on_exec(call)
            if result is not None:
                return result
        return SimpleNamespace(returncode=0, stdout=self.claude if tool == "claude" else self.codex, stderr="")

    def execs(self, tool=None):
        return [c for c in self.calls if c.argv[1] not in ("debug", "auth")
                and (tool is None or Path(c.argv[0]).name == tool)]


class FirstCallTracker:
    """Did any other child start while the first one was still running?"""

    def __init__(self, after_first=None):
        self.lock = threading.Lock()
        self.first_running = False
        self.overlapped = False
        self.seen = 0
        self.after_first = after_first

    def __call__(self, call):
        with self.lock:
            self.seen += 1
            first = self.seen == 1
            if first:
                self.first_running = True
            elif self.first_running:
                self.overlapped = True
        time.sleep(0.15)
        if first:
            with self.lock:
                if self.after_first:
                    self.after_first()
                self.first_running = False


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".codex").mkdir(parents=True)
    (h / ".codex" / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None}))
    (h / ".codex" / "installation_id").write_text("stable-id")
    return h


def make(home, run=None, *, environ=None, routes=ROUTES, secrets=(), find=None, **cfg):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8"}
    env.update(environ or {})
    config = BackupConfig(routes=routes, **cfg)
    client = BackupClient(config, environ=env, run=run or FakeRun(),
                          find=find or (lambda name, environ: f"/opt/{name}/bin/{name}"), secrets=secrets)
    return client


# --- the route table --------------------------------------------------------------------

@pytest.mark.parametrize("venice_model,tool,model", [
    ("openai-gpt-56-sol", "codex", "gpt-chair"),      # exact beats the star
    ("claude-opus-4-8", "claude", "claude-chair"),    # exact beats the family prefix
    ("claude-opus-4-7", "claude", "claude-seat"),     # family prefix
    ("deepseek-v4-pro", "codex", "gpt-seat"),         # everything else
])
def test_route_exact_then_prefix_then_star(venice_model, tool, model):
    route = BackupConfig(routes=ROUTES).route(venice_model)
    assert (route.tool, route.model) == (tool, model)


@pytest.mark.parametrize("floor", [float("nan"), float("inf"), -1.0])
def test_a_floor_that_is_not_a_sane_number_falls_back_to_the_default(floor):
    # NaN compares false with everything, so no balance would ever count as low.
    settings = SimpleNamespace(backup={"diem_floor": floor})
    assert BackupConfig.from_settings(settings).diem_floor == BackupConfig.diem_floor


def test_longest_prefix_wins():
    config = BackupConfig(routes=[{"match": "claude-", "tool": "claude", "model": "short"},
                                  {"match": "claude-opus-", "tool": "claude", "model": "long"}])
    assert config.route("claude-opus-4-7").model == "long"
    assert config.route("claude-sonnet-1").model == "short"


def test_model_without_a_route_is_a_seat_failure_not_a_guess(home):
    client = make(home, routes=[{"match": "claude-", "tool": "claude", "model": "x"}])
    with pytest.raises(BackupError, match="no backup route"):
        client.complete("grok-4-3", "s", "u")


def test_route_rejects_a_tool_that_is_not_claude_or_codex():
    with pytest.raises(ValueError):
        BackupConfig(routes=[{"match": "*", "tool": "bash", "model": "x"}]).route("anything")


# --- what a child receives --------------------------------------------------------------

@pytest.mark.parametrize("venice_model,tool", [("claude-opus-4-7", "claude"), ("grok-4-3", "codex")])
def test_prompt_goes_on_stdin_and_never_in_argv(home, venice_model, tool):
    run = FakeRun()
    client = make(home, run)
    client.complete(venice_model, "SYSTEM-PERSONA-TEXT", "USER-PROMPT-TEXT")
    call = run.execs(tool)[0]
    assert "USER-PROMPT-TEXT" in call.input
    assert "SYSTEM-PERSONA-TEXT" not in call.input        # instructions and data stay separate
    assert not any("USER-PROMPT-TEXT" in a or "SYSTEM-PERSONA-TEXT" in a for a in call.argv)
    assert "SYSTEM-PERSONA-TEXT" in call.files[f"{tool}_system"]


@pytest.mark.parametrize("venice_model", ["claude-opus-4-7", "grok-4-3"])
def test_child_environment_is_an_allow_list(home, venice_model):
    run = FakeRun()
    parent = {"ANTHROPIC_API_KEY": "sk-ant-x", "OPENAI_API_KEY": "sk-oa-x", "CODEX_API_KEY": "cx",
              "CODEX_ACCESS_TOKEN": "cat", "VENICE_API_KEY": "vk", "VENICE_COUNCIL_KEY": "vk2",
              "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CODE_OAUTH_TOKEN": "tok",
              "GITHUB_TOKEN": "gh", "TYPESAFE_API_KEY": "ts", "RANDOM_THING": "x",
              "COUNCIL_BACKUP": "force"}
    client = make(home, run, environ=parent)
    client.complete(venice_model, "s", "u")
    env = run.execs()[0].env
    for name in parent:
        assert name not in env, name
    assert env["HOME"] == str(home)
    assert set(env) <= {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "TZ", "TERM", "CODEX_HOME"}


def test_child_path_includes_the_tool_directory_for_its_node_shim(home):
    run = FakeRun()
    client = make(home, run, find=lambda name, environ: f"/nvm/node/bin/{name}")
    client.complete("grok-4-3", "s", "u")
    assert run.execs()[0].env["PATH"].split(":")[0] == "/nvm/node/bin"
    assert "/usr/bin" in run.execs()[0].env["PATH"].split(":")


@pytest.mark.parametrize("venice_model", ["claude-opus-4-7", "grok-4-3"])
def test_cwd_is_an_empty_temp_dir_and_all_temp_files_are_removed(home, venice_model):
    run = FakeRun()
    client = make(home, run)
    client.complete(venice_model, "s", "u")
    call = run.execs()[0]
    assert call.cwd_listing == []
    assert not Path(call.cwd).exists()
    assert not Path(call.cwd).parent.exists()


@pytest.mark.parametrize("venice_model", ["claude-opus-4-7", "grok-4-3"])
def test_temp_files_are_removed_when_the_child_fails(home, venice_model):
    def explode(call):
        raise OSError("spawn failed")
    run = FakeRun(on_exec=explode)
    client = make(home, run)
    with pytest.raises(BackupError):
        client.complete(venice_model, "s", "u")
    assert not Path(run.execs()[0].cwd).parent.exists()


def test_claude_argv_removes_every_extension_point(home):
    run = FakeRun()
    make(home, run).complete("claude-opus-4-7", "s", "u")
    argv = run.execs()[0].argv
    assert argv[0] == "/opt/claude/bin/claude"
    assert "-p" in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    for flag in ("--strict-mcp-config", "--safe-mode", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in argv, flag
    assert argv[argv.index("--model") + 1] == "claude-seat"
    assert argv[argv.index("--effort") + 1] == "medium"
    assert not any("dangerously" in a or a == "bypassPermissions" for a in argv)
    # --bare would be the obvious choice and is deliberately not used: it refuses OAuth.
    assert "--bare" not in argv


def test_codex_argv_and_home_keep_only_the_login(home):
    run = FakeRun()
    make(home, run).complete("grok-4-3", "s", "u")
    call = run.execs("codex")[0]
    argv = call.argv
    assert argv[:2] == ["/opt/codex/bin/codex", "exec"]
    assert argv[-1] == "-"
    for flag in ("--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"):
        assert flag in argv, flag
    assert argv[argv.index("-m") + 1] == "gpt-seat"
    assert 'model_reasoning_effort="medium"' in argv
    assert "shell_tool" in argv and "plugins" in argv and "hooks" in argv and "multi_agent" in argv
    assert not any("dangerously" in a or a == "danger-full-access" for a in argv)
    # A private CODEX_HOME: AGENTS.md, skills, MCP servers and config.toml live in the real one.
    links = call.files["codex_home"]
    assert links["auth.json"] == str(home / ".codex" / "auth.json")
    assert links["installation_id"] == str(home / ".codex" / "installation_id")
    assert Path(call.env["CODEX_HOME"]).name == "home"


def test_codex_catalog_strips_the_fields_that_grant_tools(home):
    run = FakeRun()
    make(home, run).complete("grok-4-3", "s", "u")
    catalog = json.loads(run.execs("codex")[0].files["codex_catalog"])
    assert [m["slug"] for m in catalog["models"]] == ["gpt-seat"]
    entry = catalog["models"][0]
    assert entry["base_instructions"] == "keep me"
    assert entry["shell_type"] == "disabled"
    for field in ("tool_mode", "multi_agent_version", "multi_agent_reasoning_effort",
                  "apply_patch_tool_type", "web_search_tool_type"):
        assert field not in entry
    assert entry["experimental_supported_tools"] == []
    assert entry["supports_search_tool"] is False


def test_codex_model_missing_from_the_catalog_fails_clearly(home):
    run = FakeRun(catalog=json.dumps({"models": [{"slug": "other"}]}))
    with pytest.raises(BackupError, match="gpt-seat.*not in"):
        make(home, run).complete("grok-4-3", "s", "u")
    assert run.execs("codex") == []


def test_codex_refuses_when_preflight_shows_tools_are_still_there(home):
    leaky = json.dumps([{"type": "additional_tools", "tools": [{"type": "function", "name": "exec"}]},
                        {"type": "message", "role": "user", "content": [{"text": "hello"}]}])
    run = FakeRun(prompt_input=leaky)
    client = make(home, run)
    with pytest.raises(BackupError, match="exec"):
        client.complete("grok-4-3", "s", "u")
    assert run.execs("codex") == []                       # no model call was made


def test_codex_refuses_when_preflight_shows_project_or_memory_text(home):
    leaky = json.dumps([{"type": "message", "role": "developer", "content": [{"text": "sys"}]},
                        {"type": "message", "role": "user", "content": [{"text": "# AGENTS.md instructions"}]},
                        {"type": "message", "role": "user", "content": [{"text": "hello"}]}])
    run = FakeRun(prompt_input=leaky)
    with pytest.raises(BackupError, match="unexpected context"):
        make(home, run).complete("grok-4-3", "s", "u")
    assert run.execs("codex") == []


@pytest.mark.parametrize("role,text", [("developer", "sys\n\n# Memory from earlier sessions"),
                                       ("user", "# AGENTS.md instructions\nhello")])
def test_codex_refuses_when_preflight_shows_text_added_inside_a_message(home, role, text):
    # Two messages with the right roles prove nothing if one of them carries a memory file.
    messages = {"developer": "sys", "user": "hello", role: text}
    leaky = json.dumps([{"type": "message", "role": r, "content": [{"text": t}]} for r, t in messages.items()])
    run = FakeRun(prompt_input=leaky)
    with pytest.raises(BackupError, match="unexpected context"):
        make(home, run).complete("grok-4-3", "s", "u")
    assert run.execs("codex") == []


def test_codex_preflight_uses_the_same_settings_as_the_real_call(home):
    run = FakeRun()
    make(home, run).complete("grok-4-3", "s", "u")
    preview = next(c for c in run.calls if c.argv[1:3] == ["debug", "prompt-input"])
    assert 'model_reasoning_effort="medium"' in preview.argv
    real = run.execs("codex")[0]
    assert [a for a in preview.argv if a in backup.CODEX_DISABLED_FEATURES] == \
           [a for a in real.argv if a in backup.CODEX_DISABLED_FEATURES]


@pytest.mark.parametrize("item", [{"type": "additional_tools"},
                                  {"type": "additional_tools", "tools": "exec"},
                                  {"type": "additional_tools", "tools": ["exec"]}])
def test_codex_refuses_a_preview_whose_tool_list_it_cannot_read(home, item):
    # A changed format must not read as "no tools".
    preview = json.dumps([item, {"type": "message", "role": "developer", "content": [{"text": "sys"}]},
                          {"type": "message", "role": "user", "content": [{"text": "hello"}]}])
    run = FakeRun(prompt_input=preview)
    with pytest.raises(BackupError):
        make(home, run).complete("grok-4-3", "s", "u")
    assert run.execs("codex") == []


def test_codex_accepts_a_preview_with_an_empty_tool_list(home):
    preview = json.dumps([{"type": "additional_tools", "tools": []},
                          {"type": "message", "role": "developer", "content": [{"text": "sys"}]},
                          {"type": "message", "role": "user", "content": [{"text": "hello"}]}])
    assert make(home, FakeRun(prompt_input=preview)).complete("grok-4-3", "s", "u")


def test_codex_preflight_runs_once_per_client(home):
    run = FakeRun()
    client = make(home, run)
    client.complete("grok-4-3", "s", "u")
    client.complete("deepseek-v4-pro", "s", "u")
    assert sum(1 for c in run.calls if c.argv[1:3] == ["debug", "prompt-input"]) == 1
    assert len(run.execs("codex")) == 2


# --- which login pays --------------------------------------------------------------------

def test_codex_api_key_login_is_refused_because_it_would_bill_the_api(home):
    (home / ".codex" / "auth.json").write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-x"}))
    run = FakeRun()
    with pytest.raises(BackupError, match="(?i)api") as exc:
        make(home, run).complete("grok-4-3", "s", "u")
    assert "sk-x" not in str(exc.value)
    assert run.calls == []


def test_codex_login_read_during_a_token_write_is_tried_again(home):
    # This Codex writes auth.json in place, so a read can land on a half-written file.
    auth = home / ".codex" / "auth.json"
    good = auth.read_text()
    auth.write_text('{"auth_mode": "chat')
    threading.Timer(0.05, lambda: auth.write_text(good)).start()
    assert make(home, FakeRun()).complete("grok-4-3", "s", "u")


def test_codex_login_that_stays_unreadable_is_a_clear_failure(home):
    (home / ".codex" / "auth.json").write_text('{"auth_mode": "chat')
    with pytest.raises(BackupError, match="cannot read"):
        make(home, FakeRun()).complete("grok-4-3", "s", "u")


def test_a_relative_codex_home_still_links_to_the_real_login(home, monkeypatch):
    monkeypatch.chdir(home)
    run = FakeRun()
    make(home, run, environ={"CODEX_HOME": ".codex"}).complete("grok-4-3", "s", "u")
    assert run.execs("codex")[0].files["codex_home"]["auth.json"] == str((home / ".codex" / "auth.json").resolve())


def test_codex_without_a_login_says_not_logged_in(home):
    (home / ".codex" / "auth.json").unlink()
    run = FakeRun()
    with pytest.raises(BackupError, match="not logged in"):
        make(home, run).complete("grok-4-3", "s", "u")
    assert run.calls == []


@pytest.mark.parametrize("status,wanted", [
    (json.dumps({"loggedIn": False, "authMethod": "none"}), "not logged in"),
    (json.dumps({"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"}), "(?i)plan"),
    ("not json at all", "(?i)cannot be proved"),
])
def test_claude_login_that_is_not_the_plan_is_refused_before_any_model_call(home, status, wanted):
    run = FakeRun(auth_status=status)
    client = make(home, run)
    with pytest.raises(BackupError, match=wanted):
        client.complete("claude-opus-4-7", "s", "u")
    assert run.execs("claude") == []
    with pytest.raises(BackupError):                     # the check is not re-run or forgotten
        client.complete("claude-opus-4-7", "s", "u")
    assert sum(1 for c in run.calls if c.argv[1:3] == ["auth", "status"]) == 1


def test_claude_login_check_sees_the_same_environment_as_the_child(home):
    run = FakeRun()
    make(home, run, environ={"ANTHROPIC_API_KEY": "sk-ant-x"}).complete("claude-opus-4-7", "s", "u")
    status = next(c for c in run.calls if c.argv[1:3] == ["auth", "status"])
    assert "ANTHROPIC_API_KEY" not in status.env


def test_claude_reply_billed_to_an_api_key_is_refused(home):
    run = FakeRun(claude=claude_stream(key_source="ANTHROPIC_API_KEY"))
    with pytest.raises(BackupError, match="ANTHROPIC_API_KEY"):
        make(home, run).complete("claude-opus-4-7", "s", "u")


def test_claude_paid_overage_is_refused(home):
    run = FakeRun(claude=claude_stream(overage=True))
    with pytest.raises(BackupError, match="(?i)overage"):
        make(home, run).complete("claude-opus-4-7", "s", "u")


@pytest.mark.parametrize("kwargs", [{"overage": True}, {"key_source": "ANTHROPIC_API_KEY"}, {"tools": ["Read"]}])
def test_after_one_refused_claude_reply_no_further_claude_child_starts(home, kwargs):
    # A charge cannot be undone once the call ran, so the next one must not run.
    run = FakeRun(claude=claude_stream(**kwargs))
    client = make(home, run)
    for _ in range(3):
        with pytest.raises(BackupError):
            client.complete("claude-opus-4-7", "s", "u")
    assert len(run.execs("claude")) == 1


def test_overage_on_a_failed_reply_also_stops_later_claude_children(home):
    stream = claude_stream("Request failed", is_error=True, subtype="error", overage=True)
    run = FakeRun(claude=stream)
    client = make(home, run)
    for _ in range(2):
        with pytest.raises(BackupError):
            client.complete("claude-opus-4-7", "s", "u")
    assert len(run.execs("claude")) == 1


def test_a_claude_call_with_no_setup_report_stops_later_claude_children(home):
    # A crash or a timeout leaves billing and isolation unknown, and only a reply can show them.
    run = FakeRun(on_exec=lambda call: SimpleNamespace(returncode=1, stdout="", stderr="crashed"))
    client = make(home, run)
    with pytest.raises(BackupError, match="crashed"):
        client.complete("claude-opus-4-7", "s", "u")
    with pytest.raises(BackupError, match="(?i)setup"):
        client.complete("claude-opus-4-7", "s", "u")
    assert len(run.execs("claude")) == 1


def test_a_claude_timeout_stops_later_claude_children(home):
    def slow(call):
        raise subprocess.TimeoutExpired(call.argv, call.timeout)
    run = FakeRun(on_exec=slow)
    client = make(home, run)
    with pytest.raises(BackupError, match="timed out"):
        client.complete("claude-opus-4-7", "s", "u")
    with pytest.raises(BackupError):
        client.complete("claude-opus-4-7", "s", "u")
    assert len(run.execs("claude")) == 1


def test_an_ordinary_failed_claude_reply_is_only_that_seat(home):
    # Its first event still reported a clean setup, so nothing is unknown.
    run = FakeRun(claude=claude_stream("Overloaded", is_error=True, subtype="error"))
    client = make(home, run)
    for _ in range(2):
        with pytest.raises(BackupError, match="Overloaded"):
            client.complete("claude-opus-4-7", "s", "u")
    assert len(run.execs("claude")) == 2


def test_a_failed_reply_that_shows_a_broken_setup_also_stops_later_claude_children(home):
    stream = claude_stream("Request failed", is_error=True, subtype="error", tools=["Read"])
    run = FakeRun(claude=stream)
    client = make(home, run)
    for _ in range(2):
        with pytest.raises(BackupError):
            client.complete("claude-opus-4-7", "s", "u")
    assert len(run.execs("claude")) == 1


def test_the_first_claude_call_runs_alone_until_its_billing_is_known(home):
    # Parallel seats would otherwise all be charged before the first reply could stop them.
    tracker = FirstCallTracker()
    client = make(home, FakeRun(on_exec=tracker), max_concurrent=3)
    threads = [threading.Thread(target=client.complete, args=("claude-opus-4-7", "s", "u")) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert tracker.seen == 3
    assert tracker.overlapped is False


def test_a_stale_codex_token_lets_one_child_refresh_before_the_others_start(home):
    # Seats start together. If the login is due a refresh, each would spend the same refresh
    # token, and the last writer could leave a dead one in the owner's auth.json.
    auth = home / ".codex" / "auth.json"
    auth.write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "last_refresh": "2026-01-01T00:00:00Z"}))
    fresh = json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
                        "last_refresh": datetime.now(timezone.utc).isoformat()})
    tracker = FirstCallTracker(after_first=lambda: auth.write_text(fresh))
    client = make(home, FakeRun(on_exec=tracker), max_concurrent=3)
    threads = [threading.Thread(target=client.complete, args=("grok-4-3", "s", "u")) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert tracker.seen == 3
    assert tracker.overlapped is False


def test_a_stale_codex_token_waits_for_a_refresh_in_another_council_process(home):
    import fcntl
    (home / ".codex" / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
                                                           "last_refresh": "2026-01-01T00:00:00Z"}))
    lock_path = home / ".codex" / ".council-refresh.lock"     # beside the login it protects
    holder = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX)
    run = FakeRun()
    done = threading.Event()
    def call():
        make(home, run).complete("grok-4-3", "s", "u")
        done.set()
    threading.Thread(target=call, daemon=True).start()
    assert not done.wait(0.4)
    assert run.calls == []                               # nothing starts, `codex debug` refreshes too
    os.close(holder)
    assert done.wait(5)
    assert len(run.execs("codex")) == 1


def test_a_login_that_cannot_be_moved_back_is_kept_and_codex_stops(home, monkeypatch, capsys):
    # If it holds the only live refresh token, deleting it with the temp folder loses the login,
    # and a further call would spend the old token from the real file.
    def replace_link(call):
        link = Path(call.env["CODEX_HOME"]) / "auth.json"
        link.unlink()
        link.write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "fresh": True}))
    def disk_full(path, data):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(backup, "_replace_private", disk_full)
    run = FakeRun(on_exec=replace_link)
    client = make(home, run)
    client.complete("grok-4-3", "s", "u")
    err = capsys.readouterr().err
    kept = Path(next(word for word in err.split() if "council-backup-" in word).rstrip("."))
    try:
        assert json.loads(kept.read_text())["fresh"] is True
        assert not (kept.parents[1] / "system.txt").exists()     # only the login stays behind
        assert not (kept.parents[1] / "catalog.json").exists()
        assert "fresh" not in err and "auth_mode" not in err     # the place is named, the value is not
        with pytest.raises(BackupError, match="(?i)login"):
            client.complete("grok-4-3", "s", "u")
        assert len(run.execs("codex")) == 1
    finally:
        shutil.rmtree(kept.parents[1])


def test_a_login_lost_during_preflight_stops_the_same_call_before_it_starts_a_child(home, monkeypatch, capsys):
    class Run(FakeRun):
        def __call__(self, argv, *, input=None, env=None, cwd=None, timeout=None):
            result = super().__call__(argv, input=input, env=env, cwd=cwd, timeout=timeout)
            if argv[1:3] == ["debug", "models"]:
                link = Path(env["CODEX_HOME"]) / "auth.json"
                link.unlink()
                link.write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None}))
            return result
    def disk_full(path, data):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(backup, "_replace_private", disk_full)
    run = Run()
    with pytest.raises(BackupError, match="(?i)login"):
        make(home, run).complete("grok-4-3", "s", "u")
    kept = Path(next(word for word in capsys.readouterr().err.split() if "council-backup-" in word).rstrip("."))
    shutil.rmtree(kept.parents[1])
    assert run.execs("codex") == []
    assert not [c for c in run.calls if c.argv[1:3] == ["debug", "prompt-input"]]


def test_a_call_waiting_on_the_refresh_lock_stops_if_the_login_was_lost_meanwhile(home, monkeypatch, capsys):
    auth = home / ".codex" / "auth.json"
    auth.write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "last_refresh": "2026-01-01T00:00:00Z"}))
    run = FakeRun()
    client = make(home, run)
    client.complete("grok-4-3", "s", "u")                # past the catalog and the preview
    execs_before = len(run.execs("codex"))
    # The next caller queues behind a holder that loses the login while it waits.
    real_gate = client._refresh_gate
    @contextlib.contextmanager
    def gate_where_the_holder_lost_the_login(auth, wait_seconds):
        with real_gate(auth, wait_seconds):
            client._codex_stopped = "codex: a newer login could not be saved"
            yield
    monkeypatch.setattr(client, "_refresh_gate", gate_where_the_holder_lost_the_login)
    with pytest.raises(BackupError, match="could not be saved"):
        client.complete("grok-4-3", "s", "u")
    assert len(run.execs("codex")) == execs_before


def test_a_replaced_login_link_is_moved_back_before_the_refresh_lock_is_released(home):
    # Otherwise another council process can take the lock and spend the old refresh token.
    import fcntl
    auth = home / ".codex" / "auth.json"
    auth.write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "last_refresh": "2026-01-01T00:00:00Z"}))
    fresh = {"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "last_refresh": datetime.now(timezone.utc).isoformat(), "fresh": True}
    lock_path = home / ".codex" / ".council-refresh.lock"
    seen = {}
    class Run(FakeRun):
        def __call__(self, argv, *, input=None, env=None, cwd=None, timeout=None):
            result = super().__call__(argv, input=input, env=env, cwd=cwd, timeout=timeout)
            if argv[1:3] == ["debug", "models"]:
                link = Path(env["CODEX_HOME"]) / "auth.json"
                link.unlink()
                link.write_text(json.dumps(fresh))
            return result
    real_replace = backup._replace_private
    def watching_replace(path, data):
        fd = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen["lock_was_held"] = False
        except OSError:
            seen["lock_was_held"] = True
        finally:
            os.close(fd)
        real_replace(path, data)
    backup._replace_private = watching_replace
    try:
        make(home, Run()).complete("grok-4-3", "s", "u")
    finally:
        backup._replace_private = real_replace
    assert seen["lock_was_held"] is True
    assert json.loads(auth.read_text())["fresh"] is True


def test_a_stale_codex_token_with_no_usable_lock_fails_the_seat_rather_than_risk_the_login(home):
    (home / ".codex" / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
                                                           "last_refresh": "2026-01-01T00:00:00Z"}))
    (home / ".codex" / ".council-refresh.lock").mkdir()      # a folder where the lock file should be
    run = FakeRun()
    with pytest.raises(BackupError, match="lock"):
        make(home, run).complete("grok-4-3", "s", "u")
    assert run.execs("codex") == []


def test_a_fresh_codex_token_needs_no_lock_at_all(home):
    (home / ".codex" / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
                                                           "last_refresh": datetime.now(timezone.utc).isoformat()}))
    (home / ".codex" / ".council-refresh.lock").mkdir()
    assert make(home, FakeRun()).complete("grok-4-3", "s", "u")


def test_the_refresh_lock_follows_the_login_when_codex_home_is_elsewhere(tmp_path, home):
    import fcntl
    shared = tmp_path / "shared-codex"
    shared.mkdir()
    (shared / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
                                                  "last_refresh": "2026-01-01T00:00:00Z"}))
    holder = os.open(shared / ".council-refresh.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX)
    run = FakeRun()
    done = threading.Event()
    def call():
        make(home, run, environ={"CODEX_HOME": str(shared)}).complete("grok-4-3", "s", "u")
        done.set()
    threading.Thread(target=call, daemon=True).start()
    assert not done.wait(0.4)
    assert run.calls == []
    os.close(holder)
    assert done.wait(5)


def test_a_fresh_codex_token_keeps_seats_parallel(home):
    auth = home / ".codex" / "auth.json"
    auth.write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
                                "last_refresh": datetime.now(timezone.utc).isoformat()}))
    live, peak, lock = [0], [0], threading.Lock()
    def track(call):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.1)
        with lock:
            live[0] -= 1
    client = make(home, FakeRun(on_exec=track), max_concurrent=3)
    threads = [threading.Thread(target=client.complete, args=("grok-4-3", "s", "u")) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 3


def test_codex_token_refresh_replacing_the_login_link_is_moved_back(home):
    # This Codex writes through the symlink (checked against fake tokens). If a later one
    # replaces it instead, the new token must not die with the temp directory: the old
    # refresh token is already spent and the owner's real login would break.
    def replace_link(call):
        link = Path(call.env["CODEX_HOME"]) / "auth.json"
        link.unlink()
        link.write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "fresh": True}))
    run = FakeRun(on_exec=replace_link)
    make(home, run).complete("grok-4-3", "s", "u")
    assert json.loads((home / ".codex" / "auth.json").read_text())["fresh"] is True


# --- proof of isolation inside every Claude reply -----------------------------------------

@pytest.mark.parametrize("kwargs,word", [
    ({"tools": ["Read"]}, "Read"),
    ({"mcp": [{"name": "slack"}]}, "slack"),
    ({"plugins": [{"name": "evil"}]}, "evil"),
    ({"skills": ["some-skill"]}, "some-skill"),
])
def test_claude_reply_is_refused_when_init_shows_an_extension(home, kwargs, word):
    run = FakeRun(claude=claude_stream(**kwargs))
    with pytest.raises(BackupError, match=word):
        make(home, run).complete("claude-opus-4-7", "s", "u")


@pytest.mark.parametrize("missing", ["tools", "mcp_servers", "plugins", "skills", "apiKeySource"])
def test_claude_reply_is_refused_when_init_leaves_out_a_field(home, missing):
    # A changed event must not read as "nothing loaded".
    events = [json.loads(line) for line in claude_stream().splitlines()]
    del events[0][missing]
    stream = "\n".join(json.dumps(e) for e in events) + "\n"
    with pytest.raises(BackupError, match=missing if missing != "apiKeySource" else "billed"):
        make(home, FakeRun(claude=stream)).complete("claude-opus-4-7", "s", "u")


def test_claude_reply_without_an_init_event_is_refused(home):
    stream = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "{}"}) + "\n"
    with pytest.raises(BackupError, match="(?i)init"):
        make(home, FakeRun(claude=stream)).complete("claude-opus-4-7", "s", "u")


@pytest.mark.parametrize("item_type", ["command_execution", "file_change", "mcp_tool_call", "web_search"])
def test_codex_reply_is_refused_when_the_child_used_a_tool(home, item_type):
    run = FakeRun(codex=codex_stream(extra_items=[{"id": "x", "type": item_type}]))
    with pytest.raises(BackupError, match=item_type):
        make(home, run).complete("grok-4-3", "s", "u")


# --- unwrapping ---------------------------------------------------------------------------

@pytest.mark.parametrize("venice_model,stream", [
    ("claude-opus-4-7", claude_stream('```json\n{"stance": "approve"}\n```')),
    ("grok-4-3", codex_stream('```json\n{"stance": "approve"}\n```')),
])
def test_reply_is_unwrapped_for_the_existing_lenient_parser(home, venice_model, stream):
    run = FakeRun(claude=stream, codex=stream)
    raw = make(home, run).complete(venice_model, "s", "u")
    assert loads_lenient(raw) == {"stance": "approve"}


def test_codex_warning_items_do_not_fail_a_good_reply(home):
    # codex_stream always carries a `type: error` warning item; Codex emits those for retries
    # and unknown settings, and they are not failures on their own.
    assert make(home, FakeRun()).complete("grok-4-3", "s", "u") == '{"ok": true}'


def test_json_mode_asks_for_bare_json_and_plain_mode_does_not(home):
    run = FakeRun()
    client = make(home, run)
    client.complete("claude-opus-4-7", "s", "u", json_mode=True)
    client.complete("claude-opus-4-7", "s", "u", json_mode=False)
    assert "JSON" in run.execs()[0].files["claude_system"]
    assert run.execs()[1].files["claude_system"] == "s"


def test_output_cap_is_accepted_but_never_faked_as_a_flag(home):
    run = FakeRun()
    make(home, run).complete("claude-opus-4-7", "s", "u", max_completion_tokens=123, task_type="review")
    argv = run.execs()[0].argv
    assert "123" not in argv
    assert not any("temperature" in a or "max-tokens" in a or "max_output" in a for a in argv)


# --- failures are normal seat failures ------------------------------------------------------

@pytest.mark.parametrize("venice_model,kind", [("claude-opus-4-7", "claude"), ("grok-4-3", "codex")])
def test_empty_reply_is_a_failure(home, venice_model, kind):
    stream = claude_stream("") if kind == "claude" else codex_stream("")
    with pytest.raises(BackupError, match="empty"):
        make(home, FakeRun(claude=stream, codex=stream)).complete(venice_model, "s", "u")


@pytest.mark.parametrize("venice_model,stream,wanted", [
    ("claude-opus-4-7", claude_stream("Not logged in · Please run /login", is_error=True, subtype="error"), "not logged in"),
    ("claude-opus-4-7", claude_stream("Claude AI usage limit reached|1794196800", is_error=True, subtype="error"), "plan limit reached"),
    ("claude-opus-4-7", claude_stream("You've hit your limit · resets 3pm", is_error=True, subtype="error"), "plan limit reached"),
    ("grok-4-3", codex_stream(failed="unexpected status 401 Unauthorized: Missing bearer"), "not logged in"),
    ("grok-4-3", codex_stream(failed="You've hit your usage limit. Upgrade to Pro"), "plan limit reached"),
    ("grok-4-3", codex_stream(failed="The 'x' model is not supported when using Codex"), "not supported"),
])
def test_tool_failures_say_what_happened(home, venice_model, stream, wanted):
    run = FakeRun(claude=stream, codex=stream)
    with pytest.raises(BackupError, match=wanted):
        make(home, run).complete(venice_model, "s", "u")


def test_claude_rejected_rate_limit_event_reads_as_plan_limit_whatever_the_wording(home):
    stream = claude_stream("Request failed", is_error=True, subtype="error", limit="rejected")
    with pytest.raises(BackupError, match="plan limit reached"):
        make(home, FakeRun(claude=stream)).complete("claude-opus-4-7", "s", "u")


@pytest.mark.parametrize("message", ["usage_limit_reached", "Rate limit reached for model", "HTTP 429"])
def test_codex_limit_wordings(home, message):
    with pytest.raises(BackupError, match="plan limit reached"):
        make(home, FakeRun(codex=codex_stream(failed=message))).complete("grok-4-3", "s", "u")


@pytest.mark.parametrize("venice_model", ["claude-opus-4-7", "grok-4-3"])
def test_nonzero_exit_with_no_events_is_a_failure(home, venice_model):
    run = FakeRun(on_exec=lambda call: SimpleNamespace(returncode=1, stdout="", stderr="boom on stderr"))
    with pytest.raises(BackupError, match="boom on stderr"):
        make(home, run).complete(venice_model, "s", "u")


def test_timeout_comes_from_the_route_and_is_reported(home):
    def slow(call):
        raise subprocess.TimeoutExpired(call.argv, call.timeout)
    run = FakeRun(on_exec=slow)
    with pytest.raises(BackupError, match="timed out after 300s"):
        make(home, run).complete("grok-4-3", "s", "u")
    assert run.execs()[0].timeout == 300


def test_missing_tool_is_a_seat_failure_with_a_clear_message(home):
    client = make(home, find=lambda name, environ: None)
    with pytest.raises(BackupError, match="claude.*not found"):
        client.complete("claude-opus-4-7", "s", "u")


def test_available_tools_reports_what_can_be_found(home):
    only_codex = make(home, find=lambda name, environ: "/x/codex" if name == "codex" else None)
    assert only_codex.available_tools() == ["codex"]
    assert make(home, find=lambda name, environ: None).available_tools() == []
    assert make(home).available_tools() == ["claude", "codex"]


# --- secrets ----------------------------------------------------------------------------------

@pytest.mark.parametrize("venice_model,kind", [("claude-opus-4-7", "claude"), ("grok-4-3", "codex")])
def test_scrub_removes_keys_from_stdin_and_the_system_file(home, venice_model, kind):
    run = FakeRun()
    client = make(home, run, secrets=["venice-key-123"],
                  environ={"SOME_API_TOKEN": "env-secret-456", "PLAIN": "not-a-secret-789"})
    client.complete(venice_model, "sys venice-key-123 env-secret-456", "usr venice-key-123 env-secret-456 not-a-secret-789")
    call = run.execs()[0]
    blob = call.input + call.files[f"{kind}_system"]
    assert "venice-key-123" not in blob and "env-secret-456" not in blob
    assert "<redacted>" in blob
    assert "not-a-secret-789" in blob


def test_child_error_text_is_scrubbed(home):
    run = FakeRun(on_exec=lambda call: SimpleNamespace(returncode=1, stdout="", stderr="failed with venice-key-123"))
    with pytest.raises(BackupError) as exc:
        make(home, run, secrets=["venice-key-123"]).complete("grok-4-3", "s", "u")
    assert "venice-key-123" not in str(exc.value)


# --- concurrency ------------------------------------------------------------------------------

def test_concurrent_children_are_limited(home):
    live, peak, lock = [0], [0], threading.Lock()
    def track(call):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        with lock:
            live[0] -= 1
    client = make(home, FakeRun(on_exec=track), max_concurrent=2)
    threads = [threading.Thread(target=client.complete, args=("claude-opus-4-7", "s", "u")) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 2


# --- the real child runner, against real (harmless) processes ---------------------------------

def _script(tmp_path, body):
    path = tmp_path / "fake-tool"
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(0o755)
    return str(path)


def test_real_runner_passes_stdin_and_captures_output(tmp_path):
    tool = _script(tmp_path, "import sys\nsys.stdout.write(sys.stdin.read().upper())\n")
    done = backup.run_child([tool], input="héllo", env={"PATH": os.environ["PATH"]}, cwd=str(tmp_path), timeout=20)
    assert (done.returncode, done.stdout) == (0, "HÉLLO")


def test_real_runner_starts_nothing_once_council_is_stopping(tmp_path):
    marker = tmp_path / "started"
    tool = _script(tmp_path, f"open({str(marker)!r}, 'w').close()\n")
    backup._stop_children()
    try:
        with pytest.raises(OSError):
            backup.run_child([tool], input="", env={"PATH": os.environ["PATH"]}, cwd=str(tmp_path), timeout=20)
    finally:
        backup._stopping.clear()
    assert not marker.exists()


def test_a_signal_landing_while_the_child_list_is_locked_cannot_hang_council():
    # A signal handler runs on the main thread, and the chair and router call run_child from the
    # main thread too. A handler that blocks on a lock its own thread holds would never return.
    def handler_inside_the_lock():
        with backup._live_lock:
            backup._stop_children()
    thread = threading.Thread(target=handler_inside_the_lock, daemon=True)
    thread.start()
    thread.join(3)
    hung = thread.is_alive()
    backup._stopping.clear()
    assert not hung


def test_a_signal_during_a_main_thread_spawn_still_kills_that_child(tmp_path, monkeypatch):
    # The chair and the router run on the main thread, where a signal can land between starting a
    # child and listing it. That child must not outlive council.
    marker = tmp_path / "child.pid"
    tool = _script(tmp_path, f"import os, time\nopen({str(marker)!r}, 'w').write(str(os.getpid()))\ntime.sleep(120)\n")
    real_popen = subprocess.Popen
    def popen_then_signal(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        signal.raise_signal(signal.SIGTERM)
        return proc
    monkeypatch.setattr(subprocess, "Popen", popen_then_signal)
    pid = None
    with backup.child_cleanup():
        with pytest.raises(SystemExit) as stopped:       # cancelled, not an ordinary failed call
            backup.run_child([tool], input="", env={"PATH": os.environ["PATH"]}, cwd=str(tmp_path), timeout=60)
    assert stopped.value.code == 128 + signal.SIGTERM
    monkeypatch.undo()
    deadline = time.time() + 5
    while time.time() < deadline and not marker.exists():
        time.sleep(0.02)
    if marker.exists():                                  # it may have died before it could write
        pid = int(marker.read_text())
        while time.time() < deadline and _alive(pid):
            time.sleep(0.05)
        assert not _alive(pid)
    assert backup._live == set()


def test_real_runner_timeout_kills_the_whole_process_group(tmp_path):
    marker = tmp_path / "grandchild.pid"
    tool = _script(tmp_path, f"""
import subprocess, sys, time
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
open({str(marker)!r}, "w").write(str(p.pid))
time.sleep(120)
""")
    with pytest.raises(subprocess.TimeoutExpired):
        backup.run_child([tool], input="", env={"PATH": os.environ["PATH"]}, cwd=str(tmp_path), timeout=2)
    pid = int(marker.read_text())
    deadline = time.time() + 5
    while time.time() < deadline and _alive(pid):
        time.sleep(0.05)
    assert not _alive(pid)


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def test_real_runner_leaves_nothing_behind_after_a_normal_exit(tmp_path):
    marker = tmp_path / "straggler.pid"
    tool = _script(tmp_path, f"""
import subprocess, sys
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
open({str(marker)!r}, "w").write(str(p.pid))
""")
    backup.run_child([tool], input="", env={"PATH": os.environ["PATH"]}, cwd=str(tmp_path), timeout=20)
    pid = int(marker.read_text())
    deadline = time.time() + 5
    while time.time() < deadline and _alive(pid):
        time.sleep(0.05)
    assert not _alive(pid)


def test_find_tool_uses_the_given_path(tmp_path):
    (tmp_path / "bin").mkdir()
    tool = tmp_path / "bin" / "codex"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    env = {"PATH": str(tmp_path / "bin"), "HOME": str(tmp_path / "nohome")}
    assert backup.find_tool("codex", env) == str(tool)
    assert backup.find_tool("claude", env) is None


@pytest.mark.parametrize("rel", [".local/bin/claude", ".nvm/versions/node/v22.0.0/bin/claude"])
def test_find_tool_falls_back_to_the_usual_install_places(tmp_path, rel):
    tool = tmp_path / rel
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    assert backup.find_tool("claude", {"PATH": "/nonexistent", "HOME": str(tmp_path)}) == str(tool)
