"""Consume external MCP servers as tool plugins.

Each configured server is listed once at startup; each *invocation* opens a
fresh session, calls the tool, and closes. That trades per-call latency (one
process spawn or HTTP handshake per call) for zero session babysitting — the
right trade until it is measured to matter. A server that fails to list is
recorded and skipped; it never takes the brain down.

Filtering: ``include`` limits to named tools, ``exclude`` always wins.
A stdio server runs the configured command on this machine with this user's
permissions — treat the mcp_servers block of cortex.yaml as trusted config.
"""

from __future__ import annotations

import asyncio
from typing import Any

from cortex.config import McpServerConfig
from cortex.plugins import ToolPlugin, ToolRegistry


def _selected(name: str, config: McpServerConfig) -> bool:
    if name in config.exclude:
        return False
    if config.include and name not in config.include:
        return False
    return True


async def _list_tools(config: McpServerConfig) -> list[Any]:
    async with _session(config) as session:
        result = await session.list_tools()
        return list(result.tools)


async def _call_tool(config: McpServerConfig, name: str, arguments: dict[str, Any]) -> str:
    async with _session(config) as session:
        result = await session.call_tool(name, arguments)
        parts: list[str] = []
        for block in result.content:
            text = getattr(block, "text", None)
            parts.append(text if text is not None else f"[{block.type} content]")
        text = "\n".join(parts).strip() or "(empty result)"
        if getattr(result, "isError", False):
            return f"Tool {name!r} failed: {text}"
        return text


def _session(config: McpServerConfig):
    """Async context manager yielding an initialized ClientSession."""
    from contextlib import asynccontextmanager

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    @asynccontextmanager
    async def manage():
        if config.transport == "stdio":
            params = StdioServerParameters(command=config.command, args=list(config.args))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        elif config.transport == "http":
            async with streamablehttp_client(config.url, headers=dict(config.headers)) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        else:
            raise ValueError(f"unknown MCP transport {config.transport!r}")

    return manage()


def _make_plugin(config: McpServerConfig, tool: Any) -> ToolPlugin:
    schema = tool.inputSchema or {}
    properties = dict(schema.get("properties") or {})
    required = tuple(schema.get("required") or ())

    def func(**arguments: Any) -> str:
        # Tool funcs run in a worker thread with no event loop, so a private
        # asyncio.run per call is safe.
        return asyncio.run(_call_tool(config, tool.name, arguments))

    return ToolPlugin(
        name=f"{config.name}_{tool.name}",
        description=f"[{config.name}] {tool.description or tool.name}",
        parameters=properties,
        required=required,
        func=func,
    )


async def register_mcp_tools(
    registry: ToolRegistry, servers: list[McpServerConfig]
) -> list[str]:
    """Attach every enabled server's tools; returns per-server errors."""
    errors: list[str] = []
    try:
        import mcp  # noqa: F401
    except ImportError:
        if any(s.enabled for s in servers):
            errors.append(
                "mcp_servers are configured but the mcp package is not installed; "
                "install cortex-brain[mcp]"
            )
        return errors
    for config in servers:
        if not config.enabled:
            continue
        try:
            tools = await asyncio.wait_for(_list_tools(config), timeout=30)
        except Exception as exc:  # noqa: BLE001 - a dead server must not kill startup
            errors.append(f"{config.name}: {exc}")
            continue
        for tool in tools:
            if _selected(tool.name, config):
                registry.register(_make_plugin(config, tool))
    return errors
