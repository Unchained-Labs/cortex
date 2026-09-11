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
  <br><sub>Real product on film — Today, capture, the agent, channels and a rule filing a note
  are all shipped code; only the model is scripted (<code>docs/promo/</code> rebuilds it).
  <a href="https://unchained-labs.github.io/cortex/#film">The full film →</a></sub>
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
  read, with **/** from anywhere. Below the direct hits come the notes and
  files the brain's graph connects to them, each saying why (`↳ links to
  garden.md`).
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
- **Code** — repositories the brain can read, and scheduled reviews of them
  (below).
- **Settings** (admins) — three sections: **Extend** (skills, connectors,
  plugins, MCP servers, templates), **Automation** (rules and scheduled
  jobs, below), and **Admin** (accounts, index and model health, a way to
  re-index without a terminal).

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

### Subagents

Five sources to read, four files to review, three questions to research:
one agent doing them in sequence fills its context with the first before
it reaches the last. `delegate` hands each part to a subagent that runs at
the same time (up to four per call, three at once, a shorter loop than the
parent's) and reports back; the parent sees one answer per task and
decides what to keep. Children read and search with the parent's tools
and the parent's scope, and cannot write, remember or delegate — what
gets kept is decided in front of the person. The deep-research skill fans
its queries out this way. The shape is Hermes Agent's `delegate_task`.

### The graph

Search finds the passage that matches the words. It does not know that the
note it found links to three others, that the function it found is called
from two files that never say its name, or that the file it found changed
in the same commit as another six times this month. Those relations are how
a person moves from a hit to the thing they wanted, so the brain keeps
them as a graph beside the index:

- **notes** — `[[wikilinks]]`, markdown links, `#tags`
- **code** — imports resolved inside the repository, definitions, and which
  files *use* a symbol another file defines
- **history** — files that changed in the same commit, from `git log`
- **shape** — which directory holds what

It is the structural half of the GraphRAG / LightRAG / HippoRAG design,
extracted deterministically: no model call between saving a note and
searching it, milliseconds per file, and every edge is a reason a person
can check. It shows up in three places: search pulls in one hop of
neighbours below the direct hits with a `via` that says why; the agent has
`related` (what a file links to, imports, uses, changed with) and
`find_symbol` (where a name is defined and used); and the Vault shows a
**Connections** panel under every note and source file.

## Things it does without being asked

**Rules** file notes for you. A rule matches on path, tag, frontmatter,
content or age, then moves, tags or archives. Because this moves your
writing, the shape is constrained on purpose:

- there is **no delete action**, and there will not be one
- **preview is free and comes first** — you see which note goes where before
  anything moves
- **every change is logged**, so "where did my note go" always has an answer

**Jobs** are the clock: sync a connector, re-index, run the rules, write
today's digest into a note, post it into a channel, or review a repository. Intervals are hours
in plain words rather than cron, and each job says what it is: *"apply the
tidying rules daily"*. Both ship a set of ready-made suggestions, all
switched off until you read one and turn it on.

Two things deliberately absent. There is no "ask the model something and
notify me" job — every job is declared, and the one that runs the model,
the code review, has a declared input (this repo, these commits) and an
output a person approves line by line. And a channel digest with nothing in
it posts nothing, just as a review with no new commits writes nothing: a
scheduled "nothing to report" is what teaches people to ignore the channel
it arrives in.

## Code: repos the brain can read, and reviews it writes

Add a GitHub or GitLab repository in the **Code** tab — `owner/name` or its
URL, a branch if not the default — and cortex keeps a shallow clone under
`.cortex/repos/`, refreshes it on an interval, and indexes it under
`code/<name>/`. From then on the agent searches your notes and your code
together: `search_brain` finds both, `read_file` opens
`code/cortex/src/cortex/jobs.py`, and `list_repos`, `repo_tree`, `repo_log`
and `repo_diff` give it what git knows. Every repo you add is readable by
everyone on the brain; adding it is the decision to share it.

A private repository needs a token. A repo names the environment variable
that holds it (`GITHUB_TOKEN` or `GITLAB_TOKEN` by default), and the
variable comes from the shell or from a **`.env` beside `cortex.yaml`**,
which cortex loads on start — the same file serves `api_key_env` for a
model provider:

```sh
# ~/brain/.env
GITHUB_TOKEN=github_pat_…
GITLAB_TOKEN=glpat-…
OPENROUTER_API_KEY=sk-or-…
```

The token reaches git as a per-host header, never in `argv` and never in
the clone's `.git/config`. `cortex repos add owner/name` does the same from
a terminal.

**Scheduled reviews** are jobs of a new kind. Pick a repo, an interval, what
to look for ("error handling and anything that touches auth"), and
optionally a channel. On each run the agent syncs the repo, takes the diff
since the commit it last reviewed, pulls context from the brain — the
project's conventions, earlier reviews, the worklog — and writes
`reviews/<repo>-<date>.md` in the shared vault, with one line per finding:

```markdown
- [ ] **F1 · unchecked return** — high · `src/pay.py:41` — the result is dropped…
```

That is the approval loop cortex already had: a person ticks the findings
an agent may act on, `approved_findings` lists them, `record_work` closes
them. The reviewer cannot tick its own boxes — the note is written by code
from the model's answer, and ticks are stripped on the way in. A run with
no new commits writes nothing; the first run of a repo looks at the whole
codebase once. Reviews of a repo also work in Chat ("review the last three
commits of cortex"); the `code-review` skill in the library spells out the
procedure.

## Research: the agent on the web

Two tools, `web_search` and `fetch_url`, and three library skills that use
them: `web-lookup` for a fact with a source, `deep-research` for a question
that deserves several searches, cross-checked and written up under
`research/`, and `code-review` above. Search needs a backend, and the
self-hosted one comes first: set `CORTEX_SEARCH_URL` to a
[SearXNG](https://github.com/searxng/searxng) instance (with `format: json`
enabled), or `BRAVE_SEARCH_API_KEY` for the Brave Search API. With neither
set it falls back to DuckDuckGo's HTML endpoint, which needs no key and can
break without notice — when it does, the tool says so rather than answering
"nothing found".

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

**It writes its own skills.** When the agent has just worked out a
multi-step way of doing something, it saves the procedure with
`save_skill` so next time is one call, and when a skill's instructions let
it down it fixes them. The idea comes from Hermes Agent's learning loop;
the two lines cortex adds are that the brain signs what it writes
(`author: cortex`, shown as *written by the brain* on the Extend page, with
how often each skill is used) and never rewrites a skill a person wrote —
for those it says what should change and the person edits. In Chat,
**Turn this into a skill** asks it to write down what it just did.

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
├── .env               # tokens: GITHUB_TOKEN, an api_key_env… (gitignored)
└── .cortex/           # index, checkpoints, repo clones — disposable cache
```

Back it up by copying the folder. Home brain, company brain, club brain:
three folders, three `cortex serve` processes.

```sh
cortex note "the boiler service is due in March"   # capture, from anywhere
cortex today                                       # what is on
cortex clip https://example.com/recipe             # save a page as markdown
cortex repos add Unchained-Labs/cortex             # let the brain read a repo
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
.venv/bin/pytest                    # 329 tests
.venv/bin/ruff check src tests
cd web && npm install && npm run dev   # SPA dev server, proxies to :8642
```

The frontend contract lives in [docs/product-spec.md](docs/product-spec.md);
cutting a release is [RELEASING.md](RELEASING.md).

Docs: [unchained-labs.github.io/cortex](https://unchained-labs.github.io/cortex/) ·
Brand: [Unchained-Labs/branding](https://github.com/Unchained-Labs/branding) ·
License: [MIT](LICENSE)
