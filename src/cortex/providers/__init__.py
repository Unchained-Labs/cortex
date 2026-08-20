"""Model providers.

The internal message shape is the OpenAI chat-completions form; adapters
translate at the wire. ``get_provider`` returns a client for a profile.
"""

from __future__ import annotations

from cortex.config import ProviderProfile
from cortex.providers.base import ChatResult, Provider, ProviderError, ToolCall


def get_provider(profile: ProviderProfile) -> Provider:
    if profile.kind == "openai":
        from cortex.providers.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(profile)
    if profile.kind == "anthropic":
        from cortex.providers.anthropic import AnthropicProvider

        return AnthropicProvider(profile)
    raise ProviderError(
        f"unknown provider kind {profile.kind!r} (expected 'openai' or 'anthropic')"
    )


__all__ = ["ChatResult", "Provider", "ProviderError", "ToolCall", "get_provider"]
