<div align="center">
  <img src="docs/assets/lockup-horizontal.svg" width="300" alt="Unchained Labs">
</div>

# cortex

A self-hosted brain for a household or a team: a dashboard where people chat
with each other and with an agent that has read their shared notes — on your
own model, on your own machine.

<div align="center">
  <a href="https://unchained-labs.github.io/cortex/#film">
    <img src="docs/assets/cortex-demo.gif" width="720" alt="cortex demo — ask the agent, watch the tool call stream, get a cited answer">
  </a>
  <br><sub>Real product on film — dashboard, agent, and streaming are the shipped code; only the
  model is scripted (<code>docs/promo/</code> rebuilds it). <a href="https://unchained-labs.github.io/cortex/#film">The full one-minute film →</a></sub>
</div>

**Status: alpha.** The API and config surface are settling; expect breaking
changes between minor versions. The index and checkpoint formats are
disposable caches — deleting `.cortex/` loses conversations, never notes.

```sh
pip install cortxai
cortex setup                 # wizard: brain dir, model endpoint, admin account
cortex serve --host 0.0.0.0  # dashboard on :8642
```

Or `bash install.sh` (pipx/uv/venv autodetect), or `docker compose up` after
the one-time `cortex setup /brain` documented in docker-compose.yml.

**What it does not do:** cortex hosts no model — you bring an endpoint:
Ollama, vLLM, LM Studio, a LiteLLM proxy, OpenRouter, or the Anthropic API.
Vector search is exact cosine in-process, right for personal- and team-sized
brains, wrong for millions of chunks. Vault edits are last-writer-wins with
conflict *detection* (a 409 and a banner), not git-grade merging. The
calendar connector expands no recurrence rules yet.

## The dashboard

- **Today** — the default view and the reason to open it: what is on today,
  a few open tasks you can tick straight from the list, what changed, and
  anything you wrote on this date in earlier years. Computed without the
  model, so it answers instantly and works on a brain with no model
  configured at all. It is deliberately **bounded** — a handful of tasks and
  then "that is everything for today", never a growing pile of everything
  you have not done.
- **Capture** — press **c** anywhere. One line, Enter, and it lands in
  today's daily note. Also `cortex note "..."` from a terminal, and the
  agent can do it for you. Filing is optional; search does not care which
  note a line is in.
