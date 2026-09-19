"""The council's thin layer over the shared Jev client (the `jev` package, jev/client.py).

Jev is TypeSafe's small typed judge: it answers a yes/no, a choice or a score about the text
it is given. The council uses it for label-and-score jobs AROUND a review, never to review.
Background: docs/jev-council-offline-test-2026-09-19.md. What is wired in and what is not:
docs/council-jev-shadow-2026-09-19.md. The shared client's rules: docs/contracts/jev.md.

One door: every call here goes through `jev.ask` in the shared client, so there is no second
HTTP client, the shared off switch (JEV_DISABLED=1) works, and each call lands in the shared
usage ledger as project "ai-harness". This module adds what is the council's own:

  * its own model pin (contract rule 6: the 0.85 cut was measured on this version),
  * redaction of the WHOLE request, state and questions, before it is handed over,
  * a short timeout and no retries, because a review's time budget is small,
  * the council's data-scope rule by REPOSITORY: an allow list, so an unknown repo is refused,
  * the council's own kill switch, COUNCIL_JEV=0.

`tools/jev_council` imports from here; nothing here imports from `tools/` or `watchdog/`,
because those are not installed packages. (`jev` is one.)
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import jev as _shared                                  # the top-level shared package, not this module
from jev import client as _client
from jev.redact import redact_state as redact, redact_text   # noqa: F401  (re-exported)
from jev.scope import IN_SCOPE_REPOS, OUT_OF_SCOPE     # noqa: F401  one allow list and one word list for every caller

MODEL = "jev-1.13.0"            # the council's own pin; passed on every call
TIMEOUT_SECONDS = 8             # the most one call may wait; a caller with less time left passes less
GIT_PROBE_SECONDS = 3           # "which repo is this?" must never stall a review; a timeout means "unknown"
PROJECT = "ai-harness"          # how these calls are named in the shared usage ledger
TASK = "council-shadow"
KILL_SWITCH = "COUNCIL_JEV"     # "0", "off" or "false" turns every council use of Jev off
_OFF = ("0", "off", "false")

JevError = _shared.JevError
_http_post = _client.http_post  # kept for tools/jev_council, which passes it as a transport

# Owner data rule for COUNCIL work (2026-09-19): council text, code snippets and diffs may go
# to TypeSafe (recorded in jev/scope.py ALLOWED_NOTE). Scope is an ALLOW list, because a deny
# list is not fail-closed: a new private repo would be in scope until someone remembered to
# add its name.
#
# The list itself is IN_SCOPE_REPOS in jev/scope.py, imported above and re-exported under the
# same name: ONE list for every caller that describes a repository's work to Jev (the backlog
# hold gate follows it too). These are the five repos in the 2026-09-19 offline test dataset
# (docs/jev-council-offline-test-2026-09-19.md): the repos the signals were measured on.
# Adding a repo is an OWNER decision. Extend it there, deliberately; tests pin the list.

# A second, explicit refusal (belt and braces): a name here is refused even if it also lands
# on the allow list by mistake, and the list documents two decisions. (1) Repos that hold
# student, customer, mail, tax or finance data. (2) Unpublished manuscripts, so every romance
# repo: a review of a chapter quotes the plot, and the council decision did not name them.
# Extend it; never trim it without the owner.
OUT_OF_SCOPE_REPOS = frozenset({
    "sat-prep", "tax-advisor", "finance-tracker", "swimtrack-coach", "monthly-bidding", "nato-support",
    "romance-empire", "romance-tessacross.com", "romance-elliecalloway.com", "flight-7-publishing",
    "rmpeacockwriter.com"})


def in_scope(name: str | None, repo: str | None) -> bool:
    """May council text from this repository go to TypeSafe? Only when the repository is on
    the allow list (exact name), is not on the explicit refusal list, and none of the shared
    out-of-scope words appears in the repo name or in `name`: whatever else identifies the
    work, such as a backlog item id or a review id. No repo, or an unknown repo: no."""
    if not repo or repo not in IN_SCOPE_REPOS or repo in OUT_OF_SCOPE_REPOS:
        return False
    low = f"{name or ''} {repo}".lower()
    return not any(word in low for word in OUT_OF_SCOPE)


def ask(state, questions: dict, *, key: str, transport=None, task: str = TASK,
        timeout: float | None = None) -> dict:
    """One Jev call through the shared client. Returns the `answers` object. Raises JevError
    on ANY failure, and when the answer came from a different model version. The whole
    request is redacted first. `timeout` is the time this call may take; it is capped at
    TIMEOUT_SECONDS, so a caller near its deadline hands over what is left. `transport` is
    for tests; the default is the shared client's, looked up at call time."""
    seconds = TIMEOUT_SECONDS if timeout is None else max(0.0, min(TIMEOUT_SECONDS, timeout))
    try:
        got = _shared.ask(redact(state), redact(questions), project=PROJECT, task=task, model=MODEL,
                          key=key, timeout=seconds, retries=0, transport=transport)
    except JevError:
        raise                                   # the shared client already scrubbed the key
    except Exception as exc:  # noqa: BLE001
        raise JevError(f"Jev call failed: {type(exc).__name__}") from None
    answers = got.get("answers")
    if not isinstance(answers, dict):
        raise JevError("the reply carries no answers object")
    return answers


