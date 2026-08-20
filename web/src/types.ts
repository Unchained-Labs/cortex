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

export type WsEvent =
  | { type: "channel_message"; channel_id: string; message: ChannelMessage }
  | { type: "agent_partial"; channel_id: string; message_id: string; text: string }
  | { type: "vault_changed"; vault: string; path: string };
