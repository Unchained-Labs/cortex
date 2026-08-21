import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet } from "../api";
import type { SearchResult } from "../types";
import { splitVaultKey } from "../lib/paths";

export default function Search({
  focusNonce,
  onVaultPath,
}: {
  /** bumped when "/" is pressed elsewhere, to focus the query box */
  focusNonce: number;
  onVaultPath: (path: string) => void;
}) {
  const [q, setQ] = useState("");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const seenNonce = useRef(0);
  // only the newest query may write state; a slow earlier one is dropped
  const seq = useRef(0);

  const run = useCallback(async (query: string) => {
    const term = query.trim();
    const mine = ++seq.current;
    if (!term) {
      setResult(null);
      setError(null);
      setBusy(false);
      return;
    }
    setBusy(true);
    try {
      const r = await apiGet<SearchResult>(`/api/search?q=${encodeURIComponent(term)}`);
      if (mine !== seq.current) return;
      setResult(r);
      setError(null);
    } catch (e) {
      if (mine !== seq.current) return;
      setResult(null);
      setError(e instanceof Error ? e.message : "search failed");
    } finally {
      if (mine === seq.current) setBusy(false);
    }
  }, []);

  // debounced as you type; Enter just runs the pending query now
  useEffect(() => {
    const t = window.setTimeout(() => void run(q), 250);
    return () => window.clearTimeout(t);
  }, [q, run]);

  useEffect(() => {
    if (focusNonce !== seenNonce.current) {
      seenNonce.current = focusNonce;
      input.current?.focus();
      input.current?.select();
    }
  }, [focusNonce]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void run(q);
    } else if (e.key === "Escape") {
      setQ("");
    }
  };

  return (
    <div className="search-view">
      <div className="search-bar">
        <input
          ref={input}
          className="search-input"
          type="search"
          aria-label="Search your vaults"
          placeholder="Search your vaults — Enter to run"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKey}
        />
        {busy && <span className="mono faint search-busy">searching…</span>}
      </div>

      <div className="search-scroll">
        {error && <p className="form-error">✗ {error}</p>}

        {!q.trim() && !error && (
          <div className="pane-empty">
            <p className="label">Search</p>
            <p className="muted">
              Type to search everything you can read: the shared vault, your own vault and
              connector sources. Press <kbd>/</kbd> from any tab to come back here.
            </p>
          </div>
        )}

        {result && q.trim() && (
          <>
            <p className="mono search-meta">
              {result.hits.length} file{result.hits.length === 1 ? "" : "s"}
              {!result.used_vectors && (
                <span className="search-mode"> · full-text only: no embeddings</span>
              )}
            </p>

            {result.hits.length === 0 && !busy && (
              <p className="muted">Nothing matched. Try fewer or different words.</p>
            )}

            {/* `sources/…` hits are readable too — they open read-only. */}
            {result.hits.map((hit) => {
              const inVault = splitVaultKey(hit.path) !== null;
              return (
                <div key={hit.path} className="search-hit">
                  <div className="search-hit-head">
                    <button
                      className="mono path-link"
                      onClick={() => onVaultPath(hit.path)}
                      title={inVault ? `Open ${hit.path}` : `Open ${hit.path} (read-only)`}
                    >
                      {hit.path}
                    </button>
                    <span className="mono faint search-score">{hit.score.toFixed(3)}</span>
                  </div>
                  {hit.passages.map((p, i) => (
                    <button key={i} className="passage" onClick={() => onVaultPath(hit.path)}>
                      <span className="passage-head mono">
                        {p.heading || "—"}
                        <span className="faint"> · line {p.start_line}</span>
                      </span>
                      <span className="passage-text">{p.text}</span>
                    </button>
                  ))}
                </div>
              );
            })}
          </>
        )}
      </div>
    </div>
  );
}
