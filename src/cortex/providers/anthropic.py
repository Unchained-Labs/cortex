"""Anthropic Messages API adapter.

Translates cortex's internal OpenAI-shaped messages to the Messages API and
back. Anthropic serves no embeddings endpoint; point ``roles.embed`` at an
OpenAI-compatible profile when this is the chat provider.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from cortex.config import ProviderProfile
from cortex.providers.base import ChatResult, ProviderError, TokenSink, ToolCall

_VERSION = "2023-06-01"
_DEFAULT_BASE = "https://api.anthropic.com"
_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
_MAX_TOKENS = 8192


def convert_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Split out the system prompt and convert the rest to Anthropic blocks."""
    system = ""
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg["role"]
        if role == "system":
            system = f"{system}\n\n{msg['content']}".strip() if system else msg["content"]
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", ""),
                "content": msg.get("content") or "",
            }
            # Consecutive tool results join one user turn.
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for tc in msg.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": json.loads(tc["function"]["arguments"] or "{}"),
                    }
                )
            out.append({"role": "assistant", "content": blocks or msg.get("content", "")})
        else:
            out.append({"role": "user", "content": msg.get("content") or ""})
    return system, out


def convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"].get("description", ""),
            "input_schema": t["function"].get("parameters")
            or {"type": "object", "properties": {}},
        }
        for t in tools or []
    ]


class AnthropicProvider:
    def __init__(self, profile: ProviderProfile):
        self.profile = profile
        self._base = (profile.base_url or _DEFAULT_BASE).rstrip("/")

    def _headers(self) -> dict[str, str]:
        key = self.profile.key()
        if not key:
            raise ProviderError(
                f"provider {self.profile.name!r} has no API key; set api_key_env"
            )
        return {
            "x-api-key": key,
            "anthropic-version": _VERSION,
            **self.profile.headers,
        }

    def _payload(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        if not self.profile.chat_model:
            raise ProviderError(f"provider {self.profile.name!r} has no chat_model")
        system, converted = convert_messages(messages)
        payload: dict[str, Any] = {
            "model": self.profile.chat_model,
            "max_tokens": _MAX_TOKENS,
            "messages": converted,
        }
        if system:
            payload["system"] = system
        anthropic_tools = convert_tools(tools)
        if anthropic_tools:
            payload["tools"] = anthropic_tools
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
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                res = await client.post(
                    f"{self._base}/v1/messages",
                    json=self._payload(messages, tools),
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self._base} unreachable: {exc}") from exc
        if res.status_code != 200:
            raise ProviderError(f"{self._base} returned {res.status_code}: {res.text[:300]}")
        body = res.json()
        text: list[str] = []
        calls: list[ToolCall] = []
        for block in body.get("content") or []:
            if block["type"] == "text":
                text.append(block["text"])
            elif block["type"] == "tool_use":
                calls.append(
                    ToolCall(id=block["id"], name=block["name"], arguments=block["input"] or {})
                )
        return ChatResult(
            text="".join(text),
            tool_calls=calls,
            usage=_usage(body.get("usage")),
            stop_reason=body.get("stop_reason") or "",
        )

    async def _chat_streaming(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_token: TokenSink,
    ) -> ChatResult:
        payload = self._payload(messages, tools)
        payload["stream"] = True
        text: list[str] = []
        calls: list[ToolCall] = []
        usage: dict[str, int] = {}
        state: dict[str, str] = {}

        try:
            await self._stream_into(payload, on_token, text, calls, usage, state)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self._base} unreachable: {exc}") from exc

        return ChatResult(
            text="".join(text),
            tool_calls=calls,
            usage=usage,
            stop_reason=state.get("stop_reason", ""),
        )

    async def _stream_into(self, payload, on_token, text, calls, usage, state) -> None:
        # open tool_use block being assembled: id, name, partial-json chars
        open_tool: dict[str, Any] | None = None
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST", f"{self._base}/v1/messages", json=payload, headers=self._headers()
            ) as res:
                if res.status_code != 200:
                    detail = (await res.aread()).decode("utf-8", "replace")[:300]
                    raise ProviderError(f"{self._base} returned {res.status_code}: {detail}")
                async for line in res.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    kind = event.get("type")
                    if kind == "message_start":
                        usage.update(_usage(event.get("message", {}).get("usage")))
                    elif kind == "content_block_start":
                        block = event.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            open_tool = {"id": block["id"], "name": block["name"], "json": ""}
                    elif kind == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            text.append(delta["text"])
                            await on_token(delta["text"])
                        elif delta.get("type") == "input_json_delta" and open_tool is not None:
                            open_tool["json"] += delta.get("partial_json", "")
                    elif kind == "content_block_stop":
                        if open_tool is not None:
                            raw = open_tool["json"]
                            calls.append(
                                ToolCall(
                                    id=open_tool["id"],
                                    name=open_tool["name"],
                                    arguments=json.loads(raw) if raw else {},
                                )
                            )
                            open_tool = None
                    elif kind == "message_delta":
                        reason = (event.get("delta") or {}).get("stop_reason")
                        if reason:
                            state["stop_reason"] = reason
                        usage.update(_usage(event.get("usage")))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise ProviderError(
            "Anthropic serves no embeddings endpoint; point roles.embed at an "
            "openai-compatible profile (Ollama's nomic-embed-text works well)"
        )


def _usage(raw: dict | None) -> dict[str, int]:
    """Normalize to preflight's field names. Absent counts stay absent."""
    if not raw:
        return {}
    out: dict[str, int] = {}
    if raw.get("input_tokens") is not None:
        out["prompt_tokens"] = int(raw["input_tokens"])
    if raw.get("output_tokens") is not None:
        out["completion_tokens"] = int(raw["output_tokens"])
    return out
