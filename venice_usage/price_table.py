"""GENERATED FILE — do not edit by hand.

Venice model prices, read from the live catalogue at `GET /api/v1/models` and
cross-checked against the billed SKUs at `GET /billing/usage-history`.

Regenerate with:

    python -m venice_usage.refresh_prices --write

Check whether it has gone stale (exit 2 = the live catalogue disagrees):

    python -m venice_usage.refresh_prices --check

`PRICES` is DIEM (== USD on this account) per 1,000,000 tokens. Cache keys are
deliberately uneven and that is the point:

* `cache_write_5m` / `cache_write_1h` — the model is billed TTL-segmented
  cache-write SKUs, and only the TTLs actually seen on a bill are listed. No
  1-hour rate is derived from a multiplier.
* `cache_write` — one un-suffixed rate: either the bills show a single SKU with
  no TTL in its name, or the catalogue publishes a write rate this account has
  never been billed, in which case no TTL segmentation is claimed.
* no `cache_write*` key at all — the catalogue publishes no write rate and none
  was invented.
* `cache_read` likewise varies inside a family: claude-fable-5 reads at 1.20 and
  claude-fable-5-1 at 0.30 on the same 12.00 input. Billed fact, not a typo.

`EXTENDED` records above-threshold context tiers verbatim; `estimate_usd` does
not apply them (no ledger row has ever crossed a threshold, so there is no
billed line item confirming what the threshold counts). `BILLED` is the receipts
the cache shapes above were taken from.
"""

from __future__ import annotations

REFRESHED_AT = "2026-11-28"
CATALOGUE_URL = "https://api.venice.ai/api/v1/models"
BILLING_URL = "https://api.venice.ai/api/v1/billing/usage-history"
BILLING_WINDOW = "2026-08-30..2026-11-29"
STALE_AFTER_DAYS = 30

# DIEM per 1,000,000 tokens.
PRICES: dict[str, dict[str, float]] = {
    "claude-fable-5": {"input": 10.0, "output": 50.0, "cache_read": 1.0, "cache_write_5m": 12.5, "cache_write_1h": 24.0},
    "claude-fable-5-1": {"input": 10.0, "output": 50.0, "cache_read": 0.25, "cache_write_5m": 15.0},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write_5m": 6.25},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 12.0},
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write_5m": 7.5},
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write_5m": 6.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write_5m": 3.75},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write_5m": 2.5, "cache_write_1h": 6.0},
    "deepseek-v4-1-flash": {"input": 0.3, "output": 1.2, "cache_read": 0.0075},
    "deepseek-v4-1-flash-1017": {"input": 0.3, "output": 1.2, "cache_read": 0.0075},
    "deepseek-v4-flash": {"input": 0.138, "output": 0.275, "cache_read": 0.028},
    "deepseek-v4-pro": {"input": 1.65, "output": 3.301, "cache_read": 0.33},
    "deepseek-v4-pro-0813": {"input": 1.65, "output": 4.95, "cache_read": 0.165},
    "gemini-3-1-pro-preview": {"input": 2.5, "output": 15.0, "cache_read": 0.5, "cache_write": 0.5},
    "gemini-3-5-flash": {"input": 1.55, "output": 9.45, "cache_read": 0.155, "cache_write": 0.086},
    "gemini-3-8-flash": {"input": 0.9375, "output": 4.6875, "cache_read": 0.09375},
    "grok-4-3": {"input": 1.42, "output": 2.83, "cache_read": 0.23},
    "grok-4-6": {"input": 2.27, "output": 6.8, "cache_read": 0.57},
    "grok-4-7": {"input": 2.27, "output": 6.8, "cache_read": 0.57},
    "kimi-k2-6": {"input": 0.75, "output": 3.5, "cache_read": 0.16},
    "kimi-k3": {"input": 3.75, "output": 18.75, "cache_read": 0.375},
    "kimi-k3-1-pro": {"input": 2.0, "output": 10.0, "cache_read": 0.2},
    "openai-gpt-53-codex": {"input": 1.75, "output": 14.0, "cache_read": 0.175},
    "openai-gpt-54": {"input": 2.5, "output": 15.0, "cache_read": 0.25},
    "openai-gpt-55": {"input": 5.0, "output": 30.0, "cache_read": 0.5},
    "openai-gpt-56-luna": {"input": 0.2, "output": 1.2, "cache_read": 0.02, "cache_write": 0.25},
    "openai-gpt-56-sol": {"input": 4.0, "output": 20.0, "cache_read": 0.4, "cache_write": 5.0},
    "openai-gpt-6-astra": {"input": 10.0, "output": 50.0, "cache_read": 1.0, "cache_write": 12.5},
    "openai-gpt-61-sol": {"input": 2.0, "output": 10.0, "cache_read": 0.1, "cache_write": 3.125},
    "qwen-3-7-max": {"input": 2.7, "output": 8.05, "cache_read": 0.27, "cache_write": 3.35},
    "qwen-3-8-max": {"input": 2.5, "output": 7.5, "cache_read": 0.3125, "cache_write": 3.125},
    "qwen3-235b-a22b-instruct-2507": {"input": 0.15, "output": 0.75},
    "qwen3-coder-480b-a35b-instruct-turbo": {"input": 0.35, "output": 1.5, "cache_read": 0.04},
    "xiaomi-mimo-v2-6-flash": {"input": 0.14, "output": 0.28, "cache_read": 0.0028},
    "z-ai-glm-5-3": {"input": 1.75, "output": 5.5, "cache_read": 0.325},
    "z-ai-glm-5-3-flash": {"input": 0.15, "output": 0.5, "cache_read": 0.03},
    "zai-org-glm-5": {"input": 1.0, "output": 3.2, "cache_read": 0.2},
    "zai-org-glm-5-2": {"input": 1.4, "output": 4.4, "cache_read": 0.26},
}

