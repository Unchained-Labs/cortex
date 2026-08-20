# cortex product spec — v0.2 "the dashboard release"

Cortex is a product, not just a library: a self-hosted brain with a web
dashboard for a household or team. This file is the contract between the
backend (`src/cortex/server`) and the frontend (`web/`).

## Concepts

- **Brain**: a directory (`cortex.yaml` + data). One server process serves one brain.
- **User**: named account with a password (scrypt hash) and a role, `admin` or `member`.
  Created by the setup wizard (first admin) or by an admin in the dashboard.
- **Vault**: a folder of markdown. `vaults/shared/` is readable/writable by everyone;
  `vaults/<username>/` is private to its owner (and to the agent when that owner asks).
  Obsidian conventions apply: wikilinks `[[note]]`, embeds `![[img.png]]`, callouts
  `> [!note]`, YAML frontmatter, `- [ ]` tasks.
- **Channel**: a named peer-chat room (`#general` exists by default). Members post
  messages; mentioning `@cortex` makes the agent reply in the channel.
- **Agent thread**: a private conversation between one user and the agent.

## Auth

Cookie session: `POST /api/auth/login {username, password}` sets an HttpOnly cookie
`cortex_session` (HMAC-signed, 14-day expiry) and returns `{username, role}`.
`POST /api/auth/logout` clears it. `GET /api/me` → `{username, role}` or 401.
Every `/api/*` route except login requires the cookie. `/health` is public.

## REST endpoints

All responses JSON unless stated. Errors: `{"detail": str}` with 4xx/5xx.

### Agent chat
- `POST /api/chat` `{message, thread?}` → SSE stream (`text/event-stream`), frames
  `data: {json}\n\n` with `type` one of:
  - `thread {thread}` — first frame, the thread id in use
  - `token {text}` — model text delta
  - `tool_start {name, arguments}` / `tool_end {name, ok, latency_ms, preview}`
  - `notice {text}` / `error {text}` / `done {text}` (full final answer)
