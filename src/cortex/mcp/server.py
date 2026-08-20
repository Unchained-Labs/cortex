"""Export the brain's tools as an MCP server over stdio.

`cortex mcp --brain <path>` is the whole integration: Claude Code, Cursor,
Hermes, or any stdio MCP client attaches with that command and sees the same
registry the chat agent uses. Streamable-HTTP export (network clients with
``ctx_`` Bearer keys) is not built yet; the key store already exists for it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cortex.brain import Brain


async def serve_stdio(brain: Brain) -> None:
    import mcp.types as types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    registry = brain.registry
    server: Server = Server(brain.config.name)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=p.name,
                description=p.description,
                inputSchema={
                    "type": "object",
                    "properties": p.parameters,
                    "required": list(p.required),
                    "additionalProperties": False,
                },
            )
            for p in registry.plugins()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        outcome = await asyncio.to_thread(registry.invoke, name, arguments or {})
        brain.obs.tool_event(name, ok=outcome.ok, latency_ms=outcome.latency_ms, source="mcp")
        return [types.TextContent(type="text", text=outcome.text)]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
