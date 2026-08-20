import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiSend } from "../api";
import { wsSubscribe } from "../ws";
import type { Channel, ChannelMessage } from "../types";
import Markdown from "../components/Markdown";

interface Props {
  username: string;
  active: boolean;
}

export default function Channels({ username, active }: Props) {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChannelMessage[]>([]);
  const [hasOlder, setHasOlder] = useState(false);
  /** agent replies still streaming: message_id → text (current channel only) */
  const [partials, setPartials] = useState<Record<string, string>>({});
  const [unread, setUnread] = useState<Record<string, boolean>>({});
  const [input, setInput] = useState("");
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const currentRef = useRef<string | null>(null);
  currentRef.current = current;

  const loadChannels = useCallback(async () => {
    try {
      const r = await apiGet<{ channels: Channel[] }>("/api/channels");
      setChannels(r.channels);
      return r.channels;
    } catch {
      return [];
    }
  }, []);

  const openChannel = useCallback(async (id: string) => {
    setCurrent(id);
    setPartials({});
    setError(null);
    setUnread((u) => ({ ...u, [id]: false }));
    try {
      const r = await apiGet<{ messages: ChannelMessage[] }>(
        `/api/channels/${encodeURIComponent(id)}/messages?limit=50`,
      );
      setMessages(r.messages);
      setHasOlder(r.messages.length >= 50);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load channel");
    }
  }, []);

  useEffect(() => {
    void loadChannels().then((chs) => {
      const general = chs.find((c) => c.name === "general") ?? chs[0];
      if (general && currentRef.current === null) void openChannel(general.id);
    });
  }, [loadChannels, openChannel]);

  useEffect(() => {
    if (active) void loadChannels();
  }, [active, loadChannels]);

  useEffect(
    () =>
      wsSubscribe((ev) => {
        if (ev.type === "channel_message") {
          if (ev.channel_id === currentRef.current) {
            setPartials((p) => {
              if (!(ev.message.id in p)) return p;
              const next = { ...p };
              delete next[ev.message.id];
              return next;
            });
            setMessages((m) =>
              m.some((x) => x.id === ev.message.id) ? m : [...m, ev.message],
            );
          } else {
            setUnread((u) => ({ ...u, [ev.channel_id]: true }));
          }
        } else if (ev.type === "agent_partial") {
          if (ev.channel_id === currentRef.current) {
            setPartials((p) => ({ ...p, [ev.message_id]: ev.text }));
          }
        }
      }),
    [],
  );

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, partials]);

  const loadOlder = async () => {
    if (!current || messages.length === 0) return;
    try {
      const r = await apiGet<{ messages: ChannelMessage[] }>(
        `/api/channels/${encodeURIComponent(current)}/messages?before=${encodeURIComponent(
          messages[0].id,
        )}&limit=50`,
      );
      setMessages((m) => [...r.messages, ...m.filter((x) => !r.messages.some((y) => y.id === x.id))]);
      setHasOlder(r.messages.length >= 50);
    } catch {
      setHasOlder(false);
    }
  };

  const send = async () => {
    const body = input.trim();
    if (!body || !current) return;
    setInput("");
    try {
      const r = await apiSend<{ id: string; at: string }>(
        "POST",
        `/api/channels/${encodeURIComponent(current)}/messages`,
        { body },
      );
      // optimistic append; the WS echo is deduped by id
      setMessages((m) =>
        m.some((x) => x.id === r.id) ? m : [...m, { id: r.id, author: username, body, at: r.at }],
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "send failed");
      setInput(body);
    }
  };

  const createChannel = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = newName.trim().replace(/^#/, "");
    if (!name) return;
    try {
      const r = await apiSend<{ id: string; name: string }>("POST", "/api/channels", { name });
      setNewName("");
      setCreating(false);
      await loadChannels();
      void openChannel(r.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "create failed");
    }
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  const partialEntries = Object.entries(partials);

  return (
    <div className="channels split">
      <aside className="side">
        <div className="side-head">
          <span className="label">Channels</span>
          <button className="btn btn-sm" onClick={() => setCreating((c) => !c)}>
            + New
          </button>
        </div>
        {creating && (
          <form className="side-form" onSubmit={createChannel}>
            <input
              autoFocus
              placeholder="channel-name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
          </form>
        )}
        <div className="side-list">
          {channels.map((c) => (
            <button
              key={c.id}
              className={c.id === current ? "side-item active" : "side-item"}
              onClick={() => void openChannel(c.id)}
            >
              <span className="mono">#</span>
              {c.name}
              {unread[c.id] && <span className="unread-dot" title="new messages" />}
            </button>
          ))}
        </div>
      </aside>

      <section className="pane">
        <div className="msg-scroll" ref={scroller}>
          {hasOlder && (
            <button className="btn btn-sm load-older" onClick={() => void loadOlder()}>
              Load older
            </button>
          )}
          {messages.map((m) => (
            <div
              key={m.id}
              className={`msg ${m.author === "cortex" ? "msg-assistant" : "msg-peer"} ${
                m.author === username ? "msg-self" : ""
              }`}
            >
              <span className="msg-author label">
                {m.author}
                <span className="msg-at faint"> {fmtTime(m.at)}</span>
              </span>
              {m.author === "cortex" ? (
                <Markdown text={m.body} />
              ) : (
                <p className="msg-plain">{m.body}</p>
              )}
            </div>
          ))}
          {partialEntries.map(([id, text]) => (
            <div key={id} className="msg msg-assistant msg-partial">
              <span className="msg-author label">
                cortex <span className="streaming-dot" title="streaming" />
              </span>
              <Markdown text={text} />
            </div>
          ))}
          {error && <p className="form-error">✗ {error}</p>}
        </div>
        <div className="composer">
          <textarea
            rows={2}
            placeholder={
              current
                ? "Message the channel — mention @cortex for the agent"
                : "Select a channel"
            }
            disabled={!current}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
          />
          <button
            className="btn primary"
            onClick={() => void send()}
            disabled={!input.trim() || !current}
          >
            Send
          </button>
        </div>
      </section>
    </div>
  );
}

function fmtTime(at: string): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return at;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