# Seen in the ledger, not token-priced: the caller logs a real --usd.
UNPRICED: dict[str, str] = {
    "flux-2-max": "image model — no token pricing published",
    "gemini-omni-flash-1-1-image-to-video": "video model — no token pricing published",
    "gpt-image-2": "image model — no token pricing published",
    "gpt-image-2-5-flare": "image model — no token pricing published",
    "gpt-image-2-5-flare-edit": "inpaint model — no token pricing published",
    "gpt-image-2-5-sunburst": "image model — no token pricing published",
    "gpt-image-2-5-sunburst-edit": "inpaint model — no token pricing published",
    "gpt-image-2-edit": "inpaint model — no token pricing published",
    "kling-o3-pro-image-to-video": "video model — no token pricing published",
    "kling-v3-pro-image-to-video": "video model — no token pricing published",
    "minimax-h3-max-image-to-video": "video model — no token pricing published",
    "tts-elevenlabs-turbo-v2-5": "tts model — no token pricing published",
}

# Ledger ids the catalogue has never published — test doubles and
# retired models. They stay listed so coverage is provably complete.
UNKNOWN: tuple[str, ...] = ("m", "mistral-small-2603", "served-id", "test-model")

# Every model id this table was generated to cover.
COVERS: tuple[str, ...] = ("claude-fable-5", "claude-fable-5-1", "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8", "claude-opus-5", "claude-opus-5-5", "claude-sonnet-4-6", "claude-sonnet-5", "deepseek-v4-1-flash", "deepseek-v4-1-flash-1017", "deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-pro-0813", "flux-2-max", "gemini-3-1-pro-preview", "gemini-3-5-flash", "gemini-3-8-flash", "gemini-omni-flash-1-1-image-to-video", "gpt-image-2", "gpt-image-2-5-flare", "gpt-image-2-5-flare-edit", "gpt-image-2-5-sunburst", "gpt-image-2-5-sunburst-edit", "gpt-image-2-edit", "grok-4-3", "grok-4-6", "grok-4-7", "kimi-k2-6", "kimi-k3", "kimi-k3-1-pro", "kling-o3-pro-image-to-video", "kling-v3-pro-image-to-video", "m", "minimax-h3-max-image-to-video", "mistral-small-2603", "openai-gpt-53-codex", "openai-gpt-54", "openai-gpt-55", "openai-gpt-56-luna", "openai-gpt-56-sol", "openai-gpt-6-astra", "openai-gpt-61-sol", "qwen-3-7-max", "qwen-3-8-max", "qwen3-235b-a22b-instruct-2507", "qwen3-coder-480b-a35b-instruct-turbo", "served-id", "test-model", "tts-elevenlabs-turbo-v2-5", "xiaomi-mimo-v2-6-flash", "z-ai-glm-5-3", "z-ai-glm-5-3-flash", "zai-org-glm-5", "zai-org-glm-5-2")

