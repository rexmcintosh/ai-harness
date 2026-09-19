"""Jev (TypeSafe System One) client for the council's shadow signals.

Jev is a small typed judge: it answers a yes/no, a choice or a score about the text it is
given. The council uses it for label-and-score jobs AROUND the review, never to review.
Background: docs/jev-council-offline-test-2026-09-19.md. What is wired in and what is not:
docs/council-jev-shadow-2026-09-19.md.

This module is the single place the packaged code talks to TypeSafe, and the single home of
the data-scope rule. `tools/jev_council` imports from here; nothing here imports from
`tools/` or `watchdog/`, because those are not installed packages.

Contract:
  * The model version is pinned. An answer from any other version is refused.
  * The WHOLE request is redacted before it leaves: state and questions.
  * Redirects are never followed (urllib would re-send the Authorization header).
  * Scope fails closed: an unknown repository is refused.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"            # exact version; a moving alias would shift the numbers under us
TIMEOUT_SECONDS = 8
KILL_SWITCH = "COUNCIL_JEV"     # "0", "off" or "false" turns every council use of Jev off
_OFF = ("0", "off", "false")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL_QUERY = re.compile(r"(https?://[^\s?\"']+)\?[^\s\"']+")
_TOKENISH = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")

# Owner data rule (2026-09-19): council text, code snippets and diffs may go to TypeSafe for
# council work. Two things stay out. (1) Repos that hold student, customer, mail, tax or
# finance data. (2) Unpublished manuscripts, so every romance repo: that category needs its
# own decision, and a review of a chapter quotes the plot. Extend these; never trim them.
OUT_OF_SCOPE = ("sat-prep", "attainprep", "bento", "bebop", "tax", "finance", "rent",
                "swimtrack-coach", "gmail", "mail")
OUT_OF_SCOPE_REPOS = frozenset({
    "sat-prep", "tax-advisor", "finance-tracker", "swimtrack-coach", "monthly-bidding", "nato-support",
    "romance-empire", "romance-tessacross.com", "romance-elliecalloway.com", "flight-7-publishing",
    "rmpeacockwriter.com"})


class JevError(RuntimeError):
    pass


def in_scope(name: str | None, repo: str | None) -> bool:
    """May text from this repository go to TypeSafe? `name` is whatever else identifies the
    work (a backlog item id, a review id); it is checked for the same words. No repo, no."""
    if not repo or repo in OUT_OF_SCOPE_REPOS:
        return False
    low = f"{name or ''} {repo}".lower()
    return not any(word in low for word in OUT_OF_SCOPE)


def redact_text(text: str) -> str:
    """Council prose and code, whole: no line clipping (a recommendation is one long line)
    and no @handle stripping (code has decorators). Only addresses, URL query strings and
    long token-shaped strings go."""
    text = _EMAIL.sub("<email>", text)
    text = _URL_QUERY.sub(r"\1?<query removed>", text)
    return _TOKENISH.sub("<token>", text)


def redact(value):
    """Every string VALUE inside a request part. Dict keys are option ids and question
    names, so they are left alone."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib re-sends the Authorization header on a redirect. Refuse them all."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _http_post(req: dict, key: str, timeout: float) -> dict:
    request = urllib.request.Request(
        URL, data=json.dumps(req).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "ai-harness-council-shadow/1"})
    with _OPENER.open(request, timeout=timeout) as resp:
        return json.loads(resp.read())


def ask(state, questions: dict, *, key: str, transport=None) -> dict:
    """One Jev call. Returns the `answers` object. Raises JevError on ANY failure, and when
    the answer came from a different model version. `transport` is injectable; the default
    is looked up at call time so a test can replace it for a whole code path."""
    req = {"state": redact(state), "model": MODEL, "questions": redact(questions)}
    try:
        reply = (transport or _http_post)(req, key, TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)[:200].replace(key, "<key>") if key else str(exc)[:200]
        raise JevError(f"Jev call failed: {type(exc).__name__}: {detail}") from None
    if not isinstance(reply, dict) or reply.get("model") != MODEL:
        got = reply.get("model") if isinstance(reply, dict) else type(reply).__name__
        raise JevError(f"answer came from model {got!r}, expected {MODEL}")
    answers = reply.get("answers")
    if not isinstance(answers, dict):
        raise JevError("the reply carries no answers object")
    return answers


def _home(environ) -> Path:
    home = environ.get("HOME")
    return Path(home) if home else Path.home()      # Path.home() falls back to the passwd entry


def load_key(env_file=None, environ=None) -> str | None:
    """TYPESAFE_API_KEY from the environment, else from ~/.env read as text (no shell)."""
    environ = os.environ if environ is None else environ
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
    environ = os.environ if environ is None else environ
    return str(environ.get(KILL_SWITCH, "")).strip().lower() in _OFF


def shadow_enabled(environ=None) -> bool:
    """Default on when a key exists. COUNCIL_JEV=0 (or off/false) turns it off."""
    environ = os.environ if environ is None else environ
    return not switched_off(environ) and bool(load_key(environ=environ))


_GIT_LOCATION_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")


def repo_name(path) -> str | None:
    """The repository's directory name for `path`: the MAIN checkout's name, also when
    `path` is inside a linked worktree (whose own folder is named after a session, not the
    repo). None when `path` is not in a git repository or the name cannot be told."""
    try:
        target = Path(os.path.realpath(path))
        cwd = target if target.is_dir() else target.parent
        env = {k: v for k, v in os.environ.items() if k not in _GIT_LOCATION_VARS}
        proc = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
                              capture_output=True, text=True, timeout=10, env=env)
        out = proc.stdout.strip()
        if proc.returncode != 0 or not out:
            return None
        common = Path(out)
        if not common.is_absolute():
            common = cwd / common
        common = Path(os.path.realpath(common))
        if common.name == ".git":
            return common.parent.name or None
        if common.name.endswith(".git"):            # a bare repository
            return common.name[:-len(".git")] or None
        return None
    except Exception:  # noqa: BLE001 - not knowing the repo means "refuse", never a crash
        return None


def scope_repo(cwd, path=None) -> str | None:
    """The repository a `council` command's input belongs to, or None to refuse. Every
    repository the input can be tied to must be in scope: the working directory's, and the
    explicit path's when it lies in one. A path outside any repository (a diff saved under
    /tmp) is judged by the working directory alone."""
    cwd_repo = repo_name(cwd)
    path_repo = repo_name(path) if path not in (None, "", "-") else None
    known = [r for r in (path_repo, cwd_repo) if r]
    if not known or not all(in_scope("", r) for r in known):
        return None
    return known[0]
