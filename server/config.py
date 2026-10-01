"""Server configuration: the OpenAI-facing model names and what they map to."""

import os
import json
import re

from settings import load_environment

load_environment()

# Requests per minute allowed per client IP (override with RATE_LIMIT_PER_MINUTE).
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))

# When the server has no session, should it pop a visible browser window for
# interactive sign-in (the first request then blocks until you finish logging
# in)? On by default for local single-user use. Set to "0"/"false" for headless
# deployments, where it instead returns a 503 telling the caller to run
# the provider's auth command (`python -m deepseek.auth` or `python -m qwen.auth`).
SERVER_INTERACTIVE_LOGIN = os.getenv("SERVER_INTERACTIVE_LOGIN", "1").lower() not in (
    "0", "false", "no", "off",
)

# DeepSeek background refresher: periodically re-captures the token from the
# persistent browser profile so a request never hits an expired token. Runs in a
# daemon thread and never opens a visible window (allow_interactive=False).
SESSION_REFRESH_ENABLED = os.getenv("SESSION_REFRESH_ENABLED", "1").lower() not in (
    "0", "false", "no", "off",
)
# Refresh well before SESSION_MAX_AGE (6h in deepseek.auth) so the cached token
# stays fresh. Default 5h.
SESSION_REFRESH_INTERVAL = int(os.getenv("SESSION_REFRESH_INTERVAL", str(5 * 60 * 60)))

# Playwright browser channel for the (background) headless refresh. The light
# bundled "chromium-headless-shell" uses far less RAM than full Chrome, but may
# not decrypt cookies written by Chrome's Keychain-bound profile — so callers
# fall back to "chrome" when a headless capture is empty or fails.
REFRESH_BROWSER_CHANNEL = os.getenv("REFRESH_BROWSER_CHANNEL", "chromium-headless-shell")

# Fallback channel used after a missing token or capture error. Empty disables it.
REFRESH_BROWSER_CHANNEL_FALLBACK = os.getenv("REFRESH_BROWSER_CHANNEL_FALLBACK", "chrome")

# Public model ids the server advertises (via /v1/models) and accepts, mapped to
# DeepSeek's `model_type` wire value. This is the MODEL axis ONLY — it picks
# which model answers. DeepThink and web Search are orthogonal tools requested
# per call via the `thinking` and `search` booleans, never encoded in the model name.
#
# "vision" is deferred: it only does anything with an image attached, which needs
# ref_file_ids / file-upload plumbing we don't have yet.
MODEL_MAP = {
    "deepseek-chat":   "default",   # Instant — the fast default model
    "deepseek-expert": "expert",    # Expert  — the stronger, slower model
}

# Qwen Chat is opt-in and uses a separate account/profile from DeepSeek.
QWEN_MODEL_MAP = {
    "qwen3.8-omni-flash": "qwen3.8-omni-flash",
    "qwen3.8-max": "qwen3.8-max",
}
QWEN_ENABLED = os.getenv("QWEN_ENABLED", "0").lower() not in ("", "0", "false", "no", "off")
if QWEN_ENABLED:
    MODEL_MAP.update(QWEN_MODEL_MAP)

OPTIONAL_MODEL_PROVIDERS = {}
for provider in ("grok", "mistral", "kimi", "glm"):
    enabled = os.getenv(provider.upper() + "_ENABLED", "0").lower() not in ("", "0", "false", "no", "off")
    if enabled:
        name = provider + "-web"
        MODEL_MAP[name] = "default"
        OPTIONAL_MODEL_PROVIDERS[name] = provider

# Model availability differs by Google account and region. Require slugs from
# `agy models`, rather than advertising an invented or silently substituted one.
if os.getenv("GEMINI_ENABLED", "0").lower() not in ("", "0", "false", "no", "off"):
    google_models = json.loads(os.getenv("GEMINI_MODELS", "{}"))
    if not isinstance(google_models, dict) or len(google_models) > 50:
        raise ValueError("GEMINI_MODELS must be a JSON object with at most 50 models")
    for name, slug in google_models.items():
        if (not isinstance(name, str) or not re.fullmatch(r"gemini-[a-zA-Z0-9._-]{1,100}", name)
                or not isinstance(slug, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", slug)):
            raise ValueError("Invalid GEMINI_MODELS model name or CLI slug")
        MODEL_MAP[name] = slug
        OPTIONAL_MODEL_PROVIDERS[name] = "gemini"


def model_provider(name: str) -> str:
    return OPTIONAL_MODEL_PROVIDERS.get(name, "qwen" if name in QWEN_MODEL_MAP else "deepseek")

DEFAULT_MODEL = "deepseek-chat"


def is_known_model(name: str) -> bool:
    """Whether `name` is a model id we accept (used to 404 unknown models)."""
    return name in MODEL_MAP


def resolve_model_type(name: str) -> str:
    """Translate a public model id to the provider's wire model value.

    Caller must check `is_known_model` first; this raises KeyError otherwise.
    """
    return MODEL_MAP[name]
