"""Manage the brain's extensions from the dashboard.

Four kinds, each keeping the contract it already had on disk:

* ``plugin``    — ``plugins/<name>.py`` exposing ``register(registry)``
* ``skill``     — ``skills/<name>/SKILL.md`` (agentskills.io)
* ``connector`` — ``connectors/<name>.py`` exposing ``sync(out_dir, settings)``
* ``mcp``       — an MCP server; dashboard-managed ones live in the database,
                  cortex.yaml ones stay file-owned and read-only here

Writing a plugin or connector through this module means **running code the
author typed, as the server user**. That is the same trust level as adding a
stdio MCP server, and it is why every route on top of this is admin-only. It
is not a sandbox and does not pretend to be one.

Enable/disable state lives in the database rather than in the file, so
toggling a broken extension off never edits someone's source.
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cortex.config import BrainConfig, McpServerConfig
from cortex.plugins import ToolRegistry
from cortex.plugins.skills import parse_skill, render_skill

if TYPE_CHECKING:
    from cortex.memory.store import Store

KINDS = ("plugin", "skill", "connector", "mcp")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

PLUGIN_TEMPLATE = '''\
"""A cortex tool plugin. The whole contract is register(registry)."""

from cortex.plugins import ToolPlugin


def register(registry):
    def hello(name: str = "world") -> str:
        return f"hello, {name}"

    registry.register(
        ToolPlugin(
            name="hello",
            description="Say hello. Replace this with something useful.",
            parameters={"name": {"type": "string", "description": "Who to greet."}},
            func=hello,
        )
    )
'''

CONNECTOR_TEMPLATE = '''\
"""A cortex ingestion connector.

Distill, don't dump: write markdown that retrieves well, delete items that
disappeared, and return early when unconfigured.
"""


def sync(out_dir, settings):
    if not settings.get("enabled"):
        return
    (out_dir / "example.md").write_text(
        "# Example\\n\\nReplace this with real ingested content.\\n",
        encoding="utf-8",
    )
'''

SKILL_TEMPLATE = """\
1. Describe the first step of the procedure.
2. Then the next one.

