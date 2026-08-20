"""Chat models via LangChain.

``kind`` is the wire, with two conveniences that pre-fill a base URL:

* ``openai``     — any OpenAI-compatible endpoint (base_url required)
* ``openrouter`` — OpenAI wire, base_url defaults to https://openrouter.ai/api/v1
* ``litellm``    — OpenAI wire against your LiteLLM proxy (base_url required);
                   routing/fallback policy lives in the proxy, not here
* ``anthropic``  — the Anthropic Messages API directly
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from cortex.config import ProviderProfile
from cortex.providers.embeddings import ProviderError

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_OPENAI_WIRE = {"openai", "openrouter", "litellm"}


def chat_model(profile: ProviderProfile) -> BaseChatModel:
    if not profile.chat_model:
        raise ProviderError(f"provider {profile.name!r} has no chat_model")

    if profile.kind in _OPENAI_WIRE:
        base_url = profile.base_url or (OPENROUTER_BASE if profile.kind == "openrouter" else "")
        if not base_url:
            raise ProviderError(f"provider {profile.name!r} ({profile.kind}) needs a base_url")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=profile.chat_model,
            base_url=base_url,
            api_key=profile.key() or "not-needed",
            temperature=0.2,
            default_headers=profile.headers or None,
            stream_usage=True,
        )

    if profile.kind == "anthropic":
        key = profile.key()
        if not key:
            raise ProviderError(f"provider {profile.name!r} has no API key; set api_key_env")
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=profile.chat_model,
            api_key=key,
            temperature=0.2,
            max_tokens=8192,
            **({"base_url": profile.base_url} if profile.base_url else {}),
        )

    raise ProviderError(
        f"unknown provider kind {profile.kind!r} "
        "(expected openai, openrouter, litellm, or anthropic)"
    )
