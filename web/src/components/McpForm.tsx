import { useState } from "react";
import { apiSend } from "../api";
import type { Extension } from "../types";

interface HeaderRow {
  key: string;
  value: string;
}

/** One open MCP form. `ext` null means a new server; `nonce` keys the
 *  component so every open starts from a clean state. */
export interface McpTarget {
  ext: Extension | null;
  nonce: number;
}

const toLines = (values: string[] | undefined) => (values ?? []).join("\n");
const fromLines = (text: string) =>
  text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");

export default function McpForm({
  target,
  onClose,
  onSaved,
}: {
  target: McpTarget;
  onClose: () => void;
  onSaved: () => void;
}) {
  const ext = target.ext;
  const detail = ext?.detail ?? {};
  const [name, setName] = useState(ext?.name ?? "");
  const [transport, setTransport] = useState(detail.transport === "http" ? "http" : "stdio");
  const [command, setCommand] = useState(detail.command ?? "");
  const [args, setArgs] = useState(toLines(detail.args));
  const [url, setUrl] = useState(detail.url ?? "");
  // header values are never returned by the API, so an edit starts them empty
  const [headers, setHeaders] = useState<HeaderRow[]>(
    (detail.header_keys ?? []).map((key) => ({ key, value: "" })),
  );
  const [include, setInclude] = useState(toLines(detail.include));
  const [exclude, setExclude] = useState(toLines(detail.exclude));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const setHeader = (index: number, patch: Partial<HeaderRow>) =>
    setHeaders((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const save = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const pairs = headers.filter((h) => h.key.trim() !== "");
      await apiSend("PUT", "/api/extensions/mcp", {
        spec: {
          name: name.trim(),
          transport,
          command: command.trim(),
          args: fromLines(args),
          url: url.trim(),
          headers: Object.fromEntries(pairs.map((h) => [h.key.trim(), h.value])),
          include: fromLines(include),
          exclude: fromLines(exclude),
          enabled: ext ? ext.enabled : true,
        },
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="drawer-scrim" onMouseDown={onClose}>
      <section className="drawer" onMouseDown={(e) => e.stopPropagation()}>
        <div className="editor-bar">
          <span className="mono editor-path">mcp · {ext ? ext.name : "new"}</span>
          <div className="editor-actions">
            <button className="btn btn-sm" onClick={onClose}>
              Close
            </button>
            <button
              className="btn btn-sm primary"
              onClick={() => void save()}
              disabled={!name.trim() || busy}
            >
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
        </div>

        {error && <p className="drawer-error">✗ {error}</p>}

        <div className="drawer-scroll">
          <div className="drawer-fields">
            <label className="field">
              <span>Name</span>
              <input
                className="mono"
                autoComplete="off"
                placeholder="lowercase letters, digits, - or _"
                value={name}
                disabled={ext !== null}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Transport</span>
              <select value={transport} onChange={(e) => setTransport(e.target.value)}>
                <option value="stdio">stdio</option>
                <option value="http">http</option>
              </select>
            </label>
          </div>

          {transport === "stdio" ? (
            <div className="drawer-fields">
              <label className="field">
                <span>Command</span>
                <input
                  className="mono"
                  autoComplete="off"
                  placeholder="npx"
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                />
              </label>
              <label className="field">
                <span>Arguments — one per line</span>
                <textarea
                  className="mono"
                  rows={4}
                  value={args}
                  onChange={(e) => setArgs(e.target.value)}
                />
              </label>
            </div>
          ) : (
            <label className="field">
              <span>URL</span>
              <input
                className="mono"
                type="url"
                autoComplete="off"
                placeholder="https://mcp.example.com/sse"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </label>
          )}

          <p className="label">Headers</p>
          <p className="drawer-hint muted">
            Header values are write-only: the API never returns them, so an existing header
            shows its name with an empty value. Saving replaces the whole header set — a value
            left blank is saved as blank, not kept.
          </p>
          {headers.map((row, i) => (
            <div className="kv-row" key={i}>
              <input
                className="mono"
                autoComplete="off"
                placeholder="Authorization"
                value={row.key}
                onChange={(e) => setHeader(i, { key: e.target.value })}
              />
              <input
                className="mono"
                autoComplete="off"
                placeholder="value"
                value={row.value}
                onChange={(e) => setHeader(i, { value: e.target.value })}
              />
              <button
                className="btn btn-sm danger"
                onClick={() => setHeaders((rows) => rows.filter((_, j) => j !== i))}
              >
                Remove
              </button>
            </div>
          ))}
          <button
            className="btn btn-sm"
            onClick={() => setHeaders((rows) => [...rows, { key: "", value: "" }])}
          >
            + Header
          </button>

          <div className="drawer-fields drawer-fields-top">
            <label className="field">
              <span>Include tools — one per line</span>
              <textarea
                className="mono"
                rows={4}
                value={include}
                onChange={(e) => setInclude(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Exclude tools — one per line</span>
              <textarea
                className="mono"
                rows={4}
                value={exclude}
                onChange={(e) => setExclude(e.target.value)}
              />
            </label>
          </div>
        </div>
      </section>
    </div>
  );
}
