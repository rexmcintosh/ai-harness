"""Backup mode: the same panel, chair and gate, run on the owner's Claude and ChatGPT plans.

When Venice's free daily credits (DIEM) are gone, `council ask`, `review` and `compare` move to
the `claude` and `codex` command-line tools instead of letting Venice fall through to real money.
Owner decisions and the flags, models and measurements behind this file:
docs/council-backup-mode-2026-11-04.md.

Nothing outside the council CLI reaches this module. run_pr_review, the CI shim, backlog-run and
the diem drain build their own VeniceClient and never see it.
"""
from __future__ import annotations

import contextlib
import glob
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .venice import VeniceOutOfCredits

TOOLS = ("claude", "codex")
MODES = ("auto", "off", "force")
EXIT_INCOMPLETE = 3       # no result: exit 2 stays "operator error", exit 0 stays "ran"
LOG_ENV = "COUNCIL_BACKUP_LOG"
DEFAULT_LOG = "~/.local/state/council/backup-runs.jsonl"

# Who really answered, for the banner: a tool's family, or the family a Venice model id starts with.
_FAMILIES = {"claude": "Claude", "codex": "OpenAI", "openai": "OpenAI", "deepseek": "DeepSeek",
             "grok": "xAI", "gemini": "Google"}
_VENICE = "Venice"
_RECHECK_SECONDS = 30     # a key with a USD limit sends no error at DIEM 0, so keep looking
_CODEX_REFRESH_DAYS = 7   # Codex refreshes a token 8 days after the last refresh

# Exactly what a child may see. Everything else stays behind: ANTHROPIC_API_KEY and
# OPENAI_API_KEY would bill the API instead of the plan, VENICE_* and other secrets have no
# business there, and CLAUDECODE / CLAUDE_CODE_* would mark a nested Claude Code session.
# Live-tested with only these set (env -i), on 2026-11-04.
ENV_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE")

_SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")

# `--safe-mode --setting-sources ""` still lists these built-in plugins (Claude Code 2.1.317);
# only an explicit enabledPlugins entry removes them. A new one fails closed with its name in
# the message: add it here after reading what it does.
CLAUDE_BUILTIN_PLUGINS = ("cc-plugin-agents-md", "cc-plugin-telemetry", "cc-plugin-privacy",
                          "cc-plugin-plugin-authoring")

# Codex features that add tools, hooks or plugins. On GPT-6 models these are not enough by
# themselves: the model catalog forces a JavaScript `exec` tool and sub-agents back on, which is
# what _codex_entry removes. Each run proves the outcome with `codex debug prompt-input`.
CODEX_DISABLED_FEATURES = ("shell_tool", "unified_exec", "plugins", "hooks", "multi_agent", "multi_agent_v2",
                           "apps", "image_generation", "browser_use", "computer_use", "view_image", "goals",
                           "skill_search", "tool_suggest", "shell_snapshot", "sleep_tool", "memories",
                           "workspace_dependencies", "code_mode", "code_mode_host")

# Item kinds a tool-free `codex exec` may emit. Anything else means a tool ran.
_CODEX_PLAIN_ITEMS = ("agent_message", "reasoning", "error")

JSON_ONLY = "\n\nReturn only the JSON object. No prose around it and no code fences."

_LIMIT_WORDS = re.compile(r"usage[ _]limit|limit[ _]reached|hit your .{0,20}limit|rate[ _]limit|quota|too many requests|\b429\b", re.I)
_LOGIN_WORDS = re.compile(r"not logged in|/login|codex login|\b401\b|unauthorized|authentication|credentials were found", re.I)
_LOGIN_COMMAND = {"claude": "claude auth login", "codex": "codex login"}

_DEBUG_TIMEOUT = 60       # seconds, for `codex debug ...` and `claude auth status`: local, no model call
_PREVIEW_SYSTEM = "sys"
_PREVIEW_USER = "hello"


class BackupError(RuntimeError):
    """One backup call failed. Engine, chair and router treat it like any failed Venice call."""


# --- the route table ---------------------------------------------------------------------

@dataclass(frozen=True)
class Route:
    tool: str
    model: str
    effort: str | None = None
    timeout: int = 900

    @property
    def label(self) -> str:
        return f"{self.tool}/{self.model}"


@dataclass
class BackupConfig:
    routes: list = field(default_factory=list)
    diem_floor: float = 0.5
    balance_timeout: float = 5
    max_concurrent: int = 4

    @classmethod
    def from_settings(cls, settings) -> "BackupConfig":
        raw = getattr(settings, "backup", None) or {}
        floor = float(raw.get("diem_floor", cls.diem_floor))
        if not (math.isfinite(floor) and floor >= 0):      # NaN would never count a balance as low
            floor = cls.diem_floor
        return cls(routes=list(raw.get("routes") or []),
                   diem_floor=floor,
                   balance_timeout=float(raw.get("balance_timeout", cls.balance_timeout)),
                   max_concurrent=int(raw.get("max_concurrent", cls.max_concurrent)))

    def route(self, venice_model: str) -> Route | None:
        """The longest `match` that starts the model name, so an exact id beats a family
        prefix. `*` is what is left. None means no mapping: never guess a model."""
        name = str(venice_model)
        matches = [r for r in self.routes if r["match"] != "*" and name.startswith(r["match"])]
        chosen = max(matches, key=lambda r: len(r["match"])) if matches else next(
            (r for r in self.routes if r["match"] == "*"), None)
        if chosen is None:
            return None
        if chosen["tool"] not in TOOLS:
            raise ValueError(f"backup route {chosen['match']!r} names unknown tool {chosen['tool']!r}")
        return Route(tool=chosen["tool"], model=chosen["model"], effort=chosen.get("effort"),
                     timeout=int(chosen.get("timeout", Route.timeout)))


