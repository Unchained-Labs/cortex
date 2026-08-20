"""The agent loop: model, tools, repeat until the model stops asking.

A plain while-loop, on purpose. Model output either carries tool calls —
execute them, append results, go around — or it is the answer. Framework
graphs add ceremony this loop does not need; when a step genuinely needs
structure (verification, planning), it belongs in a tool or a skill, not in
control flow.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from cortex.events import AgentEvent
from cortex.obs import Obs
from cortex.plugins import ToolRegistry
from cortex.providers.base import ChatResult, Provider

EventSink = Callable[[AgentEvent], Awaitable[None]]

MAX_STEPS = 16
# Rough context budget in characters. When history outgrows it, the oldest
# non-system messages are dropped with a visible notice. Summarizing instead
# of dropping is the obvious next step; this at least never dies mid-answer.
CONTEXT_CHARS = 120_000


def _trim(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    total = sum(len(str(m.get("content") or "")) for m in messages)
    dropped = 0
    while total > CONTEXT_CHARS and len(messages) > 3:
        # index 0 is the system prompt; drop the oldest turn after it, but
        # never orphan tool results from the assistant call that made them.
        messages.pop(1)
        dropped += 1
        while messages[1:] and messages[1].get("role") == "tool":
            messages.pop(1)
            dropped += 1
        total = sum(len(str(m.get("content") or "")) for m in messages)
    return messages, dropped


async def run_turn(
    provider: Provider,
    registry: ToolRegistry,
    messages: list[dict[str, Any]],
    on_event: EventSink,
    obs: Obs | None = None,
    thread: str = "",
    stream: bool = True,
) -> str:
    """Run one user turn to completion. ``messages`` is mutated in place so
    the caller keeps the full transcript."""
    tools = registry.openai_tools()
    await on_event(AgentEvent("start", {"model": provider.profile.chat_model}))

    for _step in range(MAX_STEPS):
        messages, dropped = _trim(messages)
        if dropped:
            await on_event(
                AgentEvent("notice", {"text": f"context trimmed: dropped {dropped} old messages"})
            )

        async def on_token(piece: str) -> None:
            await on_event(AgentEvent("token", {"text": piece}))

        started = time.monotonic()
        result: ChatResult = await provider.chat(
            messages, tools=tools, on_token=on_token if stream else None
        )
        if obs is not None:
            obs.usage(
                model=provider.profile.chat_model,
                usage=result.usage,
                latency_ms=int((time.monotonic() - started) * 1000),
                thread=thread,
            )

        if not result.tool_calls:
            messages.append({"role": "assistant", "content": result.text})
            await on_event(AgentEvent("done", {"text": result.text}))
            return result.text

        messages.append(
            {
                "role": "assistant",
                "content": result.text or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in result.tool_calls
                ],
            }
        )
        for tc in result.tool_calls:
            await on_event(AgentEvent("tool_start", {"name": tc.name, "arguments": tc.arguments}))
            outcome = await asyncio.to_thread(registry.invoke, tc.name, tc.arguments)
            if obs is not None:
                obs.tool_event(tc.name, ok=outcome.ok, latency_ms=outcome.latency_ms)
            await on_event(
                AgentEvent(
                    "tool_end",
                    {
                        "name": tc.name,
                        "ok": outcome.ok,
                        "latency_ms": outcome.latency_ms,
                        "preview": outcome.text[:400],
                    },
                )
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": outcome.text,
                }
            )

    text = f"Stopped after {MAX_STEPS} tool steps without a final answer."
    messages.append({"role": "assistant", "content": text})
    await on_event(AgentEvent("error", {"text": text}))
    return text
