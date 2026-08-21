"""The Brain: config + store + models + tool registry, one object.

Every surface (CLI, dashboard, MCP export) opens a Brain and works through
it. Conversation state lives in the LangGraph checkpointer; the messages
table here is the UI/audit copy.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel

from cortex.agent.graph import AgentRuntime
from cortex.config import BrainConfig, load_config
from cortex.memory.store import Store
from cortex.obs import Obs
from cortex.plugins import ToolRegistry
from cortex.plugins.builtin import register_builtin
from cortex.plugins.skills import load_skills, register_skill_tool
from cortex.providers import Embedder, ProviderError, chat_model
from cortex.providers.endpoint import endpoint_scope


class Brain:
    def __init__(self, root: Path):
        self.config: BrainConfig = load_config(root)
        self.store = Store(self.config.db_path)
        self.obs = Obs(self.config.usage_path)
        self.skills: list = []
        self.registry = ToolRegistry()
        self.load_extensions()
        self._reindex_hook = None
        self._chat_model: BaseChatModel | None = None
        self._embedder: Embedder | None = None
        self._embedder_checked = False

    def load_extensions(self) -> None:
        """(Re)build the tool registry and skill shelf from disk + database.

        Called at startup and again whenever the dashboard edits an
        extension, so a new plugin is live without restarting the server."""
        self.skills = [
            s
            for s in load_skills(self.config.skills_dir)
            if not self.store.is_disabled("skill", s.name)
        ]
        registry = ToolRegistry()
        register_builtin(registry, self)
        register_skill_tool(registry, self.skills)
        registry.load_directory(
            self.config.plugins_dir, skip=self.store.disabled_names("plugin")
        )
        registry.load_entry_points()
        self.registry = registry

    def request_reindex(self) -> None:
        """Ask the host to re-index soon. The dashboard wires this to its
        debounced worker; the CLI and MCP export leave it a no-op, since
        they re-index on their own schedule."""
        if self._reindex_hook is not None:
            self._reindex_hook()

    def mcp_servers(self) -> list:
        from cortex.extensions import effective_mcp_servers

        return effective_mcp_servers(self.config, self.store)

    def close(self) -> None:
        self.store.close()

    # -- models -----------------------------------------------------------
    def chat_model(self) -> BaseChatModel:
        if self._chat_model is None:
            profile = self.config.provider_for("chat")
            if profile is None:
                raise ProviderError(
                    "no chat provider configured; add a providers: block to cortex.yaml"
                )
            self._chat_model = chat_model(profile)
        return self._chat_model

    def chat_model_name(self) -> str:
        profile = self.config.provider_for("chat")
        return profile.chat_model if profile else ""

    def embedder(self) -> Embedder | None:
        if not self._embedder_checked:
            self._embedder_checked = True
            profile = self.config.provider_for("embed")
            if profile is not None and profile.embed_model:
                self._embedder = Embedder(profile)
        return self._embedder

    def warn_if_public(self, role: str = "chat") -> str | None:
        profile = self.config.provider_for(role)
        if profile is None or not profile.base_url:
            return None
        scope_kind = endpoint_scope(profile.base_url)
        if scope_kind == "public":
            return (
                f"{role} endpoint {profile.base_url} resolves to a public address: "
                "prompts and retrieved notes will leave this network."
            )
        if scope_kind == "unresolved":
            return f"{role} endpoint {profile.base_url} did not resolve."
        return None

    def embed_query_sync(self, query: str) -> list[float] | None:
        """Embed one query from sync tool code. Degrades to None (FTS-only
        search) when no embed provider is configured or the call fails."""
        embedder = self.embedder()
        if embedder is None:
            return None
        try:
            return asyncio.run(embedder.embed([query]))[0]
        except ProviderError as exc:
            print(f"embed failed, falling back to full-text: {exc}", file=sys.stderr)
            return None

    # -- agent ------------------------------------------------------------
    def runtime(self) -> AgentRuntime:
        """Async context manager; the server holds one open, the CLI opens
        one per command."""
        return AgentRuntime(self)

    def record_turn(self, thread: str, owner: str, user_text: str, answer: str) -> None:
        self.store.touch_thread(thread, owner, title_candidate=user_text)
        self.store.append_message(thread, "user", user_text)
        self.store.append_message(thread, "assistant", answer)

    @staticmethod
    def new_thread() -> str:
        return uuid.uuid4().hex[:12]
