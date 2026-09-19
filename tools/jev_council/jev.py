"""One Jev (TypeSafe System One) call for the offline council experiments.

Reuses the watchdog shadow's transport, key loader, pinned model and redaction, so there is
one place that talks to TypeSafe. Offline runs WANT to see failures, so unlike the shadow
this raises. The transport is injectable; tests never reach the network.
"""
from __future__ import annotations

import json
import time

from watchdog.jev_shadow import (MODEL, _EMAIL, _TOKENISH, _URL_QUERY,   # noqa: F401  (re-exported)
                                 _http_post, load_key)

TIMEOUT_SECONDS = 30
RETRY_STATUS_WORDS = ("429", "529", "timed out")


class JevError(RuntimeError):
    pass


def redact_text(text: str) -> str:
    """Council prose and code, whole. Only addresses, URL query strings and long token-shaped
    strings go. (The watchdog's log redaction also clips lines at 300 characters and strips
    @handles; a recommendation is one long line and code has decorators.)"""
    text = _EMAIL.sub("<email>", text)
    text = _URL_QUERY.sub(r"\1?<query removed>", text)
    return _TOKENISH.sub("<token>", text)


def _redact_state(state):
    if isinstance(state, str):
        return redact_text(state)
    if isinstance(state, dict):
        return {k: _redact_state(v) for k, v in state.items()}
    if isinstance(state, list):
        return [_redact_state(v) for v in state]
    return state


def ask(state, questions: dict, *, key: str, transport=_http_post, retries: int = 2) -> dict:
    # Questions carry review text too (a link request lists findings as its options), so the
    # whole request is redacted. Dict KEYS are option ids and question names: left alone.
    req = {"state": _redact_state(state), "model": MODEL, "questions": _redact_state(questions)}
    started, last = time.perf_counter(), None
    for attempt in range(retries + 1):
        try:
            reply = transport(req, key, TIMEOUT_SECONDS)
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries and any(w in str(exc) for w in RETRY_STATUS_WORDS):
                time.sleep(2 * (attempt + 1))
                continue
            raise JevError(f"Jev call failed: {type(exc).__name__}: {str(exc)[:200]}") from exc
    else:  # pragma: no cover
        raise JevError(f"Jev call failed: {last}")
    if reply.get("model") != MODEL:
        raise JevError(f"answer came from model {reply.get('model')!r}, expected {MODEL}")
    return {"answers": reply.get("answers") or {},
            "input_tokens": (reply.get("usage") or {}).get("input_tokens") or 0,
            "seconds": round(time.perf_counter() - started, 3),
            "request_bytes": len(json.dumps(req))}
