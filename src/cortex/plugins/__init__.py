"""The tool plugin contract.

One contract covers every tool the agent can call, wherever it comes from:
built-ins, drop-in ``plugins/*.py`` files in the brain, Python packages
exposing the ``cortex.tools`` entry point, skills, and MCP servers. A tool is
a name, a description the model reads, a JSON-schema parameter map, and a
callable returning a string.

Registration is not authorization: a plugin that touches anything sensitive
keeps its own checks inside the callable.
"""

from __future__ import annotations

import importlib.util
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolPlugin:
    name: str
    description: str
    func: Callable[..., str]
    # JSON-schema property map, e.g. {"query": {"type": "string", "description": ...}}
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    required: tuple[str, ...] = ()


@dataclass
class ToolOutcome:
    text: str
    ok: bool
    latency_ms: int


class ToolRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, ToolPlugin] = {}
        self.load_errors: list[str] = []

    def register(self, plugin: ToolPlugin) -> None:
        if plugin.name in self._plugins:
            raise ValueError(f"tool {plugin.name!r} is already registered")
        self._plugins[plugin.name] = plugin

    def get(self, name: str) -> ToolPlugin | None:
        return self._plugins.get(name)

    def plugins(self, allowlist: set[str] | None = None) -> list[ToolPlugin]:
        """All tools, or the allowlisted subset. None means unrestricted;
        an empty set means nothing — the two must never be conflated."""
        items = list(self._plugins.values())
        if allowlist is None:
            return items
        return [p for p in items if p.name in allowlist]

    def invoke(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        started = time.monotonic()
        plugin = self._plugins.get(name)
        if plugin is None:
            return ToolOutcome(f"Unknown tool {name!r}.", ok=False, latency_ms=0)
        missing = [r for r in plugin.required if r not in arguments]
        if missing:
            return ToolOutcome(
                f"Tool {name!r} is missing required arguments: {', '.join(missing)}.",
                ok=False,
                latency_ms=0,
            )
        try:
            text = plugin.func(**arguments)
        except Exception as exc:  # noqa: BLE001 - a broken tool must not kill the turn
            latency = int((time.monotonic() - started) * 1000)
            return ToolOutcome(f"Tool {name!r} failed: {exc}", ok=False, latency_ms=latency)
        latency = int((time.monotonic() - started) * 1000)
        return ToolOutcome(str(text), ok=True, latency_ms=latency)

    # -- wire schemas -----------------------------------------------------
    def openai_tools(self, allowlist: set[str] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": p.name,
                    "description": p.description,
                    "parameters": {
                        "type": "object",
                        "properties": p.parameters,
                        "required": list(p.required),
                        "additionalProperties": False,
                    },
                },
            }
            for p in self.plugins(allowlist)
        ]

    # -- discovery --------------------------------------------------------
    def load_directory(self, directory: Path, skip: set[str] | None = None) -> None:
        """Load drop-in plugins: every top-level ``*.py`` (not ``_``-prefixed)
        must expose ``register(registry)``. A broken file is recorded and
        skipped, never fatal. ``skip`` holds stems disabled in the dashboard."""
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_") or (skip and path.stem in skip):
                continue
            try:
                spec = importlib.util.spec_from_file_location(f"cortex_plugin_{path.stem}", path)
                if spec is None or spec.loader is None:
                    raise ImportError("could not build an import spec")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                register = getattr(module, "register", None)
                if not callable(register):
                    raise AttributeError("plugin defines no register(registry) function")
                register(self)
            except Exception as exc:  # noqa: BLE001
                self.load_errors.append(f"{path.name}: {exc}")

    def load_entry_points(self) -> None:
        """Installed packages advertise tools via the ``cortex.tools`` entry
        point group; each entry point is a ``register(registry)`` callable."""
        for ep in entry_points(group="cortex.tools"):
            try:
                ep.load()(self)
            except Exception as exc:  # noqa: BLE001
                self.load_errors.append(f"entry point {ep.name}: {exc}")
