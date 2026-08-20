"""Model providers.

Chat models come from LangChain: one OpenAI-compatible wire covers Ollama,
vLLM, LM Studio, OpenRouter, and a LiteLLM proxy; Anthropic speaks its own.
Embeddings stay a thin httpx client because every self-hosted embedding
server speaks the OpenAI shape and nothing more is needed.
"""

from cortex.providers.embeddings import Embedder, ProviderError
from cortex.providers.llm import chat_model

__all__ = ["Embedder", "ProviderError", "chat_model"]
