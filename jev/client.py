"""The one place that talks to TypeSafe's Jev. Contract: docs/contracts/jev.md.

Jev is a typed judge, not a writer: it answers Choice / Score / Noul questions about a
`state` and nothing else. Use it as a plug-in inside a process (a gate before an expensive
call, a router, a checker, a sorter), never as the last safety check and never to approve
anything. Measure on history with known answers before wiring it in.

    import jev
    got = jev.ask(state, {"keep": jev.noul("Is ...?", true="...", false="...")},
                  project="ultimate-portugal", task="story-prefilter")
    p = got["answers"]["keep"]["noul"]

`ask` raises JevError. `try_ask` returns None instead: the fail-open form for live processes.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import usage

URL = "https://api.typesafe.ai/v1/systemone"
# Pinned on purpose. `jev-latest` moves, and every threshold in every caller was measured on
# one version. Bump this only with a re-measure; a caller with a measured gate should also
# pass its own `model=` so a bump here cannot move its line silently.
MODEL = "jev-1.13.0"
PRICE_PER_MTOK_USD = 0.042        # input tokens; output is free (docs.typesafe.ai/models, 2026-09-18)
ENV_FILE = Path.home() / ".env"
TIMEOUT_SECONDS = 30
RETRY_STATUSES = (429, 500, 502, 503, 529)


class JevError(RuntimeError):
    """Any failure to get a usable answer. The message never contains the key."""


class JevDisabled(JevError):
    """JEV_DISABLED=1: the global off switch. Set for every test run in tests/conftest.py."""


class TransientError(Exception):
    """Raised by a transport for a failure worth retrying (429, 5xx, 529, timeout)."""


def disabled() -> bool:
    return os.environ.get("JEV_DISABLED", "") not in ("", "0")


# --- question builders -------------------------------------------------------

def noul(instructions, *, true: str | None = None, false: str | None = None) -> dict:
    """Yes/no. The answer is the probability of yes. Put the boundary cases in true/false:
    Jev answers the words you wrote, not what you meant."""
    q: dict = {"type": "noul", "instructions": instructions}
    criteria = {k: v for k, v in (("true", true), ("false", false)) if v}
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions, options: dict) -> dict:
    """One of a fixed set: {option: description or None}. Include a no-match option when
    nothing may fit; Jev cannot pick a value you did not offer."""
    if len(options) < 2:
        raise ValueError("a choice needs at least two options")
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def score(instructions, levels: list[str]) -> dict:
    """A rating over ordered levels, each a concrete situation that stands on its own."""
    if len(levels) < 2:
        raise ValueError("a score needs at least two levels")
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


# --- key and transport -------------------------------------------------------

def load_key(env_file=None) -> str | None:
    """TYPESAFE_API_KEY from the environment, else from ~/.env read as text (no shell)."""
    value = os.environ.get("TYPESAFE_API_KEY")
    if value:
        return value
    try:
        for line in Path(env_file or ENV_FILE).read_text().splitlines():
            if line.startswith("TYPESAFE_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'") or None
    except OSError:
        pass
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib re-sends the Authorization header on a redirect. Refuse them all."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def http_post(req: dict, key: str, timeout: float) -> dict:
    """The real transport. Tests pass their own `transport=`; nothing else should."""
    request = urllib.request.Request(
        URL, data=json.dumps(req).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "ai-harness-jev/1"})
    try:
        with _OPENER.open(request, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as err:
        detail = f"HTTP {err.code}: {err.read().decode(errors='replace')[:200]}"
        raise (TransientError(detail) if err.code in RETRY_STATUSES else RuntimeError(detail)) from None
    except (TimeoutError, urllib.error.URLError) as err:
        raise TransientError(f"network: {err}") from None


# --- ask ---------------------------------------------------------------------

def ask(state, questions: dict, *, project: str, task: str, model: str | None = None,
        key: str | None = None, timeout: float = TIMEOUT_SECONDS, retries: int = 3,
        transport=None) -> dict:
    """One call. Returns {answers, input_tokens, cost_usd, seconds, model}. Raises JevError."""
    if disabled():
        raise JevDisabled("JEV_DISABLED is set: no call was made")
    model = model or MODEL
    key = key or load_key()
    if not key:
        raise JevError("no TYPESAFE_API_KEY in the environment or ~/.env")
    req = {"state": state, "model": model, "questions": questions}
    started = time.perf_counter()
    try:
        reply = None
        for attempt in range(retries + 1):
            try:
                reply = (transport or http_post)(req, key, timeout)
                break
            except TransientError as err:
                if attempt == retries:
                    raise JevError(f"Jev call failed after {retries + 1} tries: {err}") from None
                time.sleep(1.5 * 2 ** attempt)
        if not isinstance(reply, dict) or reply.get("model") != model:
            got = reply.get("model") if isinstance(reply, dict) else type(reply).__name__
            raise JevError(f"answer came from {got!r}, not the pinned {model}")
        answers = reply.get("answers") or {}
        missing = [name for name in questions if name not in answers]
        if missing:
            raise JevError(f"reply is missing answers for: {', '.join(missing)}")
        tokens = int((reply.get("usage") or {}).get("input_tokens") or 0)
        result = {"answers": answers, "input_tokens": tokens,
                  "cost_usd": tokens * PRICE_PER_MTOK_USD / 1e6,
                  "seconds": round(time.perf_counter() - started, 3), "model": model}
    except JevError:
        usage.record(project, task, model, ok=False, seconds=time.perf_counter() - started)
        raise
    except Exception as err:  # noqa: BLE001 - one error type out, and never the key in it
        usage.record(project, task, model, ok=False, seconds=time.perf_counter() - started)
        raise JevError(f"Jev call failed: {type(err).__name__}: {str(err).replace(key, '<key>')[:200]}") from None
    usage.record(project, task, model, ok=True, input_tokens=tokens,
                 cost_usd=result["cost_usd"], seconds=result["seconds"])
    return result


def try_ask(state, questions: dict, **kwargs) -> dict | None:
    """Fail-open form for live processes: None on ANY failure, including the off switch.
    The caller then does what it did before Jev existed."""
    try:
        return ask(state, questions, **kwargs)
    except Exception:  # noqa: BLE001
        return None


def ask_many(items: list[dict], questions, *, project: str, task: str, workers: int = 8, **kwargs) -> list[dict]:
    """items: [{"id", "state", ...}]. `questions` is a dict, or a function item -> dict when
    the options differ per item. Order is kept. A failure is {"id", "error"} in its place."""
    def one(item):
        try:
            qs = questions(item) if callable(questions) else questions
            return {"id": item["id"], **ask(item["state"], qs, project=project, task=task, **kwargs)}
        except Exception as err:  # noqa: BLE001
            return {"id": item.get("id"), "error": str(err)[:300]}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, items))
