/** Shared API types — mirrors docs/product-spec.md. */

export interface Me {
  username: string;
  role: "admin" | "member";
}

export interface ThreadMeta {
  thread: string;
  title: string;
  updated_at: string;
}

export interface HistoryMessage {
  role: string;
  body: string;
  at: string;
}

export type ChatFrame =
  | { type: "thread"; thread: string }
  | { type: "token"; text: string }
  | { type: "tool_start"; name: string; arguments: unknown }
  | { type: "tool_end"; name: string; ok: boolean; latency_ms: number; preview: string }
  | { type: "notice"; text: string }
  | { type: "error"; text: string }
  | { type: "done"; text: string };

export interface VaultMeta {
  name: string;
  kind: "shared" | "personal";
  files: number;
}

export interface VaultFile {
  path: string;
  size: number;
  mtime: number;
}

/** Today — `/api/digest`. Paths are index keys (`vaults/<name>/<path>`). */
export interface DigestTask {
  path: string;
  text: string;
  line: number;
}

export interface DigestEvent {
  path: string;
  title: string;
  start: string;
  today: boolean;
}

export interface DigestChanged {
  path: string;
  mtime: number;
}

export interface Digest {
  day: string;
  tasks: DigestTask[];
  events: DigestEvent[];
  changed: DigestChanged[];
  captured_today: number;
}

/**
 * `POST /api/capture` — where the line landed. `line` is the 1-based line
 * number it was written at and `text` the line itself, so the confirmation
 * can say `path:line` rather than just naming the file.
 */
export interface CaptureResult {
  vault: string;
  path: string;
  line: number;
  text: string;
}

/**
 * `GET /api/file?path=<index key>` — reads anything the caller may see,
 * including connector output under `sources/…`. `editable` is false for
 * everything outside `vaults/`, and the Vault view honours it.
 */
export interface IndexedFile {
  path: string;
  text: string;
  mtime: number;
  editable: boolean;
}

/** `GET /api/info`. The health fields are what the UI acts on. */
export interface BrainInfo {
  brain: string;
  stats: Record<string, number>;
  chat_model: string;
  embed_model: string;
  chat_endpoint: string;
  tools: string[];
  indexed: boolean;
  indexing: boolean;
  model_error: string;
}

export interface SearchPassage {
  heading: string;
  text: string;
  start_line: number;
}

export interface SearchHit {
  path: string;
  score: number;
  passages: SearchPassage[];
}

export interface SearchResult {
  used_vectors: boolean;
  hits: SearchHit[];
}

export interface Channel {
  id: string;
  name: string;
  created_by: string;
}

export interface ChannelMessage {
  id: string;
  author: string;
  body: string;
  at: string;
  /**
   * Set on the agent's final message: the id of the streaming partial this
   * message supersedes. The final id never equals the partial id, so this is
   * the only way to retire the placeholder.
   */
  replaces?: string;
}

/**
 * `GET /api/memory` — one thing the brain believes. `source` says who or what
 * recorded it (`chat:erwin`, `dashboard:sam`), which is the difference between
 * "I told it that" and "it decided that".
 */
export interface Memory {
  id: number;
  kind: string;
  subject: string;
  body: string;
  source: string;
  created_at: string;
}

/** Already ordered by kind then subject, so the view groups without sorting. */
export interface MemoryList {
  kinds: string[];
  memories: Memory[];
}

/**
 * `GET /api/templates` — a shape to start a note from. `title` is the
 * human name (frontmatter `name:`), `target` the path pattern its notes
 * land at, and `body` the markdown *after* the frontmatter — the server
 * strips it on read, so an editor recomposes it from `title`/`target`.
 */
export interface NoteTemplate {
  name: string;
  title: string;
  target: string;
  body: string;
}

export interface TemplateList {
  templates: NoteTemplate[];
  /** placeholder names, without the braces: date, slug, title, … */
  placeholders: string[];
}

/** `POST /api/templates/new-note` — where the note landed. */
export interface NewNoteResult {
  vault: string;
  path: string;
}

/**
 * One proposed rewrite of the identity, waiting on a human. The agent may
 * propose and may not write — `reason` is why it asked, `text` is the exact
 * file it would write, and nothing has changed until someone accepts.
 */
export interface IdentityProposal {
  id: number;
  text: string;
  reason: string;
  created_at: string;
  status: string;
}

