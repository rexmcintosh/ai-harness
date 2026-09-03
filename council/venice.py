from __future__ import annotations
import time
import requests

VENICE_API = "https://api.venice.ai/api/v1/chat/completions"

# Only these are worth retrying — a 4xx (bad model name, auth, bad request) will
# fail identically every time, so retrying just burns time and billing.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class VeniceError(RuntimeError):
    pass


class VeniceClient:
    """Thin Venice chat client. `post` is injectable for tests."""

    def __init__(self, api_key, *, base_url=VENICE_API, timeout=180,
                 retries=2, backoff=1.5, post=None, temperature=0.2):
        if not api_key:
            raise VeniceError("no Venice key set (tried VENICE_COUNCIL_KEY, VENICE_API_KEY)")
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.temperature = temperature
        self._post = post or requests.post

    def _scrub(self, text):
        # Defense in depth: never let our own API key ride along in a prompt,
        # however it got into the context (e.g. `ask --file .env`, a diff that
        # includes the key). This is the single chokepoint every call passes.
        if text and self.api_key:
            return text.replace(self.api_key, "<redacted>")
        return text

    def _log_usage(self, data, model, task_type):
        # Usage logging must never break or slow the Venice call — any failure here
        # (ledger package missing, malformed usage block, disk full, ...) is swallowed.
        try:
            import venice_usage
            usage = data.get("usage") or {} if isinstance(data, dict) else {}
            venice_usage.append(
                project="council",
                task_type=task_type,
                model=model,
                tokens_in=usage.get("prompt_tokens") or 0,
                tokens_out=usage.get("completion_tokens") or 0,
                source="council/venice",
            )
        except Exception:
            pass

    def complete(self, model, system, user, *, json_mode=True, task_type="chat"):
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
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        last = None
        for attempt in range(self.retries + 1):
            try:
                r = self._post(self.base_url, headers=headers, json=payload,
                               timeout=self.timeout)
            except Exception as e:  # network/connection/timeout — retryable
                last = e
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
                continue
            status = getattr(r, "status_code", 200)
            if status in _RETRYABLE_STATUS:
                last = VeniceError(f"HTTP {status}")
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
                continue
            # Non-retryable: a 2xx success, or a 4xx that won't change on retry.
            try:
                r.raise_for_status()
            except Exception as e:  # noqa: BLE001
                raise VeniceError(f"Venice HTTP {status} (not retryable): {e}") from e
            try:
                data = r.json()
                content = data["choices"][0]["message"]["content"]
            except Exception as e:  # noqa: BLE001 — malformed envelope won't fix on retry
                raise VeniceError(f"Venice returned an unparseable response: {e}") from e
            self._log_usage(data, model, task_type)
            return content
        raise VeniceError(f"Venice call failed after {self.retries + 1} tries: {last}")
