import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, ApiError } from "../api";
import type { Digest, DigestTask } from "../types";
import { splitVaultKey, shortAgo, eventWhen } from "../lib/paths";

/**
 * An index key. Everything the digest lists is readable — `sources/…` files
 * open read-only through /api/file — so every key here is a link, and a
 * calendar event finally goes somewhere.
 */
function PathLink({ path, onVaultPath }: { path: string; onVaultPath: (p: string) => void }) {
  const inVault = splitVaultKey(path) !== null;
  return (
    <button
      className="mono path-link"
      onClick={() => onVaultPath(path)}
      title={inVault ? `Open ${path}` : `Open ${path} (read-only)`}
    >
      {path}
    </button>
  );
}

function taskKey(t: DigestTask): string {
  return `${t.path}:${t.line}`;
}

export default function Today({
  active,
  refreshKey,
  isAdmin,
  onVaultPath,
  onCapture,
  onImport,
  onExtend,
}: {
  active: boolean;
  /** bumped after a capture so Today re-reads the digest */
  refreshKey: number;
  isAdmin: boolean;
  onVaultPath: (path: string) => void;
  onCapture: () => void;
  onImport: () => void;
  onExtend: () => void;
}) {
  const [digest, setDigest] = useState<Digest | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** ticked locally so the checkbox reacts before the digest comes back */
  const [ticked, setTicked] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      const r = await apiGet<Digest>("/api/digest");
      setDigest(r);
      setTicked(new Set());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load today");
    }
  }, []);

  useEffect(() => {
    if (active) void load();
  }, [active, refreshKey, load]);

  /** Tick the real `- [ ]` on its line and write the file back. */
  const completeTask = async (task: DigestTask) => {
    const target = splitVaultKey(task.path);
    if (!target) return;
    const { vault, path } = target;
    const key = taskKey(task);
    setTicked((s) => new Set(s).add(key));
    setError(null);
    try {
      const file = await apiGet<{ text: string; mtime: number }>(
        `/api/vault/file?vault=${encodeURIComponent(vault)}&path=${encodeURIComponent(path)}`,
      );
      const lines = file.text.split("\n");
      const i = task.line - 1;
      const line = i >= 0 && i < lines.length ? lines[i] : undefined;
      if (line === undefined || !line.includes("[ ]")) {
        throw new Error("that task moved in the file — reloading");
      }
      lines[i] = line.replace("[ ]", "[x]");
      await apiSend("PUT", "/api/vault/file", {
        vault,
        path,
        text: lines.join("\n"),
        base_mtime: file.mtime,
      });
      await load();
    } catch (e) {
      setTicked((s) => {
        const next = new Set(s);
        next.delete(key);
        return next;
      });
      if (e instanceof ApiError && e.status === 409) {
        setError(`${path} changed on the server since Today loaded. Refresh and try again.`);
      } else {
        setError(e instanceof Error ? e.message : "could not update the task");
      }
      await load();
    }
  };

  if (!digest) {
    return (
      <div className="today-view">
        <div className="wrap today-wrap">
          <h2>Today</h2>
          {error ? (
            <div className="banner banner-error">
              <span>✗ {error}</span>
              <button className="btn btn-sm" onClick={() => void load()}>
                Retry
              </button>
            </div>
          ) : (
            <p className="muted mono">loading…</p>
          )}
        </div>
      </div>
    );
  }

  const todayEvents = digest.events.filter((e) => e.today);
  const later = digest.events.filter((e) => !e.today);
  const empty =
    digest.tasks.length === 0 &&
    digest.events.length === 0 &&
    digest.changed.length === 0 &&
    digest.captured_today === 0;

  return (
    <div className="today-view">
      <div className="wrap today-wrap">
        <div className="today-head">
          <h2>Today</h2>
          <p className="mono today-stats">
            {digest.day} · {digest.captured_today} captured today
          </p>
        </div>

        {error && (
          <div className="banner banner-error">
            <span>✗ {error}</span>
            <button className="btn btn-sm" onClick={() => setError(null)}>
              Dismiss
            </button>
          </div>
        )}

        {empty ? (
          <div className="start-here">
            <p className="lead">
              Nothing here yet. This page fills up on its own once the brain has something to
              read — notes you write, tasks you leave open, a calendar it can see.
            </p>
            <div className="grid three start-grid">
              <div className="card start-card">
                <h3>Capture something</h3>
                <p>
                  One line into today's daily note. Press <kbd>c</kbd> from anywhere.
                </p>
                <button className="btn primary" onClick={onCapture}>
                  Capture now
                </button>
              </div>
              <div className="card start-card">
                <h3>Import your notes</h3>
                <p>A zip, a git repo, or a folder already on the server.</p>
                <button className="btn" onClick={onImport}>
                  Import
                </button>
              </div>
              <div className="card start-card">
                <h3>Connect a calendar</h3>
                <p>
                  {isAdmin
                    ? "A calendar connector writes events into sources, and they show up here."
                    : "An admin can add a calendar connector — events then show up here."}
                </p>
                {isAdmin && (
                  <button className="btn" onClick={onExtend}>
                    Extend
                  </button>
                )}
              </div>
            </div>
          </div>
        ) : (
          <>
            {todayEvents.length > 0 && (
              <section className="today-section">
                <p className="label">Today</p>
                <ul className="today-list">
                  {todayEvents.map((e, i) => (
                    <li key={`${e.path}:${i}`} className="event-row">
                      <span className="mono event-when">{eventWhen(e.start, true)}</span>
                      <span className="event-title">{e.title}</span>
                      <PathLink path={e.path} onVaultPath={onVaultPath} />
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {later.length > 0 && (
              <section className="today-section">
                <p className="label">Coming up</p>
                <ul className="today-list">
                  {later.map((e, i) => (
                    <li key={`${e.path}:${i}`} className="event-row">
                      <span className="mono event-when">{eventWhen(e.start, false)}</span>
                      <span className="event-title">{e.title}</span>
                      <PathLink path={e.path} onVaultPath={onVaultPath} />
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {digest.tasks.length > 0 && (
              <section className="today-section">
                <p className="label">Open tasks</p>
                <ul className="today-list">
                  {digest.tasks.map((t) => {
                    const key = taskKey(t);
                    const done = ticked.has(key);
                    return (
                      <li key={key} className="task-row">
                        <label className="task-check">
                          <input
                            type="checkbox"
                            checked={done}
                            disabled={done || !splitVaultKey(t.path)}
                            onChange={() => void completeTask(t)}
                          />
                          <span className={done ? "task-text task-done" : "task-text"}>
                            {t.text}
                          </span>
                        </label>
                        <PathLink path={t.path} onVaultPath={onVaultPath} />
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}

            {digest.changed.length > 0 && (
              <section className="today-section">
                <p className="label">Changed recently</p>
                <ul className="today-list">
                  {digest.changed.map((c) => (
                    <li key={c.path} className="changed-row">
                      <PathLink path={c.path} onVaultPath={onVaultPath} />
                      <span className="muted changed-at">{shortAgo(c.mtime)}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
}