- `GET /api/threads` → `{threads: [{thread, title, updated_at}]}` (user's own)
- `GET /api/history?thread=` → `{thread, messages: [{role, body, at}]}`

### Search
- `GET /api/search?q=` → `{used_vectors, hits: [{path, score, passages: [{heading, text,
  start_line}]}]}` — scoped to the caller (shared + own vault + sources).

### Vaults
- `GET /api/vaults` → `{vaults: [{name, kind: "shared"|"personal", files}]}` (only ones
  the caller may see)
- `GET /api/vault/tree?vault=` → `{vault, files: [{path, size, mtime}]}` (sorted, md +
  attachments; no directories entries — the client derives folders from paths)
- `GET /api/vault/file?vault=&path=` → `{vault, path, text, mtime}` (404 if missing;
  binary attachments come from `GET /api/vault/raw?vault=&path=` as bytes)
- `PUT /api/vault/file` `{vault, path, text, base_mtime?}` → `{mtime}`. If the file
  changed since `base_mtime`, 409 `{detail: "conflict", server_mtime}` — the client
  re-loads and re-applies.
- `POST /api/vault/file` `{vault, path, text?}` → create (409 if exists)
- `DELETE /api/vault/file?vault=&path=` → `{ok: true}`
- `POST /api/vault/import` — either multipart upload `file=<zip>` + form fields
  `vault`, or JSON `{vault, git_url}` or `{vault, src_path}` (server-side path).
  → `{imported: n_files, skipped: n}`. Zip entries outside the vault root are skipped.
- Writes re-index incrementally in the background; search may lag a save by seconds.

### Channels (peer chat)
- `GET /api/channels` → `{channels: [{id, name, created_by}]}`
- `POST /api/channels` `{name}` → `{id, name}`
- `GET /api/channels/{id}/messages?before=&limit=50` → `{messages: [{id, author, body,
  at}]}` newest-last
- `POST /api/channels/{id}/messages` `{body}` → `{id, at}`. If body contains
  `@cortex`, the agent is invoked with recent channel context and posts its reply as
  author `cortex` (streamed over WS as it generates).

### Admin (role=admin only)
- `GET /api/admin/users` → `{users: [{username, role, created_at}]}`
- `POST /api/admin/users` `{username, password, role}` → 201
- `DELETE /api/admin/users/{username}` → `{ok}` (cannot delete yourself)
- `GET /api/info` → brain name, index stats, models, tools (all users may call)

## WebSocket

`GET /ws` (cookie-authenticated). Server pushes JSON events:
- `{type: "channel_message", channel_id, message: {id, author, body, at}}`
- `{type: "agent_partial", channel_id, message_id, text}` — the agent's channel reply
  growing token by token; terminated by a final `channel_message` with the same
  `message_id`.
- `{type: "vault_changed", vault, path}` — another session saved a file.
Client sends nothing except pings; all mutations go through REST.

## Frontend

`web/` — React 18 + Vite + TypeScript, no router library (tab state), no UI kit.
Styling: vendored `tokens.css` + `brand.css` from `src/cortex/server/web/`, custom CSS
through `--ul-*` variables only, dark default. Fonts self-hosted via @fontsource
(space-grotesk, inter, jetbrains-mono) — no CDN at runtime.

Views (tabs in the header, brand lockup at left):
1. **Chat** — agent conversation: thread list sidebar, SSE streaming, tool activity
   lines (⚙ running, ✓/✗ done), markdown-rendered answers with file-path citations
   clickable → opens Vault view at that file when it is a vault path.
2. **Channels** — channel list, message pane over WS, composer. `@cortex` replies
   render progressively from `agent_partial` events.
3. **Vault** — vault picker (shared + personal), file tree (folders derived from
   paths, collapsible), CodeMirror 6 markdown editor, and a rendered preview with
   Obsidian formatting: wikilinks (click → open target), embeds `![[...]]` (images
   inline via /api/vault/raw), callouts (> [!note]/[!warning]/[!tip] boxes), YAML
   frontmatter (key-value table), task checkboxes (toggling writes the file), tags
   `#tag` highlighted. Save = PUT with base_mtime; on 409 show a conflict banner.
   Edit/Preview toggle; autosave off, Ctrl-S saves.
4. **Import** — zip upload or git URL/server path form, per the import endpoint.
5. **Admin** (admins only) — user management + `/api/info` stats.
6. **Sign-in** — username/password against /api/auth/login.

Build: `npm run build` outputs to `../src/cortex/server/webdist/` (vite `outDir`,
`emptyOutDir`). The backend serves `webdist/index.html` at `/` and hashed assets at
`/app/*`; `/assets/*` keeps serving the brand css/svg files.

## Agent stack

LangGraph + LangChain: `create_react_agent` over per-provider chat models —
`ChatOpenAI(base_url=...)` for every OpenAI-compatible endpoint (Ollama, vLLM,
LM Studio, **OpenRouter**, **LiteLLM proxy**), `ChatAnthropic` for direct Anthropic.
`AsyncSqliteSaver` checkpoints threads in `.cortex/checkpoints.db`. MCP servers attach
through `langchain-mcp-adapters`. Local tools remain `ToolPlugin`s, adapted to
LangChain `StructuredTool`s. Usage lands in `.cortex/usage.jsonl` via a callback
(prompt_tokens/completion_tokens when reported; absent stays absent).

Per-request scope: a ContextVar carries the caller's readable path prefixes
(`vaults/shared`, `vaults/<user>`, `sources`, extra paths); retrieval tools filter
through it. `None` scope (CLI, MCP export — the box owner) means unrestricted; an
empty set means nothing. The two are never conflated.

## Setup wizard

`cortex setup [path]` — interactive: brain name → endpoint kind (ollama / vllm /
openrouter / litellm / anthropic / custom) → base URL (pinged via `<url>/models`,
warning on failure and on public endpoints) → chat + embed model names → admin
username/password → port. Writes cortex.yaml, creates the brain layout and the admin
user, prints the serve command. Non-interactive via flags for scripting.
`install.sh` = curl-able bootstrap: checks python3.11+, pipx/venv installs
cortex-brain, runs `cortex setup`. Dockerfile + docker-compose.yml build the SPA and
run `cortex serve --host 0.0.0.0` with the brain in a volume.
