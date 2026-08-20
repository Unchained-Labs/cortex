"""OpenAI-compatible chat-completions client.

Covers Ollama, vLLM, LM Studio, OpenRouter, and OpenAI itself — anything
serving ``/chat/completions`` and ``/embeddings`` under a ``/v1`` base URL.
Raw httpx, no SDK: the surface cortex uses is small and the dependency is not
worth its weight.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from cortex.config import ProviderProfile
from cortex.providers.base import ChatResult, ProviderError, TokenSink, ToolCall

_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


def _usage(raw: dict | None) -> dict[str, int]:
    """Keep preflight's field names; absent counts stay absent, never zero."""
    if not raw:
        return {}
    out: dict[str, int] = {}
    for field in ("prompt_tokens", "completion_tokens"):
        if raw.get(field) is not None:
            out[field] = int(raw[field])
    return out


def _parse_arguments(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"model produced unparseable tool arguments: {raw[:200]}") from exc
    if not isinstance(parsed, dict):
        raise ProviderError(f"tool arguments must be an object, got: {raw[:200]}")
    return parsed


class OpenAICompatProvider:
    def __init__(self, profile: ProviderProfile):
        if not profile.base_url:
            raise ProviderError(f"provider {profile.name!r} has no base_url")
        self.profile = profile
        self._base = profile.base_url.rstrip("/")

    async def _post(self, client: httpx.AsyncClient, path: str, payload: dict) -> httpx.Response:
        try:
            return await client.post(f"{self._base}{path}", json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self._base} unreachable: {exc}") from exc

    def _headers(self) -> dict[str, str]:
        headers = dict(self.profile.headers)
        key = self.profile.key()
        headers["Authorization"] = f"Bearer {key or 'not-needed'}"
        return headers

    def _chat_payload(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        if not self.profile.chat_model:
            raise ProviderError(f"provider {self.profile.name!r} has no chat_model")
        payload: dict[str, Any] = {
            "model": self.profile.chat_model,
            "messages": messages,
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = tools
        return payload

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_token: TokenSink | None = None,
    ) -> ChatResult:
        if on_token is None:
            return await self._chat_blocking(messages, tools)
        return await self._chat_streaming(messages, tools, on_token)

    async def _chat_blocking(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> ChatResult:
        payload = self._chat_payload(messages, tools)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await self._post(client, "/chat/completions", payload)
        if res.status_code != 200:
            raise ProviderError(f"{self._base} returned {res.status_code}: {res.text[:300]}")
        body = res.json()
        try:
            choice = body["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"malformed chat response: {json.dumps(body)[:300]}") from exc
        calls = [
            ToolCall(
                id=tc.get("id") or f"call_{i}",
                name=tc["function"]["name"],
                arguments=_parse_arguments(tc["function"].get("arguments") or ""),
            )
            for i, tc in enumerate(message.get("tool_calls") or [])
        ]
        return ChatResult(
            text=message.get("content") or "",
            tool_calls=calls,
            usage=_usage(body.get("usage")),
            stop_reason=choice.get("finish_reason") or "",
        )

    async def _chat_streaming(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_token: TokenSink,
    ) -> ChatResult:
        payload = self._chat_payload(messages, tools)
        payload["stream"] = True
        # Ask for usage in the final chunk; servers that ignore the option
        # simply report no usage.
        payload["stream_options"] = {"include_usage": True}

        text: list[str] = []
        # index -> {"id":, "name":, "arguments": str}
        pending: dict[int, dict[str, str]] = {}
        usage: dict[str, int] = {}
        stop_reason = ""

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    f"{self._base}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                ) as res:
                    if res.status_code != 200:
                        detail = (await res.aread()).decode("utf-8", "replace")[:300]
                        raise ProviderError(f"{self._base} returned {res.status_code}: {detail}")
                    async for line in res.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("usage"):
                            usage = _usage(chunk["usage"])
                        for choice in chunk.get("choices") or []:
                            if choice.get("finish_reason"):
                                stop_reason = choice["finish_reason"]
                            delta = choice.get("delta") or {}
                            piece = delta.get("content")
                            if piece:
                                text.append(piece)
                                await on_token(piece)
                            for tc in delta.get("tool_calls") or []:
                                slot = pending.setdefault(
                                    tc.get("index", 0), {"id": "", "name": "", "arguments": ""}
                                )
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["name"] += fn["name"]
                                if fn.get("arguments"):
                                    slot["arguments"] += fn["arguments"]
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self._base} unreachable: {exc}") from exc

        calls = [
            ToolCall(
                id=slot["id"] or f"call_{idx}",
                name=slot["name"],
                arguments=_parse_arguments(slot["arguments"]),
            )
            for idx, slot in sorted(pending.items())
        ]
        return ChatResult(
            text="".join(text), tool_calls=calls, usage=usage, stop_reason=stop_reason
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.profile.embed_model:
            raise ProviderError(f"provider {self.profile.name!r} has no embed_model")
        payload = {"model": self.profile.embed_model, "input": texts}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await self._post(client, "/embeddings", payload)
        if res.status_code != 200:
            raise ProviderError(f"{self._base} returned {res.status_code}: {res.text[:300]}")
        rows = res.json().get("data") or []
        if len(rows) != len(texts):
            raise ProviderError(f"asked for {len(texts)} embeddings, got {len(rows)}")
        # Reassemble by the reported index, never by arrival order — a shifted
        # vector list silently corrupts every later chunk.
        vectors: list[list[float] | None] = [None] * len(texts)
        for row in rows:
            vectors[row["index"]] = row["embedding"]
        if any(v is None for v in vectors):
            raise ProviderError("embedding response left gaps in the batch")
        return vectors  # type: ignore[return-value]
