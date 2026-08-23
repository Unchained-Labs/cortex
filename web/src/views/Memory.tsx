import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import type { Memory as MemoryRow, MemoryList } from "../types";
import IdentityReadout from "../components/IdentityReadout";

/** `2026-08-21T16:04:46+00:00` → `21 Aug`. Enough to date a belief. */
function shortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** Rows arrive ordered by kind then subject; keep that order, just fold it. */
function groupBySubject(rows: MemoryRow[]): { subject: string; rows: MemoryRow[] }[] {
  const out: { subject: string; rows: MemoryRow[] }[] = [];
  for (const row of rows) {
    const last = out[out.length - 1];
    if (last && last.subject === row.subject) last.rows.push(row);
    else out.push({ subject: row.subject, rows: [row] });
  }
  return out;
}

/**
 * One memory, read or being corrected. Editing happens on the row itself —
 * this is the surface where a wrong belief gets fixed, and a fix three clicks
 * deep is a fix nobody makes.
 */
function Row({
  row,
  kinds,
  onSaved,
  onForgotten,
}: {
  row: MemoryRow;
  kinds: string[];
  onSaved: () => void;
  onForgotten: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(row.body);
  const [kind, setKind] = useState(row.kind);
  const [subject, setSubject] = useState(row.subject);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = () => {
    setBody(row.body);
    setKind(row.kind);
    setSubject(row.subject);
    setError(null);
    setEditing(true);
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!body.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await apiSend("PUT", `/api/memory/${row.id}`, { body: body.trim(), kind, subject });
      setEditing(false);
      onSaved();
    } catch (err) {
      // 422 carries a plain reason. Keep the text as typed.
      setError(err instanceof Error ? err.message : "save failed");
    } finally {
      setBusy(false);
    }
  };

  const forget = async () => {
    if (!window.confirm(`Forget this?\n\n${row.body}\n\nThe brain stops using it.`)) return;
    setBusy(true);
    setError(null);
    try {
      await apiSend("DELETE", `/api/memory/${row.id}`);
      onForgotten();
    } catch (err) {
      setError(err instanceof Error ? err.message : "forget failed");
      setBusy(false);
    }
  };

  if (editing) {
    return (
      <form className="mem-row mem-row-editing" onSubmit={save}>
        <div className="mem-edit-fields">
          <label className="field mem-edit-body">
            <span>Memory</span>
            <input autoFocus value={body} onChange={(e) => setBody(e.target.value)} />
          </label>
          <label className="field mem-edit-kind">
            <span>Kind</span>
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              {kinds.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
          <label className="field mem-edit-subject">
            <span>Subject</span>
            <input
              className="mono"
              placeholder="who or what it is about"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
            />
          </label>
        </div>
        <div className="mem-edit-actions">
          <button className="btn btn-sm" type="button" onClick={() => setEditing(false)}>
            Cancel
          </button>
          <button className="btn btn-sm primary" type="submit" disabled={!body.trim() || busy}>
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
        {error && <p className="form-error mem-row-error">✗ {error}</p>}
      </form>
    );
  }

  return (
    <div className="mem-row">
      <p className="mem-body">{row.body}</p>
      <p className="mem-meta">
        <span className="mono mem-source" title="who or what recorded this">
          {row.source || "unknown"}
        </span>
        <span className="faint"> · {shortDate(row.created_at)}</span>
      </p>
      <div className="mem-row-actions">
        <button className="btn btn-sm" onClick={open}>
          Edit
        </button>
        <button className="btn btn-sm danger" onClick={() => void forget()} disabled={busy}>
          Forget
        </button>
      </div>
      {error && <p className="form-error mem-row-error">✗ {error}</p>}
    </div>
  );
}

export default function Memory({ active, isAdmin }: { active: boolean; isAdmin: boolean }) {
  const [kinds, setKinds] = useState<string[]>([]);
  const [rows, setRows] = useState<MemoryRow[]>([]);
  const [filter, setFilter] = useState(""); // "" = every kind
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // the add form
  const [body, setBody] = useState("");
  const [kind, setKind] = useState("fact");
  const [subject, setSubject] = useState("");
  const [busy, setBusy] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const load = useCallback(async (which: string) => {
    try {
      const r = await apiGet<MemoryList>(
        `/api/memory${which ? `?kind=${encodeURIComponent(which)}` : ""}`,
      );
      setKinds(r.kinds ?? []);
      setRows(r.memories ?? []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load memory");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (active) void load(filter);
  }, [active, filter, load]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!body.trim() || busy) return;
    setBusy(true);
    setAddError(null);
    try {
      await apiSend("POST", "/api/memory", { body: body.trim(), kind, subject });
      setBody("");
      setSubject("");
      await load(filter);
    } catch (err) {
      // 422 says why in plain words; the typed line stays where it is.
      setAddError(err instanceof Error ? err.message : "could not remember that");
    } finally {
      setBusy(false);
    }
  };

  const kindOptions = kinds.length > 0 ? kinds : ["person", "project", "preference", "goal", "fact"];

  // Rows come grouped by kind, alphabetically. Read them in the order the
  // server declares its kinds instead — people and projects before loose
  // facts is how anyone scanning this actually thinks.
  const groups = new Map<string, MemoryRow[]>();
  for (const row of rows) {
    const bucket = groups.get(row.kind);
    if (bucket) bucket.push(row);
    else groups.set(row.kind, [row]);
  }
  const byKind = [
    ...kindOptions.filter((k) => groups.has(k)),
    ...[...groups.keys()].filter((k) => !kindOptions.includes(k)),
  ].map((kind) => ({ kind, rows: groups.get(kind) as MemoryRow[] }));

  return (
    <div className="memory-view">
      <div className="wrap">
        <div className="memory-head">
          <h2>Memory</h2>
          <p className="memory-lead">
            What the brain believes about you and your work. It remembers what you tell it,
            here or in chat, and everything it remembers is listed below for you to correct.
          </p>
        </div>

        {/* Admins read and edit this in Admin → Identity; a second read-only
         *  copy there would just be clutter. Members have no other way in. */}
        {!isAdmin && <IdentityReadout active={active} />}

        {error && (
          <div className="banner banner-error">
            <span>✗ {error}</span>
            <button className="btn btn-sm" onClick={() => void load(filter)}>
              Retry
            </button>
          </div>
        )}

        <form className="card mem-add" onSubmit={add}>
          <div className="mem-add-row">
            <label className="field mem-add-body">
              <span>Remember that…</span>
              <input
                placeholder="sam prefers short answers"
                value={body}
                onChange={(e) => setBody(e.target.value)}
              />
            </label>
            <label className="field mem-add-kind">
              <span>Kind</span>
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                {kindOptions.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </label>
            <label className="field mem-add-subject">
              <span>Subject (optional)</span>
              <input
                className="mono"
                placeholder="sam"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
              />
            </label>
            <button className="btn primary mem-add-btn" type="submit" disabled={!body.trim() || busy}>
              {busy ? "Saving…" : "Remember"}
            </button>
          </div>
          {addError && <p className="form-error mem-add-error">✗ {addError}</p>}
        </form>

        <div className="mem-filter" role="group" aria-label="Filter by kind">
          <div className="seg">
            <button
              className={filter === "" ? "seg-btn active" : "seg-btn"}
              onClick={() => setFilter("")}
            >
              All
            </button>
            {kindOptions.map((k) => (
              <button
                key={k}
                className={filter === k ? "seg-btn active" : "seg-btn"}
                onClick={() => setFilter(k)}
              >
                {k}
              </button>
            ))}
          </div>
        </div>

        {byKind.map((group) => (
          <section className="mem-group" key={group.kind}>
            <h3 className="mem-kind">{group.kind}</h3>
            {groupBySubject(group.rows).map((sub, i) => (
              <div className="mem-subject-block" key={`${sub.subject}-${i}`}>
                {sub.subject ? (
                  <p className="mono mem-subject">{sub.subject}</p>
                ) : (
                  <p className="mem-subject faint">no subject</p>
                )}
                <div className="mem-rows">
                  {sub.rows.map((row) => (
                    <Row
                      key={row.id}
                      row={row}
                      kinds={kindOptions}
                      onSaved={() => void load(filter)}
                      onForgotten={() => void load(filter)}
                    />
                  ))}
                </div>
              </div>
            ))}
          </section>
        ))}

        {loaded && rows.length === 0 && !error && (
          <p className="muted mem-empty">
            {filter
              ? `Nothing remembered under ${filter} yet.`
              : "Nothing remembered yet. The brain keeps what you tell it that should outlast one " +
                "conversation — who someone is, what a project is for, how you like answers " +
                "written — and this is where you read it back and fix anything it got wrong. " +
                "The agent writes here too, whenever you tell it something durable in chat."}
          </p>
        )}
      </div>
    </div>
  );
}
