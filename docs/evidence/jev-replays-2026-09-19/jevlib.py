"""Shared Jev helper for replay experiments (the prototype of ai-harness `jev`).

    from jevlib import noul, choice, score, ask_many

    qs = {"keep": noul("Is this ...?", true="...", false="...")}
    results = ask_many([{"id": "a", "state": {...}}, ...], qs)   # [{id, answers, input_tokens, seconds} | {id, error}]

Rules baked in: the model version is pinned, a reply from any other version is an error,
redirects are refused, the key is read from the environment or ~/.env and never printed,
429/529/5xx are retried with backoff, and every call's tokens are counted.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
PRICE_PER_MTOK = 0.042           # input tokens; output is free (docs /models, 2026-09-18)
STATE_TOKEN_LIMIT = 32000        # state plus the longest question


def noul(instructions, *, true=None, false=None):
    q = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {k: v for k, v in (("true", true), ("false", false)) if v}
    return q


def choice(instructions, options: dict):
    """options: {option: description or None}. Include a no-match option when nothing may fit."""
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions, levels: list[str]):
    """levels: ordered, each a concrete situation that stands on its own. At least two."""
    return {"type": "score", "instructions": instructions, "criteria": levels}


def load_key() -> str:
    value = os.environ.get("TYPESAFE_API_KEY")
    if value:
        return value
    for line in (Path.home() / ".env").read_text().splitlines():
        if line.startswith("TYPESAFE_API_KEY="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("TYPESAFE_API_KEY not found in the environment or ~/.env")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def ask(state, questions: dict, *, key: str, timeout: float = 30, retries: int = 4) -> dict:
    body = json.dumps({"state": state, "model": MODEL, "questions": questions}).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "jevlab/0.1"})
        started = time.perf_counter()
        try:
            with _OPENER.open(req, timeout=timeout) as resp:
                reply = json.loads(resp.read())
            if reply.get("model") != MODEL:
                return {"error": f"reply from {reply.get('model')}, not {MODEL}"}
            return {"answers": reply["answers"], "input_tokens": reply["usage"]["input_tokens"],
                    "seconds": round(time.perf_counter() - started, 3)}
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 529) and attempt < retries:
                time.sleep(1.5 * 2 ** attempt)
                continue
            return {"error": f"HTTP {err.code}: {err.read().decode(errors='replace')[:200]}"}
        except Exception as exc:  # noqa: BLE001
            if attempt < retries:
                time.sleep(1.5 * 2 ** attempt)
                continue
            return {"error": str(exc)[:200]}


def ask_many(items: list[dict], questions, *, workers: int = 8) -> list[dict]:
    """items: [{"id", "state"}]. `questions` is a dict, or a function item -> dict for per-item questions."""
    key = load_key()

    def one(item):
        qs = questions(item) if callable(questions) else questions
        return {"id": item["id"], **ask(item["state"], qs, key=key)}

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(one, items))
    ok = [r for r in out if "error" not in r]
    tokens = sum(r["input_tokens"] for r in ok)
    print(f"[jevlib] {len(ok)} ok, {len(out) - len(ok)} errors, {time.perf_counter() - started:.0f}s, "
          f"{tokens} tokens, ${tokens * PRICE_PER_MTOK / 1e6:.4f}")
    return out
