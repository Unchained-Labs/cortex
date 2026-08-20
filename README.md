<div align="center">
  <img src="docs/assets/lockup-horizontal.svg" width="300" alt="Unchained Labs">
</div>

# cortex

Turns a folder of notes and sources into a private brain you can chat with —
on your own model, on your own machine.

**Status: alpha.** The CLI surface (`init`, `index`, `chat`, `serve`, `mcp`,
`connectors`, `keys`, `status`) is settling; config keys and the plugin
contract may still change between minor versions.

```sh
pip install 'cortex-brain[server,mcp]'
cortex init ~/brains/home        # scaffolds the brain and its cortex.yaml
cortex index --brain ~/brains/home
cortex chat  --brain ~/brains/home
```

**What it does not do:** cortex hosts no model. It speaks to an
OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, OpenRouter, OpenAI) or
the Anthropic API — you bring one. It does not sync git repositories or crawl
forges, it ships one calendar connector (single events only, no recurrence
expansion), and its vector search is exact cosine in-process: built for
personal- and team-sized brains, not millions of chunks. Multi-user auth is a
shared `ctx_` key, not accounts.

## What a brain is

A directory. `cortex init` creates:

```
~/brains/home/
├── cortex.yaml     # providers, roles, mcp servers, connectors
├── notes/          # yours — an Obsidian vault clone works as-is
├── sources/        # connector output, one folder per connector
├── skills/         # agentskills.io SKILL.md procedure folders
├── plugins/        # drop-in tool plugins (*.py)
├── connectors/     # drop-in ingestion connectors (*.py)
└── .cortex/        # index + conversation state (disposable cache)
```

Back it up by copying the folder. Run a second brain (home / company / club)
by running `cortex init` again somewhere else.

## Pluggable models

`cortex.yaml` declares provider profiles and assigns them roles:

```yaml
providers:
  local:
    kind: openai                    # the wire protocol, not the vendor
    base_url: "http://localhost:11434/v1"
    chat_model: qwen3
    embed_model: nomic-embed-text
  claude:
    kind: anthropic
    api_key_env: ANTHROPIC_API_KEY
    chat_model: claude-sonnet-5
roles:
  chat: claude
  embed: local
```

Chat and embedding can come from different endpoints. Endpoints are
classified by network facts: private, loopback, link-local, CGNAT, and
Tailscale addresses are trusted; anything public gets a plain warning that
your notes will leave the network. Without an embed provider, search degrades
to full-text and says so — it never fakes a vector score.

## Retrieval

Hybrid search over everything indexed: SQLite FTS5 and vector cosine ranked
separately, fused with reciprocal rank fusion, nudged by recency. No single
scorer is trusted on its own — the design that worked in
[Cerebras' knowledge base](https://www.cerebras.ai/blog/how-we-built-our-knowledge-base).
Chunking keeps markdown heading paths and
code definition boundaries, and the index re-builds itself from scratch when
the chunk schema *or* the embedding model changes, because silently mixing
vector spaces is corruption, not compatibility.

## Four ways to extend it

| Extension | Contract | Runs |
| :--- | :--- | :--- |
| Tool plugin | `plugins/*.py` exposing `register(registry)`, or a package with a `cortex.tools` entry point | at agent time |
| MCP server | `mcp_servers:` block in cortex.yaml (stdio or streamable HTTP) | at agent time |
| Skill | `skills/<name>/SKILL.md` (agentskills.io format) | loaded lazily via `use_skill` |
| Connector | `connectors/*.py` exposing `sync(out_dir, settings)` | at `cortex connectors run` |

A broken extension is reported and isolated; it never takes the brain down.
Registration is not authorization — a tool that touches something sensitive
keeps its own checks inside the callable.

Cortex is also an MCP *server*: `cortex mcp --brain ~/brains/home` exports
the same tool registry over stdio, so Claude Code, Cursor, or Hermes can
search your brain:

```sh
claude mcp add home-brain -- cortex mcp --brain ~/brains/home
```

## Web chat

```sh
cortex serve --brain ~/brains/home        # http://127.0.0.1:8642
```

With `server.auth: none` (the default) the server refuses to bind anything
but loopback. To expose it on a LAN, set `server.auth: key`, issue a key with
`cortex keys issue laptop`, and pass it as a Bearer token; only the SHA-256
of the key is stored.

## Observability

Every model call and tool call appends a JSONL row to `.cortex/usage.jsonl`
with `prompt_tokens` / `completion_tokens` when the endpoint reports them —
absent counts stay absent rather than becoming zeros, which is exactly what
[preflight](https://github.com/Unchained-Labs/preflight) expects for
calibration. Telemetry never makes an agent or tool call fail.

## Development

```sh
uv venv --python 3.12 && uv pip install -e '.[dev,mcp]'
.venv/bin/pytest
.venv/bin/ruff check src tests
```

Docs: [unchained-labs.github.io/cortex](https://unchained-labs.github.io/cortex/) ·
Brand: [Unchained-Labs/branding](https://github.com/Unchained-Labs/branding) ·
License: [MIT](LICENSE)
