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

export type WsEvent =
  | { type: "channel_message"; channel_id: string; message: ChannelMessage }
  | { type: "agent_partial"; channel_id: string; message_id: string; text: string }
  | { type: "vault_changed"; vault: string; path: string }
  | { type: "index_done"; stats: Record<string, number> };