# --- finding and running a child ----------------------------------------------------------

def find_tool(name: str, environ) -> str | None:
    """The tool on the given PATH, else where it is normally installed: cron and service
    shells often lack the nvm directory that holds `codex`."""
    found = shutil.which(name, path=environ.get("PATH") or "")
    if found:
        return found
    home = environ.get("HOME") or ""
    candidates = [os.path.join(home, ".local", "bin", name)]
    candidates += sorted(glob.glob(os.path.join(glob.escape(home), ".nvm", "versions", "node", "*", "bin", name)),
                         reverse=True)
    return next((c for c in candidates if os.path.isfile(c) and os.access(c, os.X_OK)), None)


_live: set = set()
_live_lock = threading.RLock()    # reentrant: the signal handler can land on a thread already holding it
_stopping = threading.Event()
_spawn_state = threading.local()


def _kill_group(proc) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


def run_child(argv, *, input, env, cwd, timeout):
    """subprocess.run, with one difference that matters: `codex` is a node shim around a native
    binary, so killing only the direct child on a timeout leaves the binary running and holding
    the output pipe. Each child gets its own session and the whole group goes when it is done."""
    with _live_lock:                 # one step, so a stop cannot fall between the check and the start
        if _stopping.is_set():
            raise OSError("council is shutting down")
        _spawn_state.active = True   # the chair runs on the main thread, where signal handlers run
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=env, cwd=cwd, text=True, encoding="utf-8", errors="replace",
                                    start_new_session=True)
            _live.add(proc)
        finally:
            _spawn_state.active = False
    if _stopping.is_set():           # a signal landed during that step; its handler left this to us
        _kill_group(proc)
        with _live_lock:
            _live.discard(proc)
        signum = getattr(_spawn_state, "signum", None)
        if signum:
            _spawn_state.signum = None
            raise SystemExit(128 + signum)
        raise OSError("council is shutting down")
    try:
        try:
            out, err = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=5)
            raise
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)
    finally:
        _kill_group(proc)
        with _live_lock:
            _live.discard(proc)


# --- the client ----------------------------------------------------------------------------

