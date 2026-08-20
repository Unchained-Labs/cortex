import type { WsEvent } from "./types";

/** Singleton /ws connection with exponential-backoff reconnect. */

type Listener = (ev: WsEvent) => void;

let sock: WebSocket | null = null;
let wanted = false;
let attempt = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<Listener>();

function open(): void {
  if (sock || !wanted) return;
  const url = `${location.origin.replace(/^http/, "ws")}/ws`;
  const ws = new WebSocket(url);
  sock = ws;
  ws.onopen = () => {
    attempt = 0;
  };
  ws.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data as string) as WsEvent;
      listeners.forEach((l) => l(ev));
    } catch {
      /* ignore non-JSON */
    }
  };
  ws.onerror = () => ws.close();
  ws.onclose = () => {
    if (sock === ws) sock = null;
    if (wanted) {
      const delay = Math.min(15000, 1000 * 2 ** attempt);
      attempt += 1;
      clearTimeout(timer);
      timer = setTimeout(open, delay);
    }
  };
}

export function wsConnect(): void {
  wanted = true;
  open();
}

export function wsDisconnect(): void {
  wanted = false;
  clearTimeout(timer);
  attempt = 0;
  sock?.close();
  sock = null;
}

export function wsSubscribe(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
