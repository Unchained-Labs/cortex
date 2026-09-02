"""Export the brain's tools as an MCP server.

Two transports over ONE server definition:

* **stdio** — `cortex mcp --brain <path>`. Claude Code, Cursor and any other
  local client attaches with that command and sees the same registry the chat
  agent uses.
* **streamable HTTP** — mounted at ``/mcp`` by the web server, authenticated
  with ``ctx_`` Bearer keys. This is the one a client in another container or
  on another machine can reach; stdio requires being able to fork the process,
  which nothing across a network boundary can do.

`build_server` exists so the two cannot drift. When they were written
separately the obvious failure was a tool added to one and not the other, and
the less obvious one was two different JSON schemas for the same tool.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cortex.brain import Brain


def build_server(brain: Brain, source: str = "mcp"):
    """The MCP server for a brain, with every registry plugin exposed.

    ``source`` is recorded on each tool event so the observability tab can tell
    a local editor apart from an agent calling in over the network — the same
    tool doing the same thing for very different reasons.
    """
    import mcp.types as types
    from mcp.server import Server

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
        brain.obs.tool_event(name, ok=outcome.ok, latency_ms=outcome.latency_ms, source=source)
        return [types.TextContent(type="text", text=outcome.text)]

    return server


async def serve_stdio(brain: Brain) -> None:
    from mcp.server.stdio import stdio_server

    server = build_server(brain, source="mcp")
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
