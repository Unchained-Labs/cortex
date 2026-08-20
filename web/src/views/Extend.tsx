import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import type { Extension, ExtensionList } from "../types";
import ConnectorPanel from "../components/ConnectorPanel";
import ExtensionEditor, { type EditorTarget } from "../components/ExtensionEditor";
import McpForm, { type McpTarget } from "../components/McpForm";

const EMPTY: ExtensionList = {
  plugins: [],
  skills: [],
  connectors: [],
  mcp_servers: [],
  load_errors: [],
  mcp_errors: [],
};

/** What the extension gives the agent, in one line per kind. */
function Provides({ ext }: { ext: Extension }) {
  if (ext.kind === "plugin") {
    if (ext.tools.length === 0) return <span className="faint">no tools</span>;
    return (
      <span className="pills">
        {ext.tools.map((t) => (
          <span className="pill mono" key={t}>
            {t}
          </span>
        ))}
      </span>
    );
  }
  if (ext.kind === "mcp") {
    const d = ext.detail;
    const transport = d.transport ?? "stdio";
    const target = transport === "http" ? d.url : [d.command, ...(d.args ?? [])].join(" ").trim();
    return (
      <span className="mono ext-detail">
        {transport}
        {target ? ` · ${target}` : ""}
      </span>
    );
  }
  if (ext.kind === "connector") {
    const keys = Object.keys(ext.detail.settings ?? {});
    return (
      <span className="muted">
        {ext.source === "builtin" ? "built-in" : "custom"}
        {keys.length > 0 ? ` · settings: ${keys.join(", ")}` : " · no settings"}
      </span>
    );
  }
  return ext.description ? (
    <span className="muted">{ext.description}</span>
  ) : (
    <span className="faint">no description</span>
  );
}

function Row({
  ext,
  onToggle,
  onEdit,
  onDelete,
}: {
  ext: Extension;
  onToggle: (ext: Extension, enabled: boolean) => void;
  onEdit: (ext: Extension) => void;
  onDelete: (ext: Extension) => void;
}) {
  const [open, setOpen] = useState(false);
  const fromFile = ext.source === "file";
  // builtins live in the codebase: they toggle and run, but have no source
  // file to edit and nothing to delete.
  const editable = ext.source === "dashboard";

  return (
    <div className="ext-row">
      <div className="ext-head">
        <span className="mono ext-name">{ext.name}</span>
        {fromFile && <span className="badge">cortex.yaml</span>}
        {ext.source === "builtin" && <span className="badge">built-in</span>}
        <div className="ext-actions">
          {fromFile ? (
            <span className="muted ext-readonly">
              defined in cortex.yaml — edit the file to change it
            </span>
          ) : (
            <label className="toggle">
              <input
                type="checkbox"
                checked={ext.enabled}
                onChange={(e) => onToggle(ext, e.target.checked)}
              />
              <span>Enabled</span>
            </label>
          )}
          {ext.kind === "connector" && (
            <button className="btn btn-sm" onClick={() => setOpen(!open)}>
              {open ? "Hide settings" : "Settings"}
            </button>
          )}
          {editable && (
            <button className="btn btn-sm" onClick={() => onEdit(ext)}>
              Edit
            </button>
          )}
          {editable && (
            <button className="btn btn-sm danger" onClick={() => onDelete(ext)}>
              Delete
            </button>
          )}
        </div>
      </div>
      <div className="ext-provides">
        <Provides ext={ext} />
      </div>
      {ext.error && <p className="ext-error">✗ {ext.error}</p>}
      {open && ext.kind === "connector" && <ConnectorPanel ext={ext} />}
    </div>
  );
}

function Section({
  title,
  items,
  onNew,
  children,
}: {
  title: string;
  items: Extension[];
  onNew: () => void;
  children: (ext: Extension) => React.ReactNode;
}) {
  return (
    <section className="card ext-section">
      <div className="ext-section-head">
        <h3>{title}</h3>
        <button className="btn btn-sm" onClick={onNew}>
          + New
        </button>
      </div>
      {items.length === 0 ? (
        <p className="muted">None yet.</p>
      ) : (
        items.map((ext) => <div key={ext.name}>{children(ext)}</div>)
      )}
    </section>
  );
}