class BackupClient:
    """VeniceClient's complete(), through a command-line tool on a plan. `run` and `find` are
    injectable for tests. temperature and max_completion_tokens have no equivalent on these
    tools: the cap is accepted and not used, and nothing pretends otherwise."""

    def __init__(self, config: BackupConfig, *, environ=None, run=None, find=None, secrets=()):
        self.config = config
        self.environ = dict(os.environ if environ is None else environ)
        self._run = run or run_child
        self._find = find or find_tool
        # The same defense as VeniceClient._scrub, wider because these prompts leave for another
        # vendor: the Venice key, and anything in the environment named like a secret.
        found = {v for k, v in self.environ.items()
                 if v and len(v) >= 8 and any(word in k.upper() for word in _SECRET_WORDS)}
        self._secrets = sorted(found | {s for s in secrets if s}, key=len, reverse=True)
        self._slots = threading.BoundedSemaphore(max(1, int(config.max_concurrent)))
        self._prepare_lock = threading.Lock()
        self._catalog = None
        self._proved: dict[str, str | None] = {}
        self._claude_login_problem: str | None = None
        self._claude_login_checked = False
        self._claude_stopped: str | None = None
        self._codex_stopped: str | None = None
        self._claude_first_lock = threading.Lock()
        self._claude_cleared = False

    def available_tools(self) -> list[str]:
        return [t for t in TOOLS if self._find(t, self.environ)]

    def _scrub(self, text):
        for secret in self._secrets:
            text = text.replace(secret, "<redacted>")
        return text

    def _explain(self, tool: str, text: str) -> str:
        """A tool's failure text, naming the two failures a person can act on."""
        short = self._scrub(" ".join(str(text).split()))[:300]
        if _LIMIT_WORDS.search(text):
            return f"{tool}: plan limit reached ({short})"
        if _LOGIN_WORDS.search(text):
            return f"{tool}: not logged in ({short}). Run `{_LOGIN_COMMAND[tool]}`."
        return f"{tool}: {short}"

    def complete(self, model, system, user, *, json_mode=True, task_type="chat",
                 max_completion_tokens=None):
        route = self.config.route(model)
        if route is None:
            raise BackupError(f"no backup route for model {model!r}; add one under "
                              "[settings.backup] in panels.toml")
        tool_path = self._find(route.tool, self.environ)
        if not tool_path:
            raise BackupError(f"{route.tool}: command not found on PATH or in the usual places")
        system_text = self._scrub(system) + (JSON_ONLY if json_mode else "")
        user_text = self._scrub(user)
        with self._slots:
            if route.tool == "claude":
                return self._claude(tool_path, route, system_text, user_text)
            return self._codex(tool_path, route, system_text, user_text)

    def _child_env(self, tool_path: str, **extra) -> dict:
        env = {k: self.environ[k] for k in ENV_ALLOW if self.environ.get(k)}
        env.setdefault("LANG", "C.UTF-8")
        # The node shim needs `node`, which sits next to it.
        tool_dir = os.path.dirname(tool_path)
        parts = [p for p in env.get("PATH", "").split(":") if p and p != tool_dir]
        env["PATH"] = ":".join([tool_dir, *parts])
        env.update(extra)
        return env

    def _call(self, tool: str, route_timeout: int, argv, *, input, env, cwd):
        try:
            return self._run(argv, input=input, env=env, cwd=cwd, timeout=route_timeout)
        except subprocess.TimeoutExpired:
            raise BackupError(f"{tool}: timed out after {route_timeout}s") from None
        except OSError as exc:
            raise BackupError(f"{tool}: could not start ({exc})") from None

    # --- claude ---------------------------------------------------------------------------

    def _prove_claude_login(self, tool_path) -> None:
        """Before the first model call: is the login these children will use the plan? A reply
        can only report who it billed after the charge has happened. Local, no model call, and
        run with the child's own environment, because that is what decides the answer."""
        with self._prepare_lock:
            if not self._claude_login_checked:
                self._claude_login_problem = self._inspect_claude_login(tool_path)
                self._claude_login_checked = True
            problem = self._claude_login_problem
        if problem:
            raise BackupError(problem)

    def _inspect_claude_login(self, tool_path) -> str | None:
        with _workspace() as (_root, cwd):
            try:
                done = self._call("claude", _DEBUG_TIMEOUT, [tool_path, "auth", "status"], input="",
                                  env=self._child_env(tool_path), cwd=cwd)
            except BackupError as exc:
                return f"{exc}; which login claude would bill cannot be proved, so it was not used"
        try:
            status = json.loads(done.stdout)
            logged_in, method = status["loggedIn"], status["authMethod"]
        except (ValueError, KeyError, TypeError):
            return "claude: which login it would bill cannot be proved (unreadable `claude auth status`); not used"
        if not logged_in:
            return "claude: not logged in. Run `claude auth login`."
        if method != "claude.ai":
            return f"claude: this login ({method}) is not the plan and would bill the API; not used"
        return None

    def _claude(self, tool_path, route, system_text, user_text) -> str:
        self._prove_claude_login(tool_path)
        # Paid overage shows only in a reply, so the first call runs alone: parallel seats would
        # all be charged before it could stop them. Packaged panels hold one Claude seat at most.
        if not self._claude_cleared:
            with self._claude_first_lock:
                if not self._claude_cleared:
                    text = self._run_claude(tool_path, route, system_text, user_text)
                    self._claude_cleared = True
                    return text
        return self._run_claude(tool_path, route, system_text, user_text)

    def _run_claude(self, tool_path, route, system_text, user_text) -> str:
        if self._claude_stopped:
            raise BackupError(self._claude_stopped)
        with _workspace() as (root, cwd):
            system_file = root / "system.txt"
            system_file.write_text(system_text, encoding="utf-8")
            plugins_off = {"enabledPlugins": {f"{p}@builtin": False for p in CLAUDE_BUILTIN_PLUGINS}}
            argv = [tool_path, "-p", "--output-format", "stream-json", "--verbose",
                    "--model", route.model, *(["--effort", route.effort] if route.effort else []),
                    "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--safe-mode",
                    "--disable-slash-commands", "--no-session-persistence", "--permission-mode", "dontAsk",
                    "--settings", json.dumps(plugins_off), "--system-prompt-file", str(system_file)]
            try:
                done = self._call("claude", route.timeout, argv, input=user_text,
                                  env=self._child_env(tool_path), cwd=cwd)
            except BackupError as exc:
                self._claude_stopped = f"claude: stopped after a call that never reported its setup ({exc})"
                raise
        return self._read_claude(done)

    def _read_claude(self, done) -> str:
        init = result = None
        overage = rejected = False
        for event in _json_lines(done.stdout):
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                init = event
            elif kind == "result":
                result = event
            elif kind == "rate_limit_event":
                info = event.get("rate_limit_info") or {}
                overage = overage or bool(info.get("isUsingOverage"))
                rejected = rejected or info.get("status") == "rejected"
        # Checked whatever the reply says, because a failed call came from the same setup. The charge
        # for this call cannot be undone, so no later seat may add to it.
        # A call that reported nothing (a crash, say) leaves billing and isolation unknown, and the
        # init event comes first on any call that really started, so an API failure still has one.
        self._claude_stopped = self._claude_setup_problem(init) or self._claude_stopped
        if overage:
            self._claude_stopped = "claude: plan limit reached and a reply used paid overage; refused"
        if result is None or result.get("is_error") or result.get("subtype") != "success" or done.returncode != 0:
            reason = str((result or {}).get("result") or done.stderr or done.stdout or f"exit {done.returncode}")
            if rejected:
                raise BackupError(f"claude: plan limit reached ({self._scrub(' '.join(reason.split()))[:200]})")
            raise BackupError(self._explain("claude", reason))
        if self._claude_stopped:
            raise BackupError(self._claude_stopped)
        text = str(result.get("result") or "")
        if not text.strip():
            raise BackupError("claude: empty reply")
        return text

    @staticmethod
    def _claude_setup_problem(init) -> str | None:
        """Claude Code states its whole setup in the first event of every reply. A reply from a
        child that was not isolated, or that billed an API key, is thrown away."""
        if init is None:
            return "claude: no setup report (init event) in the last reply, so its isolation cannot be proved; refused"
        problems = []
        for key in ("tools", "mcp_servers", "plugins", "skills"):
            found = init.get(key)
            if not isinstance(found, list):      # a changed event must not read as "nothing loaded"
                problems.append(f"{key} not reported")
            elif found:
                names = [x.get("name") if isinstance(x, dict) else x for x in found]
                problems.append(f"{key} {names}")
        if init.get("apiKeySource") != "none":
            problems.append(f"billed through {init.get('apiKeySource')}")
        if problems:
            return "claude: child was not isolated (" + "; ".join(problems) + "); reply refused"
        return None

    # --- codex ----------------------------------------------------------------------------

    def _codex_login(self) -> Path:
        """Only a ChatGPT login is the plan. An API key in auth.json would bill the API. Reads
        two fields and repeats no value."""
        real_home = self.environ.get("CODEX_HOME") or os.path.join(self.environ.get("HOME") or "~", ".codex")
        auth = Path(real_home).expanduser().resolve() / "auth.json"
        try:
            try:
                data = json.loads(auth.read_text())
            except ValueError:               # Codex writes this file in place: a refresh may be mid-write
                time.sleep(0.5)
                data = json.loads(auth.read_text())
        except FileNotFoundError:
            raise BackupError("codex: not logged in (no auth.json). Run `codex login`.") from None
        except (OSError, ValueError):
            raise BackupError(f"codex: cannot read {auth}. Run `codex login`.") from None
        if not isinstance(data, dict) or data.get("OPENAI_API_KEY") or data.get("auth_mode") != "chatgpt":
            raise BackupError("codex: this login is an API key, which would bill the API instead of the plan; refused")
        return auth

    @contextlib.contextmanager
    def _codex_home(self, auth: Path):
        """A workspace plus a private CODEX_HOME holding only the login. The real home is where
        AGENTS.md, skills, MCP servers and config.toml live, and --ignore-user-config only skips
        config.toml: from the real home a one-line prompt cost 4,367 input tokens, from this one
        138. auth.json is a symlink so a token refresh lands in the one real file (checked with
        fake tokens and a fake server: this Codex writes through the link)."""
        kept = []
        with _workspace(keep=lambda: bool(kept)) as (root, cwd):
            home = root / "home"
            home.mkdir()
            link = home / "auth.json"
            link.symlink_to(auth)
            installation = auth.with_name("installation_id")
            if installation.exists():
                (home / "installation_id").symlink_to(installation)
            try:
                yield root, cwd, home
            finally:
                # If a later Codex replaces the link instead, that file holds the only live
                # refresh token, because the old one is spent. It must outlive this directory.
                if link.is_file() and not link.is_symlink() and not self._save_replaced_login(link, auth):
                    kept.append(root)

    def _save_replaced_login(self, link: Path, auth: Path) -> bool:
        """Move a replaced login file back. If that fails (a full disk, say), it stays where it is,
        because it may hold the only live refresh token, and no further Codex call spends the old
        one from the real file."""
        try:
            _replace_private(auth, link.read_bytes())
            return True
        except OSError:
            for leftover in ("system.txt", "catalog.json"):       # only the login needs to stay
                with contextlib.suppress(OSError):
                    (link.parents[1] / leftover).unlink()
            self._codex_stopped = f"codex: a newer login could not be saved to {auth}; see {link}"
            print(f"council: could not update {auth}. A newer Codex login is saved at {link} . "
                  "Copy it over that file, or run `codex login`.", file=sys.stderr)
            return False

    @staticmethod
    def _codex_flags(root: Path, *, effort=None) -> list[str]:
        settings = [f"model_instructions_file={json.dumps(str(root / 'system.txt'))}",
                    f"model_catalog_json={json.dumps(str(root / 'catalog.json'))}",
                    'web_search="disabled"', 'approval_policy="never"', "project_doc_max_bytes=0",
                    "skills.bundled.enabled=false", "include_environment_context=false",
                    "include_permissions_instructions=false", "include_apps_instructions=false",
                    "include_collaboration_mode_instructions=false",
                    "tools.experimental_request_user_input.enabled=false", "tools.update_plan.enabled=false"]
        if effort:
            settings.append(f"model_reasoning_effort={json.dumps(effort)}")
        flags = [part for value in settings for part in ("-c", value)]
        return flags + [part for feature in CODEX_DISABLED_FEATURES for part in ("--disable", feature)]

    def _codex_entry(self, tool_path, auth, model) -> dict:
        """This model's catalog entry with the fields that grant tools removed."""
        with self._prepare_lock:
            if self._catalog is None:
                def read_catalog():
                    with self._codex_home(auth) as (_root, cwd, home):
                        return self._call("codex", _DEBUG_TIMEOUT, [tool_path, "debug", "models"], input="",
                                          env=self._child_env(tool_path, CODEX_HOME=str(home)), cwd=cwd)
                done = self._alone_if_refresh_due(auth, _DEBUG_TIMEOUT, read_catalog)
                try:
                    self._catalog = {m["slug"]: m for m in json.loads(done.stdout)["models"]}
                except (ValueError, KeyError, TypeError):
                    raise BackupError(self._explain("codex", done.stderr or "unreadable model catalog")) from None
        entry = self._catalog.get(model)
        if entry is None:
            raise BackupError(f"codex: model {model} is not in this tool's model catalog; "
                              "change it under [settings.backup] in panels.toml")
        entry = dict(entry)
        for name in ("tool_mode", "multi_agent_version", "multi_agent_reasoning_effort",
                     "apply_patch_tool_type", "web_search_tool_type"):
            entry.pop(name, None)
        entry.update(shell_type="disabled", experimental_supported_tools=[], supports_search_tool=False)
        return entry

    def _prove_codex(self, tool_path, auth, route, entry) -> None:
        """Before the first model call on each model, ask Codex what the model would see. It runs
        locally and costs nothing. A Codex update that brings tools back stops here instead of
        reaching a review. The result is kept, so a refusal repeats without another look."""
        with self._prepare_lock:
            if route.model not in self._proved:
                self._proved[route.model] = self._inspect_codex(tool_path, auth, route, entry)
            problem = self._proved[route.model]
        if problem:
            raise BackupError(problem)

    def _inspect_codex(self, tool_path, auth, route, entry) -> str | None:
        def preview():
            with self._codex_home(auth) as (root, cwd, home):
                (root / "system.txt").write_text(_PREVIEW_SYSTEM, encoding="utf-8")
                (root / "catalog.json").write_text(json.dumps({"models": [entry]}), encoding="utf-8")
                argv = [tool_path, "debug", "prompt-input", *self._codex_flags(root, effort=route.effort), "-c",
                        f"model={json.dumps(route.model)}", _PREVIEW_USER]
                return self._call("codex", _DEBUG_TIMEOUT, argv, input="",
                                  env=self._child_env(tool_path, CODEX_HOME=str(home)), cwd=cwd)
        try:
            done = self._alone_if_refresh_due(auth, _DEBUG_TIMEOUT, preview)
        except BackupError as exc:
            return f"{exc}; codex isolation cannot be proved, so it was not used"
        try:
            items = json.loads(done.stdout)
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise ValueError("not a list of items")
        except ValueError:
            return "codex: could not read its prompt preview, so its isolation cannot be proved; not used"
        listed = [item.get("tools") for item in items if item.get("type") == "additional_tools"]
        if not all(isinstance(tools, list) and all(isinstance(t, dict) for t in tools) for tools in listed):
            return "codex: could not read the tool list in its prompt preview, so its isolation cannot be proved; not used"
        tools = [name for found in listed for name in _tool_names(found)]
        if tools:
            return f"codex: tools are still exposed ({', '.join(tools)[:200]}); not used"
        # Exactly the two texts it was given. Text added to either message would still reach the model.
        seen = [(item.get("role"), _message_text(item)) for item in items if item.get("type") != "additional_tools"]
        if seen != [("developer", _PREVIEW_SYSTEM), ("user", _PREVIEW_USER)]:
            return f"codex: unexpected context in the prompt preview (roles {[role for role, _ in seen]}); not used"
        return None

    def _codex(self, tool_path, route, system_text, user_text) -> str:
        auth = self._codex_login()
        entry = self._codex_entry(tool_path, auth, route.model)
        self._prove_codex(tool_path, auth, route, entry)
        return self._alone_if_refresh_due(auth, route.timeout, lambda: self._run_codex(
            tool_path, route, auth, entry, system_text, user_text))

    def _alone_if_refresh_due(self, auth: Path, wait_seconds: int, call):
        """Seats start together. A login due a refresh would have each child spend the same refresh
        token, and the last to write could leave a dead one in the real auth.json. So the first
        child runs alone, its refresh lands, and the others see a fresh token and need none. This
        wraps every Codex child, because `codex debug models` refreshes too (checked with fake
        tokens). It spans council processes but not Codex sessions started outside council."""
        if self._codex_stopped:
            raise BackupError(self._codex_stopped)
        if _codex_refresh_due(auth):
            with self._refresh_gate(auth, wait_seconds):
                if self._codex_stopped:      # the holder this call waited for may have lost the login
                    raise BackupError(self._codex_stopped)
                if _codex_refresh_due(auth):
                    return call()
        return call()

    @contextlib.contextmanager
    def _refresh_gate(self, auth: Path, wait_seconds: int):
        """A lock file beside the login it protects, polled so that a stop or a stuck holder cannot
        hang a seat. Without a usable lock the seat fails: one failed seat is cheaper than the
        owner's Codex login."""
        import fcntl
        try:
            fd = os.open(auth.resolve().with_name(".council-refresh.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise BackupError(f"codex: login is due a refresh and the refresh lock is unusable ({exc})") from None
        try:
            deadline = time.monotonic() + wait_seconds
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if _stopping.is_set() or time.monotonic() > deadline:
                        raise BackupError(f"codex: waited {wait_seconds}s for another token refresh") from None
                    time.sleep(0.1)
            yield
        finally:
            os.close(fd)              # also releases the lock

    def _run_codex(self, tool_path, route, auth, entry, system_text, user_text) -> str:
        with self._codex_home(auth) as (root, cwd, home):
            (root / "system.txt").write_text(system_text, encoding="utf-8")
            (root / "catalog.json").write_text(json.dumps({"models": [entry]}), encoding="utf-8")
            argv = [tool_path, "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                    "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
                    "-m", route.model, *self._codex_flags(root, effort=route.effort), "-"]
            done = self._call("codex", route.timeout, argv, input=user_text,
                              env=self._child_env(tool_path, CODEX_HOME=str(home)), cwd=cwd)
        return self._read_codex(done)

    def _read_codex(self, done) -> str:
        text = failed = last_error = None
        used_tools = []
        for event in _json_lines(done.stdout):
            kind = event.get("type")
            if kind in ("item.started", "item.completed"):
                item = event.get("item") or {}
                if item.get("type") == "agent_message":
                    text = item.get("text")
                elif item.get("type") not in _CODEX_PLAIN_ITEMS:
                    used_tools.append(str(item.get("type")))
            elif kind == "turn.failed":
                failed = (event.get("error") or {}).get("message") or "turn failed"
            elif kind == "error":
                last_error = event.get("message")
        if used_tools:
            raise BackupError(f"codex: child used a tool ({', '.join(sorted(set(used_tools)))}); reply refused")
        if failed or done.returncode != 0:
            raise BackupError(self._explain("codex", failed or last_error or done.stderr or f"exit {done.returncode}"))
        if not str(text or "").strip():
            raise BackupError("codex: empty reply")
        return text


# --- one run ---------------------------------------------------------------------------------

def mode_from_env(environ) -> str:
    """COUNCIL_BACKUP, defaulting to auto. A typo must not quietly mean "may spend money"."""
    mode = (environ.get("COUNCIL_BACKUP") or "auto").strip().lower()
    if mode not in MODES:
        raise ValueError(f"COUNCIL_BACKUP must be one of {', '.join(MODES)}, not {mode!r}")
    return mode


@dataclass
class Credits:
    diem: float
    reset: str | None = None


def read_credits(key: str, *, timeout: float, get=None) -> Credits:
    """Venice's balance for this exact key: the numbers differ from key to key, so a check with
    any other key says nothing about this run. Raises when it cannot be read."""
    from diem.balance import BalanceClient
    snapshot = BalanceClient(key, get=get, timeout=timeout).snapshot()
    return Credits(diem=snapshot["diem"], reset=snapshot["next_epoch"])


class _CreditWatch:
    """The balance for one run: read at the start, then again at most every _RECHECK_SECONDS. One
    read at the start cannot see credits running out, and on a key with a USD limit nothing else
    will: Venice sends no error, it just starts charging. look() holds its lock across the read and
    sets the result inside it, so no parallel call can slip past a read still in progress."""

    def __init__(self, credits, floor: float, clock):
        self._credits = credits
        self._floor = floor
        self._clock = clock
        self._lock = threading.Lock()
        self._last = clock()
        self.ran_out: Credits | None = None
        self.unreadable = False

    def read(self) -> Credits:
        return self._credits()

    def low(self, credits: Credits) -> bool:
        return credits.diem <= 0 or credits.diem < self._floor     # no credits is out, whatever the floor

    def look(self) -> None:
        with self._lock:
            if self._clock() - self._last < _RECHECK_SECONDS:
                return
            self._last = self._clock()
            try:
                credits = self._credits()
            except Exception:  # noqa: BLE001 - each caller decides what an unreadable balance means
                self.unreadable = True
                return
            self.unreadable = False
            if self.low(credits):
                self.ran_out = credits


def _reset_text(reset, now: datetime) -> str:
    """When DIEM comes back. Venice's own time if it gave one, else its documented 00:00 UTC."""
    try:
        when = datetime.fromisoformat(str(reset).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        when = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return when.strftime("%Y-%m-%d %H:%M UTC")


def _failure_text(reason: str) -> str:
    return (f"council: BACKUP MODE could not finish: {reason}. This is not a passed review. It did "
            "not fall back to paid Venice. COUNCIL_BACKUP=off is the deliberate way to pay.")


def log_run(record: dict) -> None:
    """One line in the backup log, one write, append only. Never prompts, diffs, errors or keys.
    Failing to log must never change a review."""
    try:
        path = Path(os.environ.get(LOG_ENV) or DEFAULT_LOG).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except (OSError, TypeError, ValueError):
        print("council: could not write the backup log; the result is not affected.", file=sys.stderr)


@dataclass
class SeatRow:
    label: str
    where: str
    failed: bool = False
    is_seat: bool = True


@dataclass
class BackupReport:
    """What one backup run did, for the banner and the log."""
    trigger: str
    why: str
    panel: str
    rows: list
    families: list          # the model families whose answers this report really rests on
    chair_failed: bool = False
    incomplete: str | None = None   # why this run is not a usable result; None when it is


class RunClient:
    """One CLI run's client. complete() goes to Venice until the run switches, then to the
    backup, and stays there. Engine, chair, router and gate see one ordinary client.

    Switch on: DIEM under the floor before the run (a key with a USD limit gets no error when
    DIEM hits 0, it just starts spending), force, or the first HTTP 402 during it. Every other
    Venice failure keeps today's behavior."""

    def __init__(self, venice, backup, config: BackupConfig, *, mode: str, credits=None, stderr=None,
                 command: str = "", now=None, clock=None):
        self.venice = venice
        self.backup = backup
        self.config = config
        self.mode = mode
        self.command = command
        self.trigger = None
        self.why = ""
        self._watch = _CreditWatch(credits, config.diem_floor, clock or time.monotonic) if credits else None
        self._stderr = stderr
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._where: dict[tuple, str] = {}     # (model, system) -> "Venice" or "tool/model"

    @property
    def active(self) -> bool:
        return self.trigger is not None

    def _say(self, text: str) -> None:
        print(text, file=self._stderr or sys.stderr)

    def _switch(self, trigger: str, why: str) -> None:
        with self._lock:
            if self.trigger is not None:
                return
            self.trigger, self.why = trigger, why
        self._say(f"council: BACKUP MODE on. {why}. Seats now run on the claude and codex plans.")

    def prepare(self) -> str | None:
        """The before-run check. Returns a failure message when the backup is needed and cannot
        run at all, so the caller stops before spending anything."""
        if self.mode == "force":
            self._switch("force", "COUNCIL_BACKUP=force is set")
        elif self._watch is not None:
            try:
                credits = self._watch.read()
            except Exception:  # noqa: BLE001 - a broken balance read stays on Venice; 402 still protects
                self._say("council: could not read the Venice balance; staying on Venice.")
            else:
                if self._watch.low(credits):
                    self._switch_for_diem(credits)
        if self.active and not self.backup.available_tools():
            log_run(self._log_record(panel=None, seats={}, ok=0, failed=0, chair_ok=False))
            return _failure_text("neither the claude nor the codex command was found")
        return None

    def _switch_for_diem(self, credits: Credits) -> None:
        self._switch("diem", f"Venice credits are out (DIEM {credits.diem:.2f} is under the "
                             f"{self.config.diem_floor} floor). Credits reset "
                             f"{_reset_text(credits.reset, self._now())}")

    def complete(self, model, system, user, **kwargs):
        if not self.active and self._watch is not None:
            self._watch.look()                        # an unreadable balance stays on Venice, as at the start
            if self._watch.ran_out:
                self._switch_for_diem(self._watch.ran_out)
        if not self.active:
            try:
                answer = self.venice.complete(model, system, user, **kwargs)
            except VeniceOutOfCredits:
                self._switch("402", "Venice returned HTTP 402 during this run (credits out). Credits "
                                    f"reset {_reset_text(None, self._now())}")
            else:
                with self._lock:
                    self._where[(model, system)] = _VENICE
                return answer
        route = self.backup.config.route(model)
        with self._lock:
            self._where[(model, system)] = route.label if route else "no route"
        return self.backup.complete(model, system, user, **kwargs)

    def _where_seat(self, member) -> str | None:
        # The engine adds an output format after the persona; two seats on one model differ by persona.
        with self._lock:
            for (model, system), where in self._where.items():
                if model == member.model and system.startswith(member.system):
                    return where
        return None

    def _where_model(self, model) -> str | None:
        with self._lock:
            found = [where for (m, _system), where in self._where.items() if m == model]
        return found[-1] if found else None

    def relabel(self, panel, results) -> None:
        """Show the model that really answered, in the raw panel and to the chair. A digest that
        says deepseek, grok and gemini when one family answered would misread as agreement across
        families."""
        members = {m.name: m for m in panel.members}
        for result in results:
            member = members.get(result.member)
            where = self._where_seat(member) if member else None
            if where and where != _VENICE:
                result.model = where

    def report(self, panel, results, *, chair_model, chair_failed: bool, router_model=None) -> BackupReport:
        rows = []
        router_where = self._where_model(router_model) if router_model else None
        if router_where:
            rows.append(SeatRow("Router", _display(router_where, router_model), is_seat=False))
        members = {m.name: m for m in panel.members}
        for result in results:
            member = members.get(result.member)
            where = (self._where_seat(member) if member else None) or result.model
            rows.append(SeatRow(result.member, _display(where, member.model if member else result.model),
                                failed=bool(result.error)))
        chair_where = self._where_model(chair_model)
        rows.append(SeatRow("Chair", _display(chair_where or "not reached", chair_model),
                            failed=chair_failed, is_seat=False))
        answered = [row for row in rows if not row.failed and row.label != "Router"]
        families = list(dict.fromkeys(_family(row.where) for row in answered))
        return BackupReport(trigger=self.trigger, why=self.why, panel=panel.name, rows=rows, families=families,
                            chair_failed=chair_failed, incomplete=_why_incomplete(rows, chair_failed))

    def _log_record(self, *, panel, seats, ok, failed, chair_ok) -> dict:
        return {"time": self._now().isoformat(timespec="seconds"), "trigger": self.trigger,
                "command": self.command, "panel": panel, "seats": seats, "ok": ok, "failed": failed,
                "chair_ok": chair_ok}

    def finish(self, report: BackupReport) -> str | None:
        """Write the log row. Returns the failure message when this run is not a usable result."""
        seats = [row for row in report.rows if row.is_seat]
        failed = sum(row.failed for row in seats)
        log_run(self._log_record(panel=report.panel, seats={row.label: row.where for row in report.rows},
                                 ok=len(seats) - failed, failed=failed, chair_ok=not report.chair_failed))
        return _failure_text(report.incomplete) if report.incomplete else None


def _why_incomplete(rows, chair_failed: bool) -> str | None:
    """All seats down, at least half down (the same bar run_pr_review fails closed on), or no
    chair. Exit codes keep their meaning, so none of these may read as a pass."""
    seats = [row for row in rows if row.is_seat]
    failed = sum(row.failed for row in seats)
    if not seats:
        return "the panel has no seats"
    if failed == len(seats):
        return f"all {failed} seats failed"
    if failed * 2 >= len(seats):
        return f"{failed} of {len(seats)} seats failed"
    return "the chair failed" if chair_failed else None


def _display(where: str, venice_model) -> str:
    return f"{_VENICE}/{venice_model}" if where == _VENICE else where


def _family(where: str) -> str:
    """"codex/gpt-6.1-terra" -> OpenAI, "Venice/grok-4-3" -> xAI. An unknown name stays as it is."""
    host, _, model = where.partition("/")
    name = model.split("-")[0] if host == _VENICE else host
    return _FAMILIES.get(name, name)


class CreditGuard:
    """`council sweep`: Venice or nothing. One sweep is dozens of panel runs, so it neither burns
    plan limits nor spends real money. It refuses when credits are out or cannot be confirmed, in
    every COUNCIL_BACKUP mode. Once started it reads the balance every _RECHECK_SECONDS and stops
    at the floor, a failed read or the first 402. A key with a USD limit sends no 402, so those
    reads are what protect it, and only so far: up to 30 seconds of new calls, plus calls already
    in flight, can go past the floor. The hard stop is a key whose USD limit is 0."""

    def __init__(self, venice, *, credits, floor: float, now=None, clock=None):
        self.venice = venice
        self._watch = _CreditWatch(credits, floor, clock or time.monotonic)
        self._floor = floor
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._reset = None
        self.exhausted = False

    def refusal(self) -> str | None:
        try:
            credits = self._watch.read()
        except Exception:  # noqa: BLE001
            return ("council sweep: could not read the Venice balance, so nothing shows this sweep would "
                    "stay on free credits. Not started. Try again in a few minutes.")
        if self._watch.low(credits):
            return (f"council sweep: Venice credits are out (DIEM {credits.diem:.2f} is under the "
                    f"{self._floor} floor). A sweep runs on Venice credits only: not on the backup, not on "
                    f"real money. Try again after they reset at {_reset_text(credits.reset, self._now())}.")
        return None

    def exhausted_message(self) -> str:
        if self._watch.unreadable:
            return ("council sweep: could not read the Venice balance during this sweep, so it stopped "
                    "and is incomplete. Try again in a few minutes.")
        return (f"council sweep: Venice credits ran out during this sweep, so it is incomplete. "
                f"Try again after they reset at {_reset_text(self._reset, self._now())}.")

    def complete(self, model, system, user, **kwargs):
        if not self.exhausted:
            self._watch.look()
            if self._watch.ran_out or self._watch.unreadable:     # the rule at the start holds throughout
                self._reset = self._watch.ran_out.reset if self._watch.ran_out else None
                self.exhausted = True
        if self.exhausted:
            raise VeniceOutOfCredits("Venice credits ran out earlier in this sweep")
        try:
            return self.venice.complete(model, system, user, **kwargs)
        except VeniceOutOfCredits:
            self.exhausted = True
            raise


@contextlib.contextmanager
def child_cleanup():
    """Children run in their own sessions, so a signal aimed at council does not reach them, and
    SIGTERM kills Python without running a finally block. Ctrl-C would also wait on the seat
    threads, which wait on the children. So a signal stops the children first, then the run."""
    def stop(signum, _frame):
        _stop_children()
        if getattr(_spawn_state, "active", False):
            _spawn_state.signum = signum        # run_child finishes listing the child, then exits
        else:
            raise SystemExit(128 + signum)

    previous = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            previous[sig] = signal.signal(sig, stop)
    try:
        yield
    finally:
        _stop_children()
        _stopping.clear()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _stop_children() -> None:
    _stopping.set()
    with _live_lock:
        procs = list(_live)
    for proc in procs:
        _kill_group(proc)


@contextlib.contextmanager
def _workspace(keep=lambda: False):
    """A private temp directory holding one empty `cwd`, gone afterward whatever happened, unless
    `keep` says a file in it must survive."""
    root = Path(tempfile.mkdtemp(prefix="council-backup-"))
    try:
        cwd = root / "cwd"
        cwd.mkdir()
        yield root, str(cwd)
    finally:
        if not keep():
            shutil.rmtree(root, ignore_errors=True)


def _replace_private(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".council-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _codex_refresh_due(auth: Path) -> bool:
    """Is this login near the age where Codex refreshes it? Unknown counts as yes: the price of a
    wrong yes is one seat running before the others."""
    try:
        last = json.loads(auth.read_text())["last_refresh"]
        when = datetime.fromisoformat(re.sub(r"(\.\d{6})\d*", r"\1", str(last)).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - when > timedelta(days=_CODEX_REFRESH_DAYS)
    except (OSError, ValueError, KeyError, TypeError):
        return True


def _message_text(item: dict) -> str:
    content = item.get("content")
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content)


def _json_lines(stdout):
    for line in str(stdout or "").splitlines():
        with contextlib.suppress(ValueError):
            event = json.loads(line)
            if isinstance(event, dict):
                yield event


def _tool_names(tools) -> list[str]:
    names = []
    for tool in tools:
        name = str(tool.get("name") or tool.get("type"))
        names += [f"{name}.{sub}" for sub in _tool_names(tool["tools"])] if tool.get("tools") else [name]
    return names
