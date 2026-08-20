# Changelog

Notable changes per release. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [semantic versioning](https://semver.org/), with the caveat
that while cortex is `0.x` the minor number carries breaking changes.

## [Unreleased]

## [0.2.1] - 2026-08-21

### Fixed

- **The dashboard would not start when any MCP server was configured.**
  `langchain-mcp-adapters` 0.2 imports `RequestContext` from
  `mcp.shared.context`, which `mcp` 2.0 removed, and it needs
  `langchain-core` 1.x while cortex is on 0.3 — but its declared ranges
  express neither constraint, so a fresh install of 0.2.0 resolved to a
  combination that cannot import. Both dependencies are now upper-bounded
  (`langchain-mcp-adapters<0.2`, `mcp<2`), verified by an import test.
- The MCP adapter import is guarded, so an unusable adapter costs you MCP
  tools and reports why, instead of taking server startup down with it.
  Per-server failures were already isolated; this module-level import was
  the one path that was not.

### Changed

- The product film's fourth scene now shows the Extend panel — a plugin
  written in the browser, saved, and appearing with the tool it registered —
  instead of the Import and Admin tabs it was filmed with before that panel
  existed.

## [0.2.0] - 2026-08-20

First public release, published to PyPI as `cortxai`. The import name and
the CLI stay `cortex`.

### Added

- **Dashboard** — a React 18 single-page app served by the package itself,
  no node needed at install time.
  - *Chat*: private agent threads with server-sent streaming, live tool-call
    activity, and answers that cite files by path; clicking a citation opens
    it in the vault.
  - *Channels*: peer chat over WebSocket. Mentioning `@cortex` makes the
    agent reply in-channel, scoped to the shared vault only.
  - *Vault*: shared and per-user vaults edited in the browser with
    Obsidian-flavored rendering — wikilinks, embeds, callouts, frontmatter,
    write-through task checkboxes, tags. Ctrl-S saves with conflict
    detection (409 plus a banner) instead of a silent clobber.
  - *Import*: bring an existing Obsidian vault by zip, git URL, or server path.
  - *Extend*: admin-only management of plugins, skills, connectors and MCP
    servers, including writing plugin and connector code in the browser.
  - *Admin*: accounts, roles, index stats.
- **Accounts and scope** — scrypt passwords, HMAC-signed HttpOnly cookie
  sessions, `admin`/`member` roles. Every search, grep and tool call is
  scoped to what the caller may read, filtered inside the SQL query rather
  than trimmed after it.
- **Agent** — LangGraph's ReAct graph over LangChain chat models, with an
  `AsyncSqliteSaver` checkpoint per thread. One OpenAI-compatible wire
  covers Ollama, vLLM, LM Studio, OpenRouter and a LiteLLM proxy; Anthropic
  is spoken directly. Chat and embedding roles can use different providers.
- **Memory** — SQLite FTS5 and in-process vector cosine, fused with
  reciprocal rank fusion and nudged by recency. The index rebuilds itself
  when the chunk schema or the embedding model changes.
- **Extensions** — four surfaces: tool plugins (`plugins/*.py` or a
  `cortex.tools` entry point), MCP servers, agentskills.io skills, and
  ingestion connectors. Plugin and connector code is loaded before it is
  saved, so code that cannot import is refused with the loader's own message
  and nothing lands on disk; a successful save rebuilds the agent so new
  tools are live without a restart.
- **Surfaces** — `cortex setup` wizard, `cortex serve` dashboard,
  `cortex chat` terminal REPL, `cortex mcp` stdio export for Claude Code /
  Cursor / Hermes, `cortex ext` extension management, `cortex connectors run`.
- **Deployment** — `install.sh` (pipx / uv / venv autodetect), a Dockerfile
  that builds the SPA and the package, and a docker-compose file.
- **Observability** — every model and tool call appends JSONL to
  `.cortex/usage.jsonl` using preflight's field names. Absent token counts
  stay absent rather than becoming zeros, and telemetry never makes a call
  fail.
- **Docs** — a published site with a 62-second product film recorded from
  the running product, plus `docs/product-spec.md` as the frontend/backend
  contract.

### Security

- Endpoints are classified by network facts: private, loopback, CGNAT and
  Tailscale addresses are trusted; anything public produces a plain warning
  that prompts and notes will leave the network.
- The Extend panel executes code that admins write. This is stated in the
  panel, the README and the docs rather than buried: it is the same trust
  level as configuring a stdio MCP server, and it is why the panel is
  admin-only.
- MCP header values are never returned to a client; only their key names are.

### Known limits

- Vector search is exact cosine in-process — right for personal and team
  brains, wrong for millions of chunks.
- Vault writes are last-writer-wins with conflict *detection*, not merging.
- The calendar connector does not expand recurrence rules yet.
- Cortex hosts no model; you bring an endpoint.

[Unreleased]: https://github.com/Unchained-Labs/cortex/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/Unchained-Labs/cortex/releases/tag/v0.2.1
[0.2.0]: https://github.com/Unchained-Labs/cortex/releases/tag/v0.2.0