def _home(environ) -> Path:
    home = environ.get("HOME")
    return Path(home) if home else Path.home()      # Path.home() falls back to the passwd entry


def load_key(env_file=None, environ=None) -> str | None:
    """TYPESAFE_API_KEY from the environment, else from ~/.env read as text (no shell): the
    shared client's loader. Given an explicit `environ`, that mapping alone decides (its
    TYPESAFE_API_KEY, else the .env file under its HOME), so a caller or a test can say
    exactly which environment counts."""
    if environ is None or environ is os.environ:
        return _client.load_key(env_file)
    value = environ.get("TYPESAFE_API_KEY")
    if value:
        return value
    try:
        for line in Path(env_file or _home(environ) / ".env").read_text().splitlines():
            if line.startswith("TYPESAFE_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'") or None
    except OSError:
        pass
    return None


def switched_off(environ=None) -> bool:
    """COUNCIL_JEV=0 (or off/false): the council's switch. JEV_DISABLED=1: the shared one."""
    environ = os.environ if environ is None else environ
    return (str(environ.get(KILL_SWITCH, "")).strip().lower() in _OFF
            or str(environ.get("JEV_DISABLED", "")) not in ("", "0"))


def shadow_enabled(environ=None) -> bool:
    """Default on when a key exists. Either switch turns it off."""
    environ = os.environ if environ is None else environ
    return not switched_off(environ) and bool(load_key(environ=environ))


_GIT_LOCATION_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")


def resolve_repo(path) -> tuple[str, str | None]:
    """Which repository holds `path`? Three answers, because "no repository" and "cannot
    tell" must never look the same:

      ("repo", name)     the MAIN checkout's directory name, also when `path` is inside a
                         linked worktree (whose own folder is named after a session) or is
                         reached through a symlink
      ("outside", None)  git itself said the path is not in a git repository
      ("unknown", None)  it could not be resolved: git missing, the probe timed out, a
                         permission or ownership error, an unreadable path, an odd layout

    Callers refuse on "unknown". Only scope_repo() treats "outside" differently."""
    try:
        target = Path(os.path.realpath(path))
        cwd = target if target.is_dir() else target.parent
        env = {k: v for k, v in os.environ.items() if k not in _GIT_LOCATION_VARS}
        env["LC_ALL"] = "C"                         # the refusal below is matched on git's English text
        proc = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
                              capture_output=True, text=True, timeout=GIT_PROBE_SECONDS, env=env)
        out = (proc.stdout or "").strip()
        if proc.returncode != 0:
            not_a_repo = proc.returncode == 128 and "not a git repository" in (proc.stderr or "").lower()
            return ("outside", None) if not_a_repo else ("unknown", None)
        if not out:
            return "unknown", None
        common = Path(out)
        if not common.is_absolute():
            common = cwd / common
        common = Path(os.path.realpath(common))
        if common.name == ".git" and common.parent.name:
            return "repo", common.parent.name
        if common.name.endswith(".git") and common.name != ".git":      # a bare repository
            return "repo", common.name[:-len(".git")]
        return "unknown", None
    except Exception:  # noqa: BLE001 - not knowing the repo means "refuse", never a crash
        return "unknown", None


def repo_name(path) -> str | None:
    """The repository's name for `path` (see resolve_repo), or None when the path is not in
    a repository or the name cannot be told. None always means: refuse."""
    status, name = resolve_repo(path)
    return name if status == "repo" else None


def scope_repo(cwd, path=None) -> str | None:
    """The repository a `council` command's input belongs to, or None to refuse. Every
    repository the input can be tied to must be in scope: the working directory's, and the
    explicit path's when it lies in one.

    Fails closed. A path that git says is OUTSIDE any repository (a diff saved under /tmp) is
    judged by the working directory alone. A path or working directory whose repository
    could NOT be resolved is refused: there is no fallback to the other one."""
    cwd_status, cwd_repo = resolve_repo(cwd)
    if cwd_status == "unknown":
        return None
    names = [cwd_repo] if cwd_repo else []
    if path not in (None, "", "-"):
        path_status, path_repo = resolve_repo(path)
        if path_status == "unknown":
            return None
        if path_repo:
            names.insert(0, path_repo)
    if not names or not all(in_scope("", name) for name in names):
        return None
    return names[0]