- **Search** — hybrid full-text and vector search over everything you can
  read, with **/** from anywhere.
- **Chat** — private threads with the agent. It searches before it answers,
  streams its tool calls (⚙ `search_brain` … ✓ 33ms), and cites files by
  path; clicking a citation opens it in the vault view.
- **Channels** — peer chat for the people on the brain. Mention `@cortex` and
  the agent answers in-channel, reading only the shared vault — never
  anyone's personal vault.
- **Vault** — shared and personal vaults, edited in the browser with
  Obsidian-flavored rendering: `[[wikilinks]]`, `![[embeds]]`, `> [!note]`
  callouts, frontmatter, task checkboxes that write through, `#tags`.
  Ctrl-S saves; a concurrent edit gets a conflict banner, not a silent
  clobber.
- **Import** — bring an existing Obsidian vault as a zip upload, a git URL,
  or a server path. `.obsidian/`, `.git/` and non-vault file types are
  skipped.
- **Automation** — rules and scheduled jobs (below).
- **Admin** — accounts (`admin` / `member`), index and model health, and a
  way to re-index without a terminal.

The agent can write, narrowly: it can add a line to today's note, tick a
task by exact path and line, and save a web page as markdown. There is no
general "write any file" tool — on a vault with no version control, the
narrowness is the safety property.

Accounts are username + password (scrypt), sessions are HttpOnly cookies.
Each user sees the shared vault, their own vault, and connector sources —
search, grep, and the agent are scoped per request, filtered inside the
query rather than trimmed after it.

## The agent stack

LangGraph's ReAct agent over LangChain chat models, with conversation state
in an `AsyncSqliteSaver` checkpoint per thread:

```yaml
providers:
  local:
    kind: openai                    # Ollama, vLLM, LM Studio — one wire
    base_url: "http://localhost:11434/v1"
    chat_model: qwen3
    embed_model: nomic-embed-text
  router:
    kind: openrouter                # cloud aggregator, OpenAI wire
    api_key_env: OPENROUTER_API_KEY
    chat_model: anthropic/claude-sonnet-5
  claude:
    kind: anthropic                 # direct Anthropic Messages API
    api_key_env: ANTHROPIC_API_KEY
    chat_model: claude-sonnet-5
roles:
  chat: router
  embed: local
```

A LiteLLM proxy is `kind: litellm` with its `base_url` — its routing and
fallback policy stays in the proxy, so cortex carries no LiteLLM SDK.
Endpoints are classified by network facts: private, loopback, CGNAT and
Tailscale addresses are trusted; anything public gets a plain warning that
your notes will leave the network.

Retrieval is hybrid: SQLite FTS5 and vector cosine ranked separately, fused
with reciprocal rank fusion, nudged by recency — the design from
[Cerebras' knowledge base](https://www.cerebras.ai/blog/how-we-built-our-knowledge-base).
The index rebuilds from scratch when the chunk schema *or* embedding model
changes, because silently mixing vector spaces is corruption. No embedding
endpoint means full-text search that says so, not fake vector scores.

## Things it does without being asked

**Rules** file notes for you. A rule matches on path, tag, frontmatter,
content or age, then moves, tags or archives. Because this moves your
writing, the shape is constrained on purpose:

- there is **no delete action**, and there will not be one
- **preview is free and comes first** — you see which note goes where before
  anything moves
- **every change is logged**, so "where did my note go" always has an answer

**Jobs** are the clock: sync a connector, re-index, run the rules, write
today's digest into a note, or post it into a channel. Intervals are hours
in plain words rather than cron, and each job says what it is: *"apply the
tidying rules daily"*. Both ship a set of ready-made suggestions, all
switched off until you read one and turn it on.

Two things deliberately absent. There is no "ask the model something and
notify me" job — every job is declared and deterministic, producing a fact
rather than an opinion. And a channel digest with nothing in it posts
nothing: a scheduled "nothing to report" is what teaches people to ignore
the channel it arrives in.

## Four ways to extend it

| Extension | Contract | Runs |
| :--- | :--- | :--- |
| Tool plugin | `plugins/*.py` exposing `register(registry)`, or a package with a `cortex.tools` entry point | agent time |
| MCP server | `mcp_servers:` block (stdio or streamable HTTP), attached via langchain-mcp-adapters | agent time |
| Skill | `skills/<name>/SKILL.md` (agentskills.io), loaded lazily via `use_skill` | on demand |
| Connector | `connectors/*.py` exposing `sync(out_dir, settings)` — distill, don't dump | `cortex connectors run` |

A broken extension is reported and isolated, never fatal. Registration is
not authorization: a tool that touches something sensitive keeps its own
checks inside the callable.

**Manage them from the dashboard.** The admin-only **Extend** panel lists
every plugin, skill, connector and MCP server with what it provides, its
load error if it has one, and an enable toggle that never edits your source
file. Skills and connectors also carry a **library** of ready-made ones you
add in a click — six skills and an RSS connector ship — because most people
want the one that already does the thing, not a blank editor. You can write a plugin or connector in the browser: it is loaded
before it is saved, so code that will not import is refused with the
loader's own message instead of silently breaking the next turn, and a
successful save rebuilds the agent so the new tool is live without a
restart. Connectors get a settings box and a "Run now" button; MCP servers
get a form. Servers defined in `cortex.yaml` show up read-only — the file
stays the owner of what it declares.

Saving a plugin or connector runs that code on the server as the cortex
user. That is the same trust level as configuring a stdio MCP server, and it
is why the panel is admin-only. From the terminal, `cortex ext list`,
`cortex ext disable plugin <name>`, and `cortex ext delete` do the same
management without the browser.

Cortex is also an MCP *server* —
`claude mcp add home-brain -- cortex mcp --brain ~/brain` gives Claude Code,
Cursor, or Hermes the same tool registry, at box-owner scope.

## Layout of a brain

```
~/brain/
├── cortex.yaml        # providers, roles, mcp servers, connectors
├── vaults/shared/     # everyone's notes
├── vaults/<user>/     # each user's private vault
├── sources/           # connector output
├── skills/ plugins/ connectors/
└── .cortex/           # index, checkpoints, usage.jsonl — disposable cache
```

Back it up by copying the folder. Home brain, company brain, club brain:
three folders, three `cortex serve` processes.

```sh
cortex note "the boiler service is due in March"   # capture, from anywhere
cortex today                                       # what is on
cortex clip https://example.com/recipe             # save a page as markdown
cortex demo                                        # example notes for an empty brain
cortex service install                             # keep it running across reboots
```

An empty brain cannot help you, so `cortex setup` offers to import an
existing vault, indexes what it finds, and `cortex demo` seeds a few
obviously-fake example notes you can delete in one command.

## Observability

Every model and tool call appends JSONL to `.cortex/usage.jsonl` with
`prompt_tokens`/`completion_tokens` when the endpoint reports them — absent
counts stay absent rather than becoming zeros, which is what
[preflight](https://github.com/Unchained-Labs/preflight) expects for
calibration. Telemetry never makes a call fail.

## Development

```sh
uv venv --python 3.12 && uv pip install -e '.[dev]'
.venv/bin/pytest                    # 102 tests
.venv/bin/ruff check src tests
cd web && npm install && npm run dev   # SPA dev server, proxies to :8642
```

The frontend contract lives in [docs/product-spec.md](docs/product-spec.md);
cutting a release is [RELEASING.md](RELEASING.md).

Docs: [unchained-labs.github.io/cortex](https://unchained-labs.github.io/cortex/) ·
Brand: [Unchained-Labs/branding](https://github.com/Unchained-Labs/branding) ·
License: [MIT](LICENSE)
