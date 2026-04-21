from __future__ import annotations

from services.providers.base import DigestResult, ScoreResult
from services.providers.claude import ClaudeProvider
from services.providers.openai_compat import OpenAICompatProvider

_PROVIDER_URLS: dict[str, str] = {
    "grok": "https://api.x.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}

_VALID_PROVIDERS = ("claude", "grok", "gemini")


def get_provider(
    provider: str,
    integrations: dict,
    model_override: str | None = None,
) -> ClaudeProvider | OpenAICompatProvider:
    p = (provider or "claude").strip().lower()
    if p not in _VALID_PROVIDERS:
        p = "claude"

    if p == "claude":
        api_key = (integrations.get("claude_api_key") or "").strip()
        model = (model_override or "").strip() or (integrations.get("claude_model") or "").strip()
        return ClaudeProvider(api_key, model)

    api_key = (integrations.get(f"{p}_api_key") or "").strip()
    model = (model_override or "").strip() or (integrations.get(f"{p}_model") or "").strip()
    base_url = _PROVIDER_URLS[p]
    return OpenAICompatProvider(api_key, model, base_url, p)


__all__ = [
    "get_provider",
    "ClaudeProvider",
    "OpenAICompatProvider",
    "ScoreResult",
    "DigestResult",
]