# Above-threshold context tiers, recorded not applied.
EXTENDED: dict[str, dict[str, float]] = {
    "gemini-3-1-pro-preview": {"context_token_threshold": 200000, "input": 5.0, "output": 22.5, "cache_read": 0.5, "cache_write": 0.5},
    "grok-4-3": {"context_token_threshold": 200000, "input": 2.83, "output": 5.67, "cache_read": 0.45},
    "grok-4-6": {"context_token_threshold": 200000, "input": 4.53, "output": 13.6, "cache_read": 1.13},
    "grok-4-7": {"context_token_threshold": 200000, "input": 4.53, "output": 13.6, "cache_read": 1.13},
    "openai-gpt-54": {"context_token_threshold": 272000, "input": 5.0, "output": 22.5, "cache_read": 0.5},
    "openai-gpt-55": {"context_token_threshold": 272000, "input": 10.0, "output": 45.0, "cache_read": 1.0},
    "openai-gpt-56-luna": {"context_token_threshold": 272000, "input": 0.4, "output": 1.8, "cache_read": 0.04, "cache_write": 0.5},
    "openai-gpt-56-sol": {"context_token_threshold": 272000, "input": 8.0, "output": 30.0, "cache_read": 0.8, "cache_write": 10.0},
    "openai-gpt-6-astra": {"context_token_threshold": 272000, "input": 20.0, "output": 75.0, "cache_read": 2.0, "cache_write": 25.0},
    "openai-gpt-61-sol": {"context_token_threshold": 272000, "input": 4.0, "output": 15.0, "cache_read": 0.2, "cache_write": 5.0},
}

# Observed rates from /billing/usage-history.
BILLED: dict[str, dict[str, float]] = {
    "claude-fable-5": {"cache_read": 1.2, "cache_write_1h": 24.0, "cache_write_5m": 12.5, "input": 10.0, "output": 50.0},
    "claude-fable-5-1": {"cache_read": 0.3, "cache_write_5m": 15.0, "input": 12.0, "output": 60.0},
    "claude-opus-4-6": {"cache_read": 0.5, "cache_write_5m": 6.25, "input": 5.0, "output": 25.0},
    "claude-opus-4-8": {"cache_read": 0.5, "cache_write_1h": 12.0, "cache_write_5m": 6.25, "input": 5.0, "output": 25.0},
    "claude-opus-5": {"cache_write_5m": 7.5, "input": 6.0, "output": 30.0},
    "claude-opus-5-5": {"cache_read": 0.24, "cache_write_5m": 6.0, "input": 4.8, "output": 24.0},
    "claude-sonnet-4-6": {"cache_read": 0.3, "cache_write_5m": 3.75, "input": 3.0, "output": 15.0},
    "claude-sonnet-5": {"cache_read": 0.3, "cache_write_1h": 6.0, "cache_write_5m": 2.5, "input": 2.0, "output": 10.0},
    "deepseek-v4-1-flash": {"input": 0.375, "output": 1.5},
    "deepseek-v4-1-flash-1017": {"cache_read": 0.0075, "input": 0.3, "output": 1.2},
    "deepseek-v4-flash": {"cache_read": 0.028, "input": 0.138, "output": 0.275},
    "deepseek-v4-pro": {"cache_read": 0.33, "input": 1.65, "output": 3.301},
    "deepseek-v4-pro-0813": {"cache_read": 0.165, "input": 1.65, "output": 4.95},
    "gemini-3-1-pro-preview": {"cache_read": 0.5, "cache_write": 0.5, "input": 2.5, "output": 15.0},
    "gemini-3-5-flash": {"input": 1.55, "output": 9.45},
    "gemini-3-8-flash": {"input": 0.9375, "output": 4.6875},
    "grok-4-3": {"cache_read": 0.23, "input": 1.42, "output": 2.83},
    "grok-4-6": {"cache_read": 0.57, "input": 2.27, "output": 6.8},
    "grok-4-7": {"cache_read": 0.57, "input": 2.27, "output": 6.8},
    "kimi-k3": {"cache_read": 0.375, "input": 3.75, "output": 18.75},
    "kimi-k3-1-pro": {"cache_read": 0.2, "input": 2.0, "output": 10.0},
    "openai-gpt-53-codex": {"cache_read": 0.175, "input": 1.75, "output": 14.0},
    "openai-gpt-54": {"cache_read": 0.25, "input": 2.5, "output": 15.0},
    "openai-gpt-55": {"input": 6.25, "output": 37.5},
    "openai-gpt-56-luna": {"input": 0.26666667, "output": 1.6},
    "openai-gpt-56-sol": {"cache_read": 0.4, "cache_write": 5.0, "input": 4.0, "output": 20.0},
    "openai-gpt-6-astra": {"cache_write": 12.5, "input": 10.0, "output": 50.0},
    "openai-gpt-61-sol": {"cache_read": 0.125, "cache_write": 3.125, "input": 2.5, "output": 12.5},
    "qwen-3-7-max": {"cache_write": 3.35, "input": 2.7, "output": 8.05},
    "qwen-3-8-max": {"cache_read": 0.3125, "cache_write": 3.125, "input": 2.5, "output": 7.5},
    "qwen3-coder-480b-a35b-instruct-turbo": {"cache_read": 0.04, "input": 0.35, "output": 1.5},
    "xiaomi-mimo-v2-6-flash": {"cache_read": 0.0028, "input": 0.14, "output": 0.28},
    "z-ai-glm-5-3": {"cache_read": 0.325, "input": 1.75, "output": 5.5},
    "z-ai-glm-5-3-flash": {"cache_read": 0.03, "input": 0.15, "output": 0.5},
    "zai-org-glm-5-2": {"cache_read": 0.26, "input": 1.4, "output": 4.4},
}

