import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet } from "../api";
import { chatStream } from "../sse";
import type { ThreadMeta, HistoryMessage } from "../types";
import Markdown from "../components/Markdown";

interface ToolLine {
  kind: "tool";
  name: string;
  status: "running" | "ok" | "fail";
  latency_ms?: number;
  preview?: string;
}

interface NoteLine {
  kind: "notice" | "error";
  text: string;
}

type ActivityLine = ToolLine | NoteLine;

interface ChatMessage {
  role: "user" | "assistant";
  body: string;
  activity?: ActivityLine[];
}

interface StreamState {
  text: string;
  activity: ActivityLine[];
}

function Activity({ lines }: { lines: ActivityLine[] }) {
  if (lines.length === 0) return null;
  return (
    <div className="tool-lines mono">
      {lines.map((l, i) =>
        l.kind === "tool" ? (
          <div key={i} className={`tool-line tool-${l.status}`}>
            <span className="tool-glyph">
              {l.status === "running" ? "⚙" : l.status === "ok" ? "✓" : "✗"}
            </span>
            <span className="tool-name">{l.name}</span>
            {l.latency_ms !== undefined && <span className="tool-lat">{l.latency_ms}ms</span>}
            {l.preview && <span className="tool-preview">{l.preview}</span>}
          </div>
        ) : (
          <div key={i} className={`tool-line tool-${l.kind === "error" ? "fail" : "notice"}`}>
            <span className="tool-glyph">{l.kind === "error" ? "✗" : "·"}</span>
            <span className="tool-preview">{l.text}</span>
          </div>
        ),
      )}
    </div>
  );
}

export default function Chat({ onVaultPath }: { onVaultPath: (path: string) => void }) {
  const [threads, setThreads] = useState<ThreadMeta[]>([]);
  const [thread, setThread] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [stream, setStream] = useState<StreamState | null>(null);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const threadRef = useRef<string | null>(null);
  threadRef.current = thread;

  const loadThreads = useCallback(() => {
    apiGet<{ threads: ThreadMeta[] }>("/api/threads")
      .then((r) => setThreads(r.threads))
      .catch(() => {});
  }, []);

  useEffect(loadThreads, [loadThreads]);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, stream]);

  const openThread = async (id: string) => {
    if (stream) return; // don't switch mid-stream
    setThread(id);
    setError(null);
    try {
      const r = await apiGet<{ thread: string; messages: HistoryMessage[] }>(
        `/api/history?thread=${encodeURIComponent(id)}`,
      );
      setMessages(
        r.messages.map((m) => ({
          role: m.role === "user" ? "user" : "assistant",
          body: m.body,
        })),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load thread");
    }
  };

  const newThread = () => {
    if (stream) return;
    setThread(null);
    setMessages([]);
    setError(null);
  };

  const send = async () => {
    const message = input.trim();
    if (!message || stream) return;
    setInput("");
    setError(null);
    setMessages((m) => [...m, { role: "user", body: message }]);
    let acc: StreamState = { text: "", activity: [] };
    setStream(acc);
    const update = (next: StreamState) => {
      acc = next;
      setStream(next);
    };
    let finalText: string | null = null;
    try {
      await chatStream({ message, thread: threadRef.current ?? undefined }, (frame) => {
        switch (frame.type) {
          case "thread":
            setThread(frame.thread);
            break;
          case "token":
            update({ ...acc, text: acc.text + frame.text });
            break;
          case "tool_start":
            update({
              ...acc,
              activity: [...acc.activity, { kind: "tool", name: frame.name, status: "running" }],
            });
            break;
          case "tool_end": {
            const activity = [...acc.activity];
            // close the most recent still-running call with this name
            for (let i = activity.length - 1; i >= 0; i--) {
              const a = activity[i];
              if (a.kind === "tool" && a.name === frame.name && a.status === "running") {
                activity[i] = {
                  ...a,
                  status: frame.ok ? "ok" : "fail",
                  latency_ms: frame.latency_ms,
                  preview: frame.preview,
                };
                break;
              }
            }
            update({ ...acc, activity });
            break;
          }
          case "notice":
            update({ ...acc, activity: [...acc.activity, { kind: "notice", text: frame.text }] });
            break;
          case "error":
            update({ ...acc, activity: [...acc.activity, { kind: "error", text: frame.text }] });
            break;
          case "done":
            finalText = frame.text;
            break;
        }
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "stream failed");
      // Nothing came back, so nothing was said: hand the typed message back
      // rather than making someone retype it. (Channels already does this.)
      if (!finalText && !acc.text && acc.activity.length === 0) {
        setMessages((m) => {
          const last = m[m.length - 1];
          return last && last.role === "user" && last.body === message ? m.slice(0, -1) : m;
        });
        setInput((cur) => (cur.trim() ? cur : message));
      }
    }
    const body = finalText ?? acc.text;
    if (body || acc.activity.length > 0) {
      setMessages((m) => [
        ...m,
        { role: "assistant", body, activity: acc.activity.length ? acc.activity : undefined },
      ]);
    }
    setStream(null);
    loadThreads();
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <div className="chat split">
      <aside className="side">
        <div className="side-head">
          <span className="label">Threads</span>
          <button className="btn btn-sm" onClick={newThread} disabled={!!stream}>
            + New
          </button>
        </div>
        <div className="side-list">
          {threads.map((t) => (
            <button
              key={t.thread}
              className={t.thread === thread ? "side-item active" : "side-item"}
              onClick={() => void openThread(t.thread)}
              title={t.updated_at}
            >
              {t.title || t.thread}
            </button>
          ))}
          {threads.length === 0 && <p className="side-empty muted">No threads yet.</p>}
        </div>
      </aside>

      <section className="pane">
        <div className="msg-scroll" ref={scroller}>
          {messages.length === 0 && !stream && (
            <div className="pane-empty">
              <p className="label">Agent</p>
              <p className="muted">
                Ask the agent anything about your vaults. Cited file paths open in the Vault
                view.
              </p>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`msg msg-${m.role}`}>
              <span className="msg-author label">{m.role === "user" ? "you" : "cortex"}</span>
              {m.activity && <Activity lines={m.activity} />}
              {m.role === "assistant" ? (
                <Markdown text={m.body} onVaultPath={onVaultPath} />
              ) : (
                <p className="msg-plain">{m.body}</p>
              )}
            </div>
          ))}
          {/* the answer arrives token by token; a screen reader is told about
              it politely rather than left with a silent, growing block */}
          {stream && (
            <div className="msg msg-assistant" aria-live="polite" aria-busy="true">
              <span className="msg-author label">cortex</span>
              <Activity lines={stream.activity} />
              {stream.text ? (
                <Markdown text={stream.text} onVaultPath={onVaultPath} />
              ) : (
                <p className="muted mono thinking">…</p>
              )}
            </div>
          )}
          {error && <p className="form-error">✗ {error}</p>}
        </div>
        <div className="composer">
          <textarea
            rows={2}
            aria-label="Message the agent"
            placeholder="Message the agent — Enter to send, Shift+Enter for newline"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
          />
          <button className="btn primary" onClick={() => void send()} disabled={!input.trim() || !!stream}>
            Send
          </button>
        </div>
      </section>
    </div>
  );
}
