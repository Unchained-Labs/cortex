"""Streaming events emitted by an agent turn.

Every surface (CLI, SSE, MCP) consumes the same event stream, so a new
surface never needs to reach into the loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentEvent:
    # "start" | "token" | "tool_start" | "tool_end" | "notice" | "done" | "error"
    type: str
    data: dict[str, Any] = field(default_factory=dict)