Keep it concrete — the agent follows these instructions literally.
"""


class ExtensionError(ValueError):
    pass


@dataclass
class ExtensionInfo:
    kind: str
    name: str
    enabled: bool = True
    # plugin: the tool names it registers; mcp: nothing until the agent attaches
    tools: list[str] = field(default_factory=list)
    description: str = ""
    error: str = ""
    source: str = "dashboard"  # "dashboard" (editable) or "file" (read-only)
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def validate_name(name: str) -> str:
    name = name.strip().lower()
    if not NAME_RE.match(name):
        raise ExtensionError(
            "names are 1-48 chars of lowercase letters, digits, '-' or '_'"
        )
    return name


# -- paths -----------------------------------------------------------------


def _plugin_path(config: BrainConfig, name: str) -> Path:
    return config.plugins_dir / f"{validate_name(name)}.py"


def _connector_path(config: BrainConfig, name: str) -> Path:
    return config.connectors_dir / f"{validate_name(name)}.py"


def _skill_path(config: BrainConfig, name: str) -> Path:
    return config.skills_dir / validate_name(name) / "SKILL.md"


# -- validation ------------------------------------------------------------


def check_plugin(code: str, name: str) -> list[str]:
    """Load the code in a scratch registry and return the tools it registers.

    This *executes* the module — the same thing that happens at startup — so
    a syntax error or a bad register() surfaces here instead of at the next
    agent turn."""
    scratch = ToolRegistry()
    module_name = f"cortex_plugin_check_{name}"
    spec = importlib.util.spec_from_loader(module_name, loader=None)
    if spec is None:
        raise ExtensionError("could not prepare the module")
    module = importlib.util.module_from_spec(spec)
    try:
        exec(compile(code, f"{name}.py", "exec"), module.__dict__)  # noqa: S102
    except Exception as exc:  # noqa: BLE001
        raise ExtensionError(f"plugin failed to load: {exc}") from exc
    register = getattr(module, "register", None)
    if not callable(register):
        raise ExtensionError("plugin defines no register(registry) function")
    try:
        register(scratch)
    except Exception as exc:  # noqa: BLE001
        raise ExtensionError(f"register() failed: {exc}") from exc
    tools = [p.name for p in scratch.plugins()]
    if not tools:
        raise ExtensionError("register() registered no tools")
    return tools


def check_connector(code: str, name: str) -> None:
    module_name = f"cortex_connector_check_{name}"
    spec = importlib.util.spec_from_loader(module_name, loader=None)
    if spec is None:
        raise ExtensionError("could not prepare the module")
    module = importlib.util.module_from_spec(spec)
    try:
        exec(compile(code, f"{name}.py", "exec"), module.__dict__)  # noqa: S102
    except Exception as exc:  # noqa: BLE001
        raise ExtensionError(f"connector failed to load: {exc}") from exc
    if not callable(getattr(module, "sync", None)):
        raise ExtensionError("connector defines no sync(out_dir, settings) function")


# -- listing ---------------------------------------------------------------


def list_all(config: BrainConfig, store: Store) -> dict[str, list[dict]]:
    return {
        "plugins": [e.as_dict() for e in _list_plugins(config, store)],
        "skills": [e.as_dict() for e in _list_skills(config, store)],
        "connectors": [e.as_dict() for e in _list_connectors(config, store)],
        "mcp_servers": [e.as_dict() for e in _list_mcp(config, store)],
    }


def _list_plugins(config: BrainConfig, store: Store) -> list[ExtensionInfo]:
    out: list[ExtensionInfo] = []
    if not config.plugins_dir.is_dir():
        return out
    for path in sorted(config.plugins_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        name = path.stem
        info = ExtensionInfo(
            kind="plugin", name=name, enabled=not store.is_disabled("plugin", name)
        )
        try:
            info.tools = check_plugin(path.read_text(encoding="utf-8"), name)
        except ExtensionError as exc:
            info.error = str(exc)
        out.append(info)
    return out


def _list_skills(config: BrainConfig, store: Store) -> list[ExtensionInfo]:
    out: list[ExtensionInfo] = []
    if not config.skills_dir.is_dir():
        return out
    for skill_md in sorted(config.skills_dir.glob("*/SKILL.md")):
        name = skill_md.parent.name
        info = ExtensionInfo(
            kind="skill", name=name, enabled=not store.is_disabled("skill", name)
        )
        parsed = parse_skill(skill_md.read_text(encoding="utf-8"))
        if parsed is None:
            info.error = "SKILL.md has no frontmatter name"
        else:
            info.description = parsed.description
        out.append(info)
    return out


def _list_connectors(config: BrainConfig, store: Store) -> list[ExtensionInfo]:
    from cortex.connectors import builtin_connectors

    settings = store.connector_settings()
    out: list[ExtensionInfo] = []
    for name in sorted(builtin_connectors()):
        merged = {**(config.connectors.get(name) or {}), **(settings.get(name) or {})}
        out.append(
            ExtensionInfo(
                kind="connector",
                name=name,
                enabled=not store.is_disabled("connector", name),
                description="built-in",
                source="builtin",
                detail={"settings": merged},
            )
        )
    if config.connectors_dir.is_dir():
        for path in sorted(config.connectors_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            name = path.stem
            info = ExtensionInfo(
                kind="connector",
                name=name,
                enabled=not store.is_disabled("connector", name),
                detail={
                    "settings": {
                        **(config.connectors.get(name) or {}),
                        **(settings.get(name) or {}),
                    }
                },
            )
            try:
                check_connector(path.read_text(encoding="utf-8"), name)
            except ExtensionError as exc:
                info.error = str(exc)
            out.append(info)
    return out


def _list_mcp(config: BrainConfig, store: Store) -> list[ExtensionInfo]:
    out: list[ExtensionInfo] = []
    file_names = set()
    for server in config.mcp_servers:
        file_names.add(server.name)
        out.append(
            ExtensionInfo(
                kind="mcp",
                name=server.name,
                enabled=server.enabled,
                source="file",
                description=f"{server.transport} (cortex.yaml)",
                detail=_mcp_detail(server),
            )
        )
    for row in store.list_mcp_servers():
        if row["name"] in file_names:
            continue  # a file entry of the same name wins
        server = _row_to_mcp(row)
        out.append(
            ExtensionInfo(
                kind="mcp",
                name=server.name,
                enabled=server.enabled,
                description=server.transport,
                detail=_mcp_detail(server),
            )
        )
    return out


def _mcp_detail(server: McpServerConfig) -> dict:
    return {
        "transport": server.transport,
        "command": server.command,
        "args": list(server.args),
        "url": server.url,
        "include": list(server.include),
        "exclude": list(server.exclude),
        # headers can hold bearer tokens; never send their values to a client
        "header_keys": sorted(server.headers),
    }


def _row_to_mcp(row) -> McpServerConfig:
    import json

    spec = json.loads(row["spec"])
    return McpServerConfig(
        name=row["name"],
        transport=spec.get("transport", "stdio"),
        command=spec.get("command", ""),
        args=tuple(spec.get("args") or ()),
        url=spec.get("url", ""),
        headers=dict(spec.get("headers") or {}),
        include=tuple(spec.get("include") or ()),
        exclude=tuple(spec.get("exclude") or ()),
        enabled=bool(row["enabled"]),
    )


def effective_mcp_servers(config: BrainConfig, store: Store) -> list[McpServerConfig]:
    """cortex.yaml servers plus dashboard-managed ones; file entries win on
    a name clash, so a hand-written config is never shadowed."""
    servers = list(config.mcp_servers)
    known = {s.name for s in servers}
    for row in store.list_mcp_servers():
        if row["name"] not in known:
            servers.append(_row_to_mcp(row))
    return servers


def effective_connectors(config: BrainConfig, store: Store) -> dict[str, dict]:
    """cortex.yaml connector settings with dashboard overrides merged in,
    minus anything disabled."""
    merged: dict[str, dict] = {k: dict(v or {}) for k, v in config.connectors.items()}
    for name, settings in store.connector_settings().items():
        merged.setdefault(name, {}).update(settings)
    if config.connectors_dir.is_dir():
        for path in config.connectors_dir.glob("*.py"):
            if not path.name.startswith("_"):
                merged.setdefault(path.stem, {})
    return {n: s for n, s in merged.items() if not store.is_disabled("connector", n)}


# -- reads / writes --------------------------------------------------------


def read_source(config: BrainConfig, kind: str, name: str) -> dict:
    """The editable body of one extension."""
    if kind == "plugin":
        path = _plugin_path(config, name)
    elif kind == "connector":
        path = _connector_path(config, name)
    elif kind == "skill":
        path = _skill_path(config, name)
    else:
        raise ExtensionError(f"{kind} has no editable source")
    if not path.is_file():
        raise FileNotFoundError(name)
    text = path.read_text(encoding="utf-8")
    if kind == "skill":
        parsed = parse_skill(text)
        return {
            "kind": kind,
            "name": name,
            "description": parsed.description if parsed else "",
            "instructions": parsed.instructions if parsed else text,
        }
    return {"kind": kind, "name": name, "code": text}


def write_plugin(config: BrainConfig, name: str, code: str) -> list[str]:
    name = validate_name(name)
    tools = check_plugin(code, name)  # refuse to save something that cannot load
    path = _plugin_path(config, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return tools


def write_connector(config: BrainConfig, name: str, code: str) -> None:
    name = validate_name(name)
    check_connector(code, name)
    path = _connector_path(config, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


def write_skill(config: BrainConfig, name: str, description: str, instructions: str) -> None:
    name = validate_name(name)
    from cortex.plugins.skills import Skill

    if not instructions.strip():
        raise ExtensionError("a skill needs instructions")
    path = _skill_path(config, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_skill(Skill(name=name, description=description, instructions=instructions)),
        encoding="utf-8",
    )


def delete_extension(config: BrainConfig, store: Store, kind: str, name: str) -> None:
    name = validate_name(name)
    if kind == "plugin":
        _plugin_path(config, name).unlink(missing_ok=True)
    elif kind == "connector":
        _connector_path(config, name).unlink(missing_ok=True)
        store.delete_connector_settings(name)
    elif kind == "skill":
        path = _skill_path(config, name)
        path.unlink(missing_ok=True)
        parent = path.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    elif kind == "mcp":
        if any(s.name == name for s in config.mcp_servers):
            raise ExtensionError(
                f"{name} is defined in cortex.yaml — edit the file to remove it"
            )
        store.delete_mcp_server(name)
    else:
        raise ExtensionError(f"unknown extension kind {kind!r}")
    store.set_disabled(kind, name, False)


def save_mcp_server(config: BrainConfig, store: Store, spec: dict) -> str:
    name = validate_name(str(spec.get("name", "")))
    if any(s.name == name for s in config.mcp_servers):
        raise ExtensionError(f"{name} is defined in cortex.yaml — edit the file instead")
    transport = str(spec.get("transport") or "stdio")
    if transport not in ("stdio", "http"):
        raise ExtensionError("transport is stdio or http")
    if transport == "stdio" and not str(spec.get("command") or "").strip():
        raise ExtensionError("a stdio server needs a command")
    if transport == "http" and not str(spec.get("url") or "").strip():
        raise ExtensionError("an http server needs a url")
    payload = {
        "transport": transport,
        "command": str(spec.get("command") or ""),
        "args": [str(a) for a in (spec.get("args") or [])],
        "url": str(spec.get("url") or ""),
        "headers": {str(k): str(v) for k, v in (spec.get("headers") or {}).items()},
        "include": [str(a) for a in (spec.get("include") or [])],
        "exclude": [str(a) for a in (spec.get("exclude") or [])],
    }
    store.upsert_mcp_server(name, payload, bool(spec.get("enabled", True)))
    return name


def set_connector_settings(store: Store, name: str, settings: dict) -> None:
    store.set_connector_settings(validate_name(name), settings)


def set_enabled(config: BrainConfig, store: Store, kind: str, name: str, enabled: bool) -> None:
    if kind not in KINDS:
        raise ExtensionError(f"unknown extension kind {kind!r}")
    name = validate_name(name)
    if kind == "mcp" and not any(s.name == name for s in config.mcp_servers):
        store.set_mcp_enabled(name, enabled)
        return
    if kind == "mcp":
        raise ExtensionError(f"{name} is defined in cortex.yaml — edit the file instead")
    store.set_disabled(kind, name, not enabled)


def scaffold(kind: str) -> dict:
    """Starter content so "New" opens something that already works."""
    if kind == "plugin":
        return {"code": PLUGIN_TEMPLATE}
    if kind == "connector":
        return {"code": CONNECTOR_TEMPLATE}
    if kind == "skill":
        return {"description": "", "instructions": SKILL_TEMPLATE}
    raise ExtensionError(f"{kind} has no scaffold")