# (model, field, catalogue rate, billed rate) where the bills and the
# catalogue disagree. The catalogue is what PRICES uses.
DISAGREEMENTS: tuple[tuple, ...] = (("claude-fable-5", "cache_read", 1.0, 1.2), ("claude-fable-5-1", "input", 10.0, 12.0), ("claude-fable-5-1", "output", 50.0, 60.0), ("claude-fable-5-1", "cache_read", 0.25, 0.3), ("claude-opus-5", "input", 5.0, 6.0), ("claude-opus-5", "output", 25.0, 30.0), ("claude-opus-5-5", "input", 4.0, 4.8), ("claude-opus-5-5", "output", 20.0, 24.0), ("claude-opus-5-5", "cache_read", 0.2, 0.24), ("claude-sonnet-5", "cache_read", 0.2, 0.3), ("deepseek-v4-1-flash", "input", 0.3, 0.375), ("deepseek-v4-1-flash", "output", 1.2, 1.5), ("openai-gpt-55", "input", 5.0, 6.25), ("openai-gpt-55", "output", 30.0, 37.5), ("openai-gpt-56-luna", "input", 0.2, 0.26666667), ("openai-gpt-56-luna", "output", 1.2, 1.6), ("openai-gpt-61-sol", "input", 2.0, 2.5), ("openai-gpt-61-sol", "output", 10.0, 12.5), ("openai-gpt-61-sol", "cache_read", 0.1, 0.125))

# SKUs Venice repriced inside the billing window read above.
REPRICED: tuple[tuple, ...] = (("claude-fable-5", "cache_write_5m", (12.5, 15.0)), ("claude-fable-5", "input", (10.0, 12.0)), ("claude-fable-5", "output", (50.0, 60.0)), ("claude-opus-4-8", "cache_read", (0.5, 0.6)), ("claude-opus-4-8", "cache_write_5m", (6.25, 7.5)), ("claude-opus-4-8", "input", (5.0, 6.0)), ("claude-opus-4-8", "output", (25.0, 30.0)), ("claude-sonnet-5", "cache_write_5m", (2.5, 3.125, 3.75)), ("claude-sonnet-5", "input", (2.0, 2.5, 3.0)), ("claude-sonnet-5", "output", (10.0, 12.5, 15.0)), ("openai-gpt-53-codex", "cache_read", (0.175, 0.219)), ("openai-gpt-53-codex", "input", (1.75, 2.19)), ("openai-gpt-53-codex", "output", (14.0, 17.5)), ("openai-gpt-54", "cache_read", (0.25, 0.313)), ("openai-gpt-54", "input", (2.5, 3.13)), ("openai-gpt-54", "output", (15.0, 18.8)), ("openai-gpt-56-sol", "cache_read", (0.25, 0.4, 0.5)), ("openai-gpt-56-sol", "cache_write", (3.125, 5.0, 6.25, 7.8125)), ("openai-gpt-56-sol", "input", (2.5, 4.0, 5.0, 6.25)), ("openai-gpt-56-sol", "output", (12.5, 20.0, 25.0, 37.5)))

# Billed model ids with no catalogue entry to map onto.
UNRESOLVED: dict[str, int] = {"minimax-m3-1-flash-preview": 90, "mistral-small-2603": 4}
