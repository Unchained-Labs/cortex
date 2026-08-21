import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiSend } from "../api";
import { wsSubscribe } from "../ws";
import type { BrainInfo } from "../types";

/**
 * The two failures that make cortex look broken rather than empty:
 *
 *  - nothing is indexed, so every answer is "I don't know";
 *  - the chat endpoint is unreachable or misconfigured, which until now
 *    surfaced only as the words "Connection error." inside a chat bubble.
 *
 * Both are stated here, at the top of the app, with the one action that fixes
 * them. Dismissal is per-session: a banner that stays gone after a reload is
 * a banner nobody ever acts on.
 */
export default function HealthBanner({ isAdmin }: { isAdmin: boolean }) {
  const [info, setInfo] = useState<BrainInfo | null>(null);
  const [dismissed, setDismissed] = useState<{ index: boolean; model: boolean }>({
    index: false,
    model: false,
  });
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await apiGet<BrainInfo>("/api/info");
      setInfo(r);
      return r;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      if (timer.current !== null) window.clearInterval(timer.current);
    };
  }, [refresh]);

  // While a rebuild runs, ask often; the socket's index_done is the fast path
  // but polling is what makes this correct with no socket at all. Otherwise
  // tick slowly, because `model_error` only appears once a chat has failed.
  const indexing = info?.indexing ?? false;
  useEffect(() => {
    const id = window.setInterval(() => void refresh(), indexing ? 2000 : 15000);
    timer.current = id;
    return () => window.clearInterval(id);
  }, [indexing, refresh]);

  useEffect(
    () =>
      wsSubscribe((ev) => {
        if (ev.type === "index_done") void refresh();
      }),
    [refresh],
  );

  const reindex = async () => {
    setStarting(true);
    setError(null);
    try {
      await apiSend("POST", "/api/reindex");
      setInfo((i) => (i ? { ...i, indexing: true } : i));
      window.setTimeout(() => void refresh(), 500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not start indexing");
    } finally {
      setStarting(false);
    }
  };

  if (!info) return null;

  const files = info.stats?.files ?? 0;
  const showIndex = (!info.indexed || info.indexing) && !dismissed.index;
  const showModel = !!info.model_error && !dismissed.model;
  if (!showIndex && !showModel) return null;

  return (
    <div className="health-bar">
      {showIndex && (
        <div className="banner banner-notice" role="status">
          <span className="health-text">
            {info.indexing ? (
              <>
                <strong>Indexing…</strong> reading your files. Search and the agent stay
                incomplete until this finishes.
                <span className="health-detail">{files} file{files === 1 ? "" : "s"} indexed so far.</span>
              </>
            ) : (
              <>
                <strong>Nothing is indexed yet.</strong> The agent can only answer from files
                it has read, so right now it will not find anything.
                {!isAdmin && (
                  <span className="health-detail">Ask an admin to run an index.</span>
                )}
                {error && <span className="health-detail">✗ {error}</span>}
              </>
            )}
          </span>
          <span className="banner-actions">
            {!info.indexing && isAdmin && (
              <button className="btn btn-sm primary" onClick={() => void reindex()} disabled={starting}>
                {starting ? "Starting…" : "Index now"}
              </button>
            )}
            <button
              className="btn btn-sm"
              onClick={() => setDismissed((d) => ({ ...d, index: true }))}
            >
              Dismiss
            </button>
          </span>
        </div>
      )}

      {showModel && (
        <div className="banner banner-error" role="alert">
          <span className="health-text">
            <strong>The chat model is not answering.</strong> {info.model_error}
          </span>
          <span className="banner-actions">
            <button
              className="btn btn-sm"
              onClick={() => setDismissed((d) => ({ ...d, model: true }))}
            >
              Dismiss
            </button>
          </span>
        </div>
      )}
    </div>
  );
}
