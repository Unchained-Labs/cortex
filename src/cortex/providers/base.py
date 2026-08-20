from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

# on_token receives text deltas as they stream; pass None for a blocking call.
TokenSink = Callable[[str], Awaitable[None]]


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Absent usage stays absent. An endpoint that reports no token counts
    # produces {} here, never zeros — unmeasured is not zero.
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str = ""


class Provider(Protocol):
    profile: Any

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_token: TokenSink | None = None,
    ) -> ChatResult: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