export default function Extend({ active }: { active: boolean }) {
  const [list, setList] = useState<ExtensionList>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<EditorTarget | null>(null);
  const [mcp, setMcp] = useState<McpTarget | null>(null);

  const load = useCallback(() => {
    apiGet<ExtensionList>("/api/extensions")
      .then((r) => setList({ ...EMPTY, ...r }))
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load extensions"));
  }, []);

  useEffect(() => {
    if (active) load();
  }, [active, load]);

  const openNew = async (kind: "plugin" | "connector" | "skill") => {
    setError(null);
    try {
      const s = await apiGet<{ code?: string; description?: string; instructions?: string }>(
        `/api/extensions/scaffold?kind=${kind}`,
      );
      setTarget({
        kind,
        name: "",
        isNew: true,
        code: s.code ?? "",
        description: s.description ?? "",
        instructions: s.instructions ?? "",
        nonce: Date.now(),
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load the scaffold");
    }
  };

  const openEdit = async (ext: Extension) => {
    if (ext.kind === "mcp") {
      setMcp({ ext, nonce: Date.now() });
      return;
    }
    if (ext.kind !== "plugin" && ext.kind !== "connector" && ext.kind !== "skill") return;
    setError(null);
    try {
      const s = await apiGet<{ code?: string; description?: string; instructions?: string }>(
        `/api/extensions/source?kind=${ext.kind}&name=${encodeURIComponent(ext.name)}`,
      );
      setTarget({
        kind: ext.kind,
        name: ext.name,
        isNew: false,
        code: s.code ?? "",
        description: s.description ?? "",
        instructions: s.instructions ?? "",
        nonce: Date.now(),
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load the source");
    }
  };

  const toggle = async (ext: Extension, enabled: boolean) => {
    setError(null);
    try {
      await apiSend(
        "POST",
        `/api/extensions/${ext.kind}/${encodeURIComponent(ext.name)}/enabled`,
        { enabled },
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "toggle failed");
    }
    load();
  };

  const remove = async (ext: Extension) => {
    if (!window.confirm(`Delete ${ext.kind} ${ext.name}? This removes it from the brain.`)) {
      return;
    }
    setError(null);
    try {
      await apiSend("DELETE", `/api/extensions/${ext.kind}/${encodeURIComponent(ext.name)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "delete failed");
    }
    load();
  };

  const saved = () => {
    setTarget(null);
    setMcp(null);
    load();
  };

  const row = (ext: Extension) => (
    <Row ext={ext} onToggle={toggle} onEdit={openEdit} onDelete={remove} />
  );

  return (
    <div className="extend-view">
      <div className="wrap">
        <h2>Extend</h2>

        <div className="banner banner-notice extend-warning">
          <span>
            Saving a plugin or connector runs that code on the server, as the cortex user. It
            is the same trust level as a stdio MCP server, and the reason this panel is
            admin-only.
          </span>
        </div>

        {error && (
          <div className="banner banner-error">
            <span>✗ {error}</span>
            <button className="btn btn-sm" onClick={() => setError(null)}>
              Dismiss
            </button>
          </div>
        )}

        {list.load_errors.length > 0 && (
          <div className="banner banner-error extend-strip">
            <div>
              <p className="label">Load errors</p>
              {list.load_errors.map((e, i) => (
                <p className="ext-error" key={i}>
                  {e}
                </p>
              ))}
            </div>
          </div>
        )}
        {list.mcp_errors.length > 0 && (
          <div className="banner banner-error extend-strip">
            <div>
              <p className="label">MCP errors</p>
              {list.mcp_errors.map((e, i) => (
                <p className="ext-error" key={i}>
                  {e}
                </p>
              ))}
            </div>
          </div>
        )}

        <Section title="Plugins" items={list.plugins} onNew={() => void openNew("plugin")}>
          {row}
        </Section>
        <Section title="Skills" items={list.skills} onNew={() => void openNew("skill")}>
          {row}
        </Section>
        <Section
          title="Connectors"
          items={list.connectors}
          onNew={() => void openNew("connector")}
        >
          {row}
        </Section>
        <Section
          title="MCP servers"
          items={list.mcp_servers}
          onNew={() => setMcp({ ext: null, nonce: Date.now() })}
        >
          {row}
        </Section>

        <p className="muted extend-foot">
          Saving rebuilds the agent, so new tools are live on the next turn without a restart.
        </p>
      </div>

      {target && (
        <ExtensionEditor
          key={target.nonce}
          target={target}
          onClose={() => setTarget(null)}
          onSaved={saved}
        />
      )}
      {mcp && (
        <McpForm key={mcp.nonce} target={mcp} onClose={() => setMcp(null)} onSaved={saved} />
      )}
    </div>
  );
}
