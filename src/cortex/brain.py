"""The Brain: config + store + providers + tool registry, one object.

Every surface (CLI, HTTP server, MCP export) opens a Brain and works through
it, so behavior can never drift between surfaces.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

from cortex.agent.loop import run_turn
from cortex.agent.prompt import build_system_prompt
from cortex.config import BrainConfig, load_config
from cortex.events import AgentEvent
from cortex.memory.store import Store
from cortex.obs import Obs
from cortex.plugins import ToolRegistry
from cortex.plugins.builtin import register_builtin
from cortex.plugins.skills import load_skills, register_skill_tool
from cortex.providers import Provider, ProviderError, get_provider
from cortex.providers.endpoint import endpoint_scope

HISTORY_TURNS = 30


class Brain:
    def __init__(self, root: Path):
        self.config: BrainConfig = load_config(root)
        self.store = Store(self.config.db_path)
        self.obs = Obs(self.config.usage_path)
        self._providers: dict[str, Provider] = {}
        self.skills = load_skills(self.config.skills_dir)
        self.registry = ToolRegistry()
        register_builtin(self.registry, self)
        register_skill_tool(self.registry, self.skills)
        self.registry.load_directory(self.config.plugins_dir)
        self.registry.load_entry_points()
        self.mcp_errors: list[str] = []
        self._mcp_loaded = False

    async def attach_mcp(self) -> None:
        """Attach configured MCP servers' tools. Separate from __init__ so
        surfaces that never call tools (e.g. `cortex index`) skip the cost."""
        if self._mcp_loaded or not self.config.mcp_servers:
            self._mcp_loaded = True
            return
        from cortex.mcp.client import register_mcp_tools

        self.mcp_errors = await register_mcp_tools(self.registry, self.config.mcp_servers)
        self._mcp_loaded = True

    def close(self) -> None:
        self.store.close()

    # -- providers --------------------------------------------------------
    def provider(self, role: str) -> Provider | None:
        profile = self.config.provider_for(role)
        if profile is None:
            return None
        if profile.name not in self._providers:
            self._providers[profile.name] = get_provider(profile)
        return self._providers[profile.name]

    def warn_if_public(self, role: str = "chat") -> str | None:
        profile = self.config.provider_for(role)
        if profile is None or not profile.base_url:
            return None
        scope = endpoint_scope(profile.base_url)
        if scope == "public":
            return (
                f"{role} endpoint {profile.base_url} resolves to a public address: "
                "prompts and retrieved notes will leave this network."
            )
        if scope == "unresolved":
            return f"{role} endpoint {profile.base_url} did not resolve."
        return None

    def embed_query_sync(self, query: str) -> list[float] | None:
        """Embed one query from sync tool code. Degrades to None (FTS-only
        search) when no embed provider is configured or the call fails."""
        embedder = self.provider("embed")
        if embedder is None:
            return None
        try:
            return asyncio.run(embedder.embed([query]))[0]
        except (ProviderError, OSError) as exc:
            print(f"embed failed, falling back to full-text: {exc}", file=sys.stderr)
            return None

    # -- conversation -----------------------------------------------------
    def build_messages(self, thread: str, user_text: str) -> list[dict[str, Any]]:
        system = build_system_prompt(self.config.name, self.config.persona, self.skills)
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for row in self.store.history(thread, limit=HISTORY_TURNS):
            if row["role"] in ("user", "assistant"):
                messages.append({"role": row["role"], "content": row["body"]})
        messages.append({"role": "user", "content": user_text})
        return messages

    async def chat_turn(
        self,
        thread: str,
        user_text: str,
        on_event,
        stream: bool = True,
    ) -> str:
        provider = self.provider("chat")
        if provider is None:
            raise ProviderError(
                "no chat provider configured; add a providers: block to cortex.yaml"
            )
        await self.attach_mcp()
        for err in self.mcp_errors:
            await on_event(AgentEvent("notice", {"text": f"mcp: {err}"}))
        messages = self.build_messages(thread, user_text)
        self.store.append_message(thread, "user", user_text)
        answer = await run_turn(
            provider,
            self.registry,
            messages,
            on_event,
            obs=self.obs,
            thread=thread,
            stream=stream,
        )
        self.store.append_message(thread, "assistant", answer)
        return answer

    @staticmethod
    def new_thread() -> str:
        return uuid.uuid4().hex[:12]