/**
 * `GET /api/identity` — who the brain is for, as a file rather than a config
 * string. `untouched` is true while the file is still `starter`, which is also
 * why the starter never reaches the prompt. `persona` is the old cortex.yaml
 * string, still honoured and prepended to the file when both exist.
 */
export interface IdentityState {
  text: string;
  starter: string;
  untouched: boolean;
  persona: string;
  max_chars: number;
  proposals: IdentityProposal[];
}

export interface AdminUser {
  username: string;
  role: string;
  created_at: string;
}

export type ExtensionKind = "plugin" | "skill" | "connector" | "mcp";

/** `detail` is kind-shaped: connectors carry `settings`, mcp the rest. */
export interface ExtensionDetail {
  settings?: Record<string, unknown>;
  transport?: string;
  command?: string;
  args?: string[];
  url?: string;
  include?: string[];
  exclude?: string[];
  /** header names only — the server never sends their values */
  header_keys?: string[];
}

export interface Extension {
  kind: ExtensionKind;
  name: string;
  enabled: boolean;
  tools: string[];
  description: string;
  error: string;
  source: "dashboard" | "file" | "builtin";
  detail: ExtensionDetail;
}

export interface ExtensionList {
  plugins: Extension[];
  skills: Extension[];
  connectors: Extension[];
  mcp_servers: Extension[];
  load_errors: string[];
  mcp_errors: string[];
}

/**
 * `GET /api/extensions/library` — ready-made skills, so an empty Skills
 * section offers something to add rather than an empty list.
 */
export interface LibrarySkill {
  name: string;
  description: string;
  instructions: string;
  installed: boolean;
}

export interface LibraryConnector {
  name: string;
  description: string;
  /** "builtin" already ships; "template" writes starter code into the brain. */
  kind: string;
  settings: Record<string, unknown>;
  installed: boolean;
}

export interface SkillLibrary {
  skills: LibrarySkill[];
  connectors: LibraryConnector[];
}

/** One condition of a rule: `tag` matches `recipe`. */
export interface RuleMatch {
  kind: string;
  value: string;
}

/** What a rule does once every condition holds. Never a delete. */
export interface RuleAction {
  kind: string;
  value: string;
}

/**
 * `GET /api/rules`. `describes` is the sentence the panel renders — the
 * parts are only there for the builder. A rule whose stored spec no longer
 * parses comes back as a name and an `error`, and nothing else.
 */
export interface Rule {
  name: string;
  vault?: string;
  matches?: RuleMatch[];
  action?: RuleAction;
  enabled?: boolean;
  describes?: string;
  error?: string;
}

export interface RuleList {
  rules: Rule[];
  /** ready-made rules, all `enabled: false` */
  suggested: Rule[];
  match_kinds: string[];
  action_kinds: string[];
}

/** One line of `GET /api/rules/preview` — what a run would do, undone. */
export interface PlannedAction {
  path: string;
  rule: string;
  action: string;
  target: string;
}

export interface RulePreview {
  planned: PlannedAction[];
  count: number;
}

/** `POST /api/rules/apply`. `action: "error"` carries its reason in `target`. */
export interface AppliedAction {
  rule: string;
  action: string;
  path: string;
  target: string;
}

export interface RuleApply {
  actions: AppliedAction[];
  count: number;
}

/** `GET /api/rules/history` — where a note went, and when. */
export interface RuleRun {
  at: string;
  rule: string;
  action: string;
  path: string;
  target: string;
}

/**
 * What a job *is*: a kind, an interval in hours (never a cron string) and
 * whatever settings that kind needs. The suggested jobs arrive as this and
 * nothing more — they have never run.
 */
export interface JobSpec {
  name: string;
  kind: string;
  interval_hours: number;
  settings: Record<string, unknown>;
  enabled: boolean;
  describes?: string;
}

/** A saved job, which also carries how its last run went. */
export interface Job extends JobSpec {
  last_run: string;
  last_status: string;
  last_detail: string;
}

export interface JobList {
  jobs: Job[];
  /** ready-made jobs, all `enabled: false` */
  suggested: JobSpec[];
  kinds: string[];
  connectors: string[];
}

export interface JobRun {
  name: string;
  status: string;
  detail: string;
}

export type WsEvent =
  | { type: "channel_message"; channel_id: string; message: ChannelMessage }
  | { type: "agent_partial"; channel_id: string; message_id: string; text: string }
  | { type: "vault_changed"; vault: string; path: string }
  | { type: "index_done"; stats: Record<string, number> };
