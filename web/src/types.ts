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
  | { type: "vault_changed"; vault: string; path: string };
