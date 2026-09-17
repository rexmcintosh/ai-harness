from __future__ import annotations
import time
import requests

VENICE_API = "https://api.venice.ai/api/v1/chat/completions"

# complete() needs to tell "caller said nothing" from "caller said 0", because 0
# is the operator's kill switch for the output cap and must beat a client default.
_UNSET = object()

# Only these are worth retrying — a 4xx (bad model name, auth, bad request) will
# fail identically every time, so retrying just burns time and billing.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class VeniceError(RuntimeError):
    pass


class VeniceClient:
    """Thin Venice chat client. `post` is injectable for tests."""

    def __init__(self, api_key, *, base_url=VENICE_API, timeout=180,
                 retries=2, backoff=1.5, post=None, temperature=0.2,
                 max_completion_tokens=None, transport_is_real=None):
        if not api_key:
            raise VeniceError("no Venice key set (tried VENICE_COUNCIL_KEY, VENICE_API_KEY)")
        self.api_key = api_key
        # Default output ceiling for every call this client makes, so no call
        # site can be left unbounded by omission. Individual calls (the chair,
        # the router, a per-seat override) pass their own. None or <= 0 sends
        # nothing, which is Venice's own "use the model default".
        self.max_completion_tokens = max_completion_tokens
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.temperature = temperature
        self._post = post or requests.post
        # An injected transport means this client is not making real Venice
        # calls, so the usage it would log is synthetic and must not reach the
        # production ledger (see venice_usage/guard.py — a hand-run demo put 13
        # fabricated rows into it on 2026-09-11). A wrapper that DOES call
        # Venice for real — a budget cap, a retry shim — says so here, once.
        self._transport_is_real = (
            (post is None or post is requests.post) if transport_is_real is None
            else bool(transport_is_real))

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
            venice_usage.log_client_call(
                data=data,
                model=model,
                project="council",
                task_type=task_type,
                source="council/venice",
                transport_is_real=self._transport_is_real,
            )
        except Exception:
            pass

    def complete(self, model, system, user, *, json_mode=True, task_type="chat",
                 max_completion_tokens=_UNSET):
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
        # `max_completion_tokens`, not `max_tokens`: Venice's OpenAPI spec
        # (20260911.122036) marks max_tokens deprecated in favour of it, and
        # defines it as the bound on "visible output tokens AND reasoning
        # tokens" — every council seat is a reasoning model, so the reasoning
        # half is the half that runs away.
        cap = (self.max_completion_tokens if max_completion_tokens is _UNSET
               else max_completion_tokens)
        if cap is not None and int(cap) > 0:
            payload["max_completion_tokens"] = int(cap)
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
