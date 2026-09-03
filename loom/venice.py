# loom/venice.py
"""Thin Venice chat client (DIEM backend), mirroring council/venice.py. The API key
rides ONLY in the Authorization header — never in the prompt — and is scrubbed from
any outbound text as defense-in-depth. `post` is injectable for tests."""
from __future__ import annotations

import time
from typing import Callable, Optional

import requests

VENICE_API = "https://api.venice.ai/api/v1/chat/completions"
_RETRYABLE = {429, 500, 502, 503, 504}


class VeniceError(RuntimeError):
    pass


class VeniceClient:
    def __init__(self, api_key: str, *, base_url: str = VENICE_API, timeout: int = 180,
                 retries: int = 2, backoff: float = 1.5, temperature: float = 0.2,
                 post: Optional[Callable] = None) -> None:
        if not api_key:
            raise VeniceError("no Venice key set (tried VENICE_LOOM_KEY, VENICE_API_KEY)")
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.temperature = temperature
        self._post = post or requests.post

    def _scrub(self, text: str) -> str:
        if text and self.api_key:
            return text.replace(self.api_key, "<redacted>")
        return text

    def _log_usage(self, data: dict, model: str, task: str = "weave") -> None:
        # Usage logging must never break or slow the Venice call — swallow everything.
        try:
            import venice_usage
            usage = data.get("usage") or {} if isinstance(data, dict) else {}
            venice_usage.append(
                project="loom",
                task_type=task,
                model=model,
                tokens_in=usage.get("prompt_tokens") or 0,
                tokens_out=usage.get("completion_tokens") or 0,
                source="loom/venice",
            )
        except Exception:
            pass

    def complete(self, model: str, system: str, user: str, *, json_mode: bool = False,
                 task: str = "weave") -> str:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._scrub(system)},
                {"role": "user", "content": self._scrub(user)},
            ],
            "temperature": self.temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last = None
        for attempt in range(self.retries + 1):
            try:
                r = self._post(self.base_url, headers=headers, json=payload, timeout=self.timeout)
            except Exception as e:                       # network — retryable
                last = e
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
                continue
            status = getattr(r, "status_code", 200)
            if status in _RETRYABLE:
                last = VeniceError(f"HTTP {status}")
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
                continue
            try:
                r.raise_for_status()
            except Exception as e:
                raise VeniceError(f"Venice HTTP {status} (not retryable): {e}") from e
            try:
                data = r.json()
                content = data["choices"][0]["message"]["content"]
            except Exception as e:
                raise VeniceError(f"Venice returned an unparseable response: {e}") from e
            self._log_usage(data, model, task)
            return content
        raise VeniceError(f"Venice call failed after {self.retries + 1} tries: {last}")
