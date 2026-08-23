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

### Today and capture

- `GET /api/digest` → `{day, tasks: [{path, text, line}], events: [{path, title, start,
  today}], changed: [{path, mtime}], captured_today, total_open_tasks}`.
  Computed without the model, scoped to the caller — it must answer instantly and on a
  brain with no model configured.
- `POST /api/capture` `{text, vault?}` → `{vault, path, line}`. Appends one timestamped
  line to today's daily note (`journal/YYYY-MM-DD.md`) in the caller's own vault by
  default. 422 on empty text, 404 for a vault the caller may not write.

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

### Extensions (role=admin only)

Saving a plugin or connector **runs the author's code as the server user** —
same trust level as a stdio MCP server. The UI must say this where code is edited.

- `GET /api/extensions` → `{plugins: [E], skills: [E], connectors: [E], mcp_servers: [E],
  load_errors: [str], mcp_errors: [str]}` where E is
  `{kind, name, enabled, tools: [str], description, error, source, detail}`.
  `source` is `"dashboard"` (editable), `"file"` (cortex.yaml, read-only here) or
  `"builtin"`. `error` non-empty means it failed to load — show it, don't hide it.
  For mcp, `detail` = `{transport, command, args, url, include, exclude, header_keys}` —
  header *values* are never sent to the client.
- `GET /api/extensions/scaffold?kind=plugin|connector|skill` → starter content
  (`{code}` or `{description, instructions}`) so "New" opens something that runs.
- `GET /api/extensions/source?kind=&name=` → `{kind, name, code}` for plugin/connector,
  `{kind, name, description, instructions}` for skill. 404 if missing.
- `PUT /api/extensions/{kind}` — body by kind:
  plugin/connector `{name, code, settings?}`, skill `{name, description, instructions}`,
  mcp `{spec: {name, transport, command?, args?, url?, headers?, include?, exclude?, enabled?}}`.
  → `{name, tools?}`. **422 with the loader's own message** when the code will not load;
  nothing is written in that case. On success the agent is rebuilt, so new tools are live
  on the next turn without a restart.
- `POST /api/extensions/{kind}/{name}/enabled` `{enabled}` → toggles without editing the file.
- `POST /api/extensions/connector/{name}/settings` `{settings}` → merge over cortex.yaml.
- `POST /api/extensions/connector/{name}/run` → `{name, result}` (`"ok"` or the error text),
  then a re-index is queued.
- `DELETE /api/extensions/{kind}/{name}` → `{ok}`. 422 for a cortex.yaml-defined MCP server.

### Identity

`identity.md` at the brain root: who the brain is for and how they like things done,
read into every system prompt. Any user may read it; **admins edit it**. The old
`persona:` string in cortex.yaml is still honoured — both appear when both exist, so
upgrading never drops a configured persona.

**The agent may propose changes and may not make them.** A system that quietly rewrites
its own instructions is one nobody can reason about, and the failure is silent — you
would never know which version answered you.

- `GET /api/identity` → `{text, starter, untouched, persona, max_chars, mtime,
  proposals: [P], decided: [P]}` where P is `{id, text, reason, created_at, status,
  decided_at, decided_by}`. `proposals` is what is waiting; `decided` is what was
  accepted or discarded and by whom — the same question the rules history answers for
  moved notes. `untouched` is true while the file is still the
  starter, which is also why the starter never reaches the prompt: a placeholder in
  every conversation is worse than nothing.
