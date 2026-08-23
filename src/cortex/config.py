"""Brain configuration.

A brain is a directory with a ``cortex.yaml`` at its root. Everything cortex
knows lives under that directory, so backing up a brain is copying a folder
and running two brains side by side is running the CLI twice with different
``--brain`` paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_NAME = "cortex.yaml"


@dataclass(frozen=True)
class ProviderProfile:
    """One model endpoint. ``kind`` is the wire protocol, not the vendor:
    ``openai`` speaks the OpenAI-compatible chat-completions API (Ollama,
    vLLM, LM Studio, OpenRouter, OpenAI itself), ``anthropic`` speaks the
    Anthropic Messages API."""

    name: str
    kind: str = "openai"
    base_url: str = ""
    api_key: str = ""
    api_key_env: str = ""
    chat_model: str = ""
    embed_model: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    def key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""


@dataclass(frozen=True)
class McpServerConfig:
    """An external MCP server this brain consumes as extra tools."""

    name: str
    transport: str = "stdio"  # "stdio" or "http"
    command: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()  # exclude wins over include
    enabled: bool = True


@dataclass
class BrainConfig:
    root: Path
    name: str = "cortex"
    persona: str = ""
    providers: dict[str, ProviderProfile] = field(default_factory=dict)
    # role -> provider profile name. Roles: "chat", "embed". A missing role
    # falls back to the first declared provider; a missing "embed" role with a
    # provider that has no embed_model disables vector search (FTS still works).
    roles: dict[str, str] = field(default_factory=dict)
    mcp_servers: list[McpServerConfig] = field(default_factory=list)
    connectors: dict[str, dict] = field(default_factory=dict)
    extra_paths: list[Path] = field(default_factory=list)

    # -- layout -----------------------------------------------------------
    @property
    def vaults_dir(self) -> Path:
        return self.root / "vaults"

    @property
    def shared_vault(self) -> Path:
        return self.vaults_dir / "shared"

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def templates_dir(self) -> Path:
        return self.root / "templates"

    @property
    def skills_dir(self) -> Path:
        return self.root / "skills"

    @property
    def plugins_dir(self) -> Path:
        return self.root / "plugins"

    @property
    def connectors_dir(self) -> Path:
        return self.root / "connectors"

    @property
    def state_dir(self) -> Path:
        return self.root / ".cortex"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "index.db"

    @property
    def usage_path(self) -> Path:
        return self.state_dir / "usage.jsonl"

    def vault_roots(self) -> list[Path]:
        if not self.vaults_dir.is_dir():
            return []
        return sorted(p for p in self.vaults_dir.iterdir() if p.is_dir())

    def indexed_roots(self) -> list[Path]:
        roots = self.vault_roots() + [self.sources_dir]
        roots += [p for p in self.extra_paths]
        return [r for r in roots if r.is_dir()]

    def root_pairs(self) -> list[tuple[str, Path]]:
        """(index-key prefix, filesystem root) for everything indexed:
        "vaults/shared", "vaults/<user>", "sources", plus extra-path names."""
        pairs = [(f"vaults/{root.name}", root) for root in self.vault_roots()]
        if self.sources_dir.is_dir():
            pairs.append(("sources", self.sources_dir))
        pairs += [(p.name, p) for p in self.extra_paths if p.is_dir()]
        return pairs

    def resolve_key(self, key: str) -> Path | None:
        """Map an index key like "vaults/shared/garden.md" to its file,
        refusing traversal outside the mapped root. None for anything else."""
        for prefix, root in self.root_pairs():
            if key.startswith(prefix + "/"):
                rest = key[len(prefix) + 1 :]
                target = (root / rest).resolve()
                if str(target).startswith(str(root.resolve()) + "/"):
                    return target
                return None
        return None

    # -- providers --------------------------------------------------------
    def provider_for(self, role: str) -> ProviderProfile | None:
        if not self.providers:
            return None
        name = self.roles.get(role)
        if name is None and role != "chat":
            # embed falls back to the chat provider only if it can embed
            fallback = self.provider_for("chat")
            if fallback is not None and fallback.embed_model:
                return fallback
            return None
        if name is None:
            return next(iter(self.providers.values()))
        profile = self.providers.get(name)
        if profile is None:
            raise ConfigError(f"roles.{role} points at unknown provider {name!r}")
        return profile


class ConfigError(ValueError):
    pass


def _profile(name: str, raw: dict) -> ProviderProfile:
    known = {f for f in ProviderProfile.__dataclass_fields__ if f != "name"}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"provider {name!r}: unknown keys {sorted(unknown)}")
    return ProviderProfile(name=name, **{k: raw[k] for k in raw})


def _mcp(raw: dict) -> McpServerConfig:
    raw = dict(raw)
    for tup in ("args", "include", "exclude"):
        if tup in raw:
            raw[tup] = tuple(raw[tup])
    return McpServerConfig(**raw)


def load_config(root: Path) -> BrainConfig:
    root = root.resolve()
    path = root / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(f"{path} does not exist; run `cortex init {root}` first")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")

    providers = {
        name: _profile(name, spec or {}) for name, spec in (raw.get("providers") or {}).items()
    }
    mcp_servers = [
        _mcp({"name": name, **(spec or {})})
        for name, spec in (raw.get("mcp_servers") or {}).items()
    ]
    extra = [Path(p).expanduser() for p in (raw.get("extra_paths") or [])]

    return BrainConfig(
        root=root,
        name=str(raw.get("name") or "cortex"),
        persona=str(raw.get("persona") or ""),
        providers=providers,
        roles=dict(raw.get("roles") or {}),
        mcp_servers=mcp_servers,
        connectors=dict(raw.get("connectors") or {}),
        extra_paths=extra,
    )


def find_brain(explicit: str | None = None) -> Path:
    """Resolve the brain directory: --brain flag, then $CORTEX_BRAIN, then an
    ancestor of the working directory containing cortex.yaml."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("CORTEX_BRAIN")
    if env:
        return Path(env).expanduser().resolve()
    cur = Path.cwd()
    for candidate in (cur, *cur.parents):
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    raise ConfigError(
        "no brain found: pass --brain, set CORTEX_BRAIN, or run inside a "
        "directory created by `cortex init`"
    )
