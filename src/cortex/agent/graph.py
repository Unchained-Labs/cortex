"""The agent runtime: a LangGraph ReAct agent over the brain's tools.

``create_react_agent`` supplies the loop; conversation state lives in an
``AsyncSqliteSaver`` checkpoint per thread, so history is the graph's
problem, not the caller's. Local ``ToolPlugin``s are adapted to LangChain
``StructuredTool``s from their declared JSON schemas; MCP servers attach
through ``langchain-mcp-adapters``. Everything streams out as AgentEvents,
the same shape every surface consumes.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from langchain_core.tools import StructuredTool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.prebuilt import create_react_agent
from pydantic import Field, create_model

from cortex.config import McpServerConfig
from cortex.events import AgentEvent
from cortex.plugins import ToolPlugin, ToolRegistry

if TYPE_CHECKING:
    from cortex.brain import Brain

_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}

RECURSION_LIMIT = 40  # ~16 model/tool rounds


def _args_model(plugin: ToolPlugin):
    fields: dict[str, Any] = {}
    for name, schema in plugin.parameters.items():
        py_type = _JSON_TYPES.get(schema.get("type", "string"), str)
        description = schema.get("description", "")
        if name in plugin.required:
            fields[name] = (py_type, Field(description=description))
        else:
            fields[name] = (py_type | None, Field(default=None, description=description))
    return create_model(f"{plugin.name}_args", **fields)


def adapt_registry(registry: ToolRegistry) -> list[StructuredTool]:
    """Every ToolPlugin becomes a StructuredTool whose executor is the
    registry's isolating invoke — a broken plugin returns an error string
    to the model instead of killing the turn."""
    tools: list[StructuredTool] = []
    for plugin in registry.plugins():

        def make(p: ToolPlugin):
            async def run(**kwargs: Any) -> str:
                arguments = {k: v for k, v in kwargs.items() if v is not None}
                outcome = await asyncio.to_thread(registry.invoke, p.name, arguments)
                return outcome.text

            return StructuredTool.from_function(
                coroutine=run,
                name=p.name,
                description=p.description,
                args_schema=_args_model(p),
            )

        tools.append(make(plugin))
    return tools


async def load_mcp_tools(
    servers: list[McpServerConfig],
) -> tuple[list[Any], list[str]]:
    """Attach configured MCP servers; per-server failures are reported,
    never fatal. include limits, exclude wins."""
    errors: list[str] = []
    tools: list[Any] = []
    enabled = [s for s in servers if s.enabled]
    if not enabled:
        return tools, errors
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:
        # An unusable adapter (a broken install, or an mcp release the
        # adapter has not caught up with) must cost you MCP tools, not the
        # whole brain: this import used to run unguarded and took server
        # startup down with it.
        errors.append(f"MCP support unavailable: {exc}")
        return tools, errors

    for config in enabled:
        if config.transport == "stdio":
            connection: dict[str, Any] = {
                "transport": "stdio",
                "command": config.command,
                "args": list(config.args),
            }
        else:
            connection = {
                "transport": "streamable_http",
                "url": config.url,
                "headers": dict(config.headers) or None,
            }
        client = MultiServerMCPClient({config.name: connection})
        try:
            server_tools = await asyncio.wait_for(client.get_tools(), timeout=30)
        except Exception as exc:  # noqa: BLE001 - a dead server must not kill startup
            errors.append(f"{config.name}: {exc}")
            continue
        for tool in server_tools:
            if tool.name in config.exclude:
                continue
            if config.include and tool.name not in config.include:
                continue
            tools.append(tool)
    return tools, errors


class AgentRuntime:
    """Open once per process; holds the checkpointer and the compiled graph."""

    def __init__(self, brain: Brain):
        self.brain = brain
        self._saver_cm = None
        self.agent = None
        self.mcp_errors: list[str] = []
        self.startup_error: str = ""

    async def __aenter__(self) -> AgentRuntime:
        from cortex.agent.prompt import build_system_prompt
        from cortex.providers import ProviderError

        try:
            model = self.brain.chat_model()
        except ProviderError as exc:
            # The dashboard still serves vaults and peer chat without a
            # model; agent turns fail with this message instead.
            self.startup_error = str(exc)
            return self
        tools = adapt_registry(self.brain.registry)
        mcp_tools, self.mcp_errors = await load_mcp_tools(self.brain.mcp_servers())
        known = {t.name for t in tools}
        for tool in mcp_tools:
            if tool.name in known:
                self.mcp_errors.append(f"tool name collision, skipped: {tool.name}")
                continue
            known.add(tool.name)
            tools.append(tool)

        self._saver_cm = AsyncSqliteSaver.from_conn_string(
            str(self.brain.config.state_dir / "checkpoints.db")
        )
        saver = await self._saver_cm.__aenter__()
        from cortex import identity as identitymod

        prompt = build_system_prompt(
            self.brain.config.name,
            identitymod.effective(self.brain.config),
            self.brain.skills,
        )
        self.agent = create_react_agent(model, tools, prompt=prompt, checkpointer=saver)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._saver_cm is not None:
            await self._saver_cm.__aexit__(*exc)

    async def run(self, thread: str, user_text: str, on_event) -> str:
        """One turn. History comes from the checkpointer; scope from the
        caller's ContextVars (set before calling)."""
        if self.agent is None:
            from cortex.providers import ProviderError

            raise ProviderError(self.startup_error or "AgentRuntime is not open")
        config = {
            "configurable": {"thread_id": thread},
            "recursion_limit": RECURSION_LIMIT,
        }
        final: list[str] = []
        started: dict[str, float] = {}
        streamed: set[str] = set()
        model_name = self.brain.chat_model_name()
        await on_event(AgentEvent("start", {"model": model_name}))

        async for event in self.agent.astream_events(
            {"messages": [("user", user_text)]}, config=config, version="v2"
        ):
            kind = event["event"]
            if kind == "on_chat_model_start":
                started[event["run_id"]] = time.monotonic()
                final.clear()  # a new model call supersedes pre-tool commentary
            elif kind == "on_chat_model_stream":
                text = _chunk_text(event["data"]["chunk"])
                if text:
                    streamed.add(event["run_id"])
                    final.append(text)
                    await on_event(AgentEvent("token", {"text": text}))
            elif kind == "on_chat_model_end":
                latency = int(
                    (time.monotonic() - started.pop(event["run_id"], time.monotonic())) * 1000
                )
                output = event["data"].get("output")
                usage = _usage_of(output)
                self.brain.obs.usage(model_name, usage, latency, thread)
                # A model (or endpoint) that never streamed still answered:
                # surface its full content as one late token.
                if event["run_id"] not in streamed and not getattr(output, "tool_calls", None):
                    text = _chunk_text(output)
                    if text:
                        final.append(text)
                        await on_event(AgentEvent("token", {"text": text}))
            elif kind == "on_tool_start":
                started[event["run_id"]] = time.monotonic()
                await on_event(
                    AgentEvent(
                        "tool_start",
                        {"name": event["name"], "arguments": event["data"].get("input") or {}},
                    )
                )
            elif kind == "on_tool_end":
                latency = int(
                    (time.monotonic() - started.pop(event["run_id"], time.monotonic())) * 1000
                )
                output = event["data"].get("output")
                text = str(getattr(output, "content", output) or "")
                ok = not text.startswith(("Tool ", "Unknown tool", "Error:"))
                self.brain.obs.tool_event(event["name"], ok=ok, latency_ms=latency)
                await on_event(
                    AgentEvent(
                        "tool_end",
                        {
                            "name": event["name"],
                            "ok": ok,
                            "latency_ms": latency,
                            "preview": text[:400],
                        },
                    )
                )

        answer = "".join(final)
        await on_event(AgentEvent("done", {"text": answer}))
        return answer


def _chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # anthropic block deltas
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


def _usage_of(output: Any) -> dict[str, int]:
    """usage_metadata → preflight field names; absent counts stay absent."""
    meta = getattr(output, "usage_metadata", None) or {}
    usage: dict[str, int] = {}
    if meta.get("input_tokens") is not None:
        usage["prompt_tokens"] = int(meta["input_tokens"])
    if meta.get("output_tokens") is not None:
        usage["completion_tokens"] = int(meta["output_tokens"])
    return usage