- `PUT /api/identity {text, base_mtime?}` (admin) → `{ok, mtime}`; 422 over `max_chars`,
  because identity is read on every turn and length costs on every turn. Passing
  `base_mtime` opts into the vault's concurrency rule — **409 `{detail: "conflict",
  server_mtime}`** rather than clobbering another admin's save. Omitting it is explicit
  last-writer-wins.
- `POST /api/identity/proposals/{id}/{accept|discard}` (admin) → `{ok, decision}`.
  Accepting writes the file and rebuilds the agent; discarding writes nothing. Each
  proposal is decided once (404 afterwards), and anything but accept/discard is a 422.

### Note templates

A template is a markdown file under `templates/` with optional frontmatter naming
where its notes land (`name:` and `target:`). Placeholders are deliberately few —
`{{title}} {{slug}} {{date}} {{time}} {{datetime}} {{user}}` — and an unknown one is
left visible rather than blanked, so a typo looks wrong instead of losing a line.
Reading and using templates is open to any user; editing the shared set is admin-only.

- `GET /api/templates` → `{templates: [{name, title, target, body, raw}], placeholders}`.
  **`body` is the frontmatter-stripped body for rendering; `raw` is the file as written.**
  An editor must round-trip `raw` — `PUT` writes the whole file, so saving `body` back
  would delete the frontmatter and silently relocate every future note from that
  template.
- `POST /api/templates/new-note {template, vault, title}` → `{vault, path}`.
  **422 if the note already exists** — a template starts something, it never
  overwrites. 404 for an unknown template or a vault the caller may not write.
- `PUT /api/templates {name, body}` (admin) → the saved template, 422 on a bad name.
- `DELETE /api/templates/{name}` (admin).
- `POST /api/templates/install` (admin) → `{written: [name, …]}`, the names actually
  written; the starter set never clobbers an edited file, so a second call returns `[]`.
- `PUT` slugifies the name it is given (`My Notes` → `my-notes`) and returns the name it
  used — show that back rather than assuming what was typed.

### Memory (any signed-in user)

What the brain remembers, typed. Kinds: `person`, `project`, `preference`, `goal`,
`fact` — untyped memories are facts. Not admin-only: a brain that quietly believes a
wrong thing about someone is worse than one that believes nothing, so anyone who can
see a memory can correct it.

- `GET /api/memory?kind=` → `{kinds, memories: [{id, kind, subject, body, source,
  created_at}]}`, grouped-friendly (already ordered by kind then subject).
- `POST /api/memory {body, kind?, subject?}` → `{id, kind}`; 422 on empty body or an
  unknown kind.
- `PUT /api/memory/{id} {body, kind, subject}` → `{ok}`; 404 if it is gone.
- `DELETE /api/memory/{id}` → `{ok}` (retires it; the row is kept for the audit trail
  but leaves every read path).

### Rules and scheduled jobs (role=admin only)

**Rules** tidy the vault: match notes, then move, tag or archive them. They move
someone's writing, so the design is constrained — there is no delete action, preview is
always available and free, and every applied change is logged.

- `GET /api/rules` → `{rules: [R], suggested: [R], match_kinds, action_kinds}` where R is
  `{name, vault, matches: [{kind, value}], action: {kind, value}, enabled, describes}`.
  `describes` is a plain-English sentence ("tag matches 'recipe' → move into recipes/") —
  render it, do not make the user reconstruct it from the parts.
  `match_kinds`: path (glob), tag, frontmatter (`key` or `key: value`), content,
  older_than_days. `action_kinds`: move, tag, archive. `suggested` are ready-made rules,
  all `enabled: false`.
- `PUT /api/rules {rule}` → the saved rule, or **422 with a plain reason**.
- `DELETE /api/rules/{name}`
- `GET /api/rules/preview` → `{planned: [{path, rule, action, target}], count}` — exactly
  what a run would do, having done none of it. **The panel must offer this before apply.**
- `POST /api/rules/apply` → `{actions, count}`; each action is `{rule, action, path, target}`,
  and `action: "error"` carries the reason in `target`.
- `GET /api/rules/history` → `{history: [{at, rule, action, path, target}]}` — "where did
  my note go" must always have an answer.

**Jobs** are the clock. Kinds: `connector` (settings `{connector}`), `index`, `rules`
(settings `{dry_run}`), `digest` (settings `{vault}`, writes `briefings/<day>.md`),
`channel_digest` (settings `{channel}`, posts into a channel and **posts nothing when the
digest is empty**). Intervals are hours, not cron.

- `GET /api/jobs` → `{jobs: [J], suggested: [J], kinds, connectors}`; J is
  `{name, kind, interval_hours, settings, enabled, last_run, last_status, last_detail,
  describes}`. `describes` again reads as a sentence ("apply the tidying rules daily").
- `PUT /api/jobs {job}` → saved job or 422.
- `DELETE /api/jobs/{name}`, `POST /api/jobs/{name}/run` → `{name, status, detail}`.

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
5. **Extend** (admins only) — four sections (Plugins, Skills, Connectors, MCP servers).
   Each row: name, enabled toggle, what it provides (tool names / description /
   transport), and its error in `--ul-down` when it failed to load. Editing opens a
   CodeMirror editor (python for plugin/connector, plain text for skill instructions)
   with a Save that surfaces the 422 loader message inline — a plugin that will not
   load must never look saved. Connectors additionally get a JSON settings box and a
   "Run now" button showing the result. MCP servers get a form (transport, command +
   args or url, header key/value rows, include/exclude); `source: "file"` rows render
   read-only with a "defined in cortex.yaml" note. A visible warning states that
   saving code executes it on the server.
6. **Admin** (admins only) — user management + `/api/info` stats.

### Identity in the dashboard (v0.4)

Put it where the brain's own settings live — an **Identity** section in Admin is the
natural home, since editing is admin-only. Show the current text in the editor, save via
PUT with the 422 rendered inline. **Pending proposals are the interesting part**: each
shows its reason, the proposed text (a diff against the current text if that is cheap,
otherwise the full text), and Accept / Discard. Make it obvious that nothing has changed
yet — a pending proposal is a question, not a fait accompli. Any signed-in user can read
the identity text; only admins see the editor and the decisions.

### Templates in the Vault (v0.4)

The Vault view gains **New from template** beside its existing new-file action: pick a
template, type a title, and the note is created where the template's `target` says and
opened. Show the target pattern so it is obvious where it will land. An admin also gets
a template editor (list, edit body, delete, and an "add the starter set" action) — put
it wherever it sits most naturally, but not behind a new top-level tab.

### Memory (v0.4, any user)

A **Memory** tab listing what the brain knows, grouped by kind, each row showing its
subject and body with inline edit and a forget button, plus an add form (body, kind
dropdown, subject). This is the correction surface — it must be easy to fix a wrong
memory, not just read one. Fold **Import** into the Vault view at the same time (it is
a once-a-year vault operation occupying a top-level slot), so the tab count does not
grow.

### Extend, redesigned (v0.4)

The current page is four flat lists of names under a warning about arbitrary code
execution — it tells you what you have, never what any of it is *for*. Rework it so
each section leads with one plain sentence about what that kind of extension does for
you, and so the common case is picking something ready-made rather than writing code:

- **Skills** and **Connectors** show a small **library** of ready-made ones with a
  one-line description and an Add button. Writing your own stays available, one click
  further in. An empty section must show the library, never an empty list.
- Every row says what it *gives you*: a plugin's tool names, a skill's description, a
  connector's last run and result, an MCP server's transport.
- Keep the code-execution warning, but put it where code is written (the editor), not
  as the first thing on the page.

### Automation (admins only, new tab)

Two panels on one page, because they are one idea — things the brain does without being
asked:

- **Rules**: a list of rules each rendered as its `describes` sentence with an enable
  toggle. A builder that composes conditions and an action from dropdowns (no raw JSON).
  **Preview is the primary button**, showing a table of "this note → that folder" before
  anything moves; Apply is secondary and confirms. A history list underneath.
- **Scheduled jobs**: each as its `describes` sentence ("apply the tidying rules daily"),
  with last run, last status (`--ul-up`/`--ul-down`), enable toggle, and Run now. Adding
  one is a kind dropdown plus an interval in human units (hourly / every 6 hours / daily /
  twice a day / weekly), not a cron string. Offer the `suggested` jobs as one-click adds.

### Today, capture and search (v0.3)

- **Today** is the default tab and the first thing a user sees. It renders
  `/api/digest`: events today and coming up, open tasks (each a checkbox that ticks
  the real file), what changed recently, and how much was captured today. Every item
  links into the Vault view. On an empty brain it must not be a blank pane — it says
  what to do first (capture, import, connect a calendar) with buttons that go there.
- **Capture** is global: a **c** keypress from any tab (except while typing) opens a
  one-line box; Enter files it via `POST /api/capture` and Escape cancels. It is the
  single most-used control in the product — it must never take more than one keypress
  to reach.
- **Search** is a tab of its own over `GET /api/search`: a query box, hits grouped by
  file with their matching passages, headings shown, and a note when results are
  full-text only. Clicking a hit opens it in the Vault view at that file. Reachable
  with **/** from anywhere.
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
cortxai, runs `cortex setup`. Dockerfile + docker-compose.yml build the SPA and
run `cortex serve --host 0.0.0.0` with the brain in a volume.
