import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiSend, ApiError, rawUrl } from "../api";
import { wsSubscribe } from "../ws";
import type { VaultMeta, VaultFile } from "../types";
import type { VaultTarget } from "../App";
import { buildTree, type TreeNode } from "../lib/tree";
import { splitFrontmatter, toggleTask, resolveTarget, isImagePath } from "../lib/obsidian";
import Editor from "../components/Editor";
import Markdown from "../components/Markdown";

type Mode = "edit" | "preview";

function TreeView({
  nodes,
  collapsed,
  openPath,
  onToggle,
  onOpen,
  depth,
}: {
  nodes: TreeNode[];
  collapsed: Set<string>;
  openPath: string | null;
  onToggle: (path: string) => void;
  onOpen: (path: string) => void;
  depth: number;
}) {
  return (
    <>
      {nodes.map((n) =>
        n.kind === "dir" ? (
          <div key={n.path}>
            <button
              className="tree-row tree-dir"
              style={{ paddingLeft: `${depth * 14 + 8}px` }}
              onClick={() => onToggle(n.path)}
            >
              <span className="tree-glyph">{collapsed.has(n.path) ? "▸" : "▾"}</span>
              {n.name}
            </button>
            {!collapsed.has(n.path) && (
              <TreeView
                nodes={n.children}
                collapsed={collapsed}
                openPath={openPath}
                onToggle={onToggle}
                onOpen={onOpen}
                depth={depth + 1}
              />
            )}
          </div>
        ) : (
          <button
            key={n.path}
            className={n.path === openPath ? "tree-row tree-file active" : "tree-row tree-file"}
            style={{ paddingLeft: `${depth * 14 + 8}px` }}
            onClick={() => onOpen(n.path)}
            title={n.path}
          >
            <span className="tree-glyph faint">{n.name.endsWith(".md") ? "▪" : "▫"}</span>
            {n.name}
          </button>
        ),
      )}
    </>
  );
}

export default function Vault({ target }: { target: VaultTarget | null }) {
  const [vaults, setVaults] = useState<VaultMeta[]>([]);
  const [vault, setVault] = useState<string | null>(null);
  const [files, setFiles] = useState<VaultFile[]>([]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [savedText, setSavedText] = useState("");
  const [baseMtime, setBaseMtime] = useState<number | null>(null);
  const [mode, setMode] = useState<Mode>("edit");
  const [conflict, setConflict] = useState<number | null>(null); // server_mtime
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newPath, setNewPath] = useState<string | null>(null); // null = closed
  const seenNonce = useRef(0);

  const vaultRef = useRef(vault);
  vaultRef.current = vault;
  // Ctrl-S reaches both the CodeMirror keymap and the container handler in
  // the same keystroke; without this guard the second concurrent save races
  // the first and 409s against its own sibling.
  const savingRef = useRef(false);
  const openPathRef = useRef(openPath);
  openPathRef.current = openPath;
  const dirty = text !== savedText;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  const loadTree = useCallback(async (v: string): Promise<VaultFile[]> => {
    const r = await apiGet<{ vault: string; files: VaultFile[] }>(
      `/api/vault/tree?vault=${encodeURIComponent(v)}`,
    );
    setFiles(r.files);
    return r.files;
  }, []);

  const openFile = useCallback(async (v: string, path: string) => {
    setError(null);
    setConflict(null);
    setNotice(null);
    if (!path.endsWith(".md") && !path.endsWith(".txt")) {
      // binary/attachment: preview only
      setOpenPath(path);
      setText("");
      setSavedText("");
      setBaseMtime(null);
      setMode("preview");
      return;
    }
    try {
      const r = await apiGet<{ vault: string; path: string; text: string; mtime: number }>(
        `/api/vault/file?vault=${encodeURIComponent(v)}&path=${encodeURIComponent(path)}`,
      );
      setOpenPath(r.path);
      setText(r.text);
      setSavedText(r.text);
      setBaseMtime(r.mtime);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to open file");
    }
  }, []);

  const selectVault = useCallback(
    async (v: string, openAfter?: string) => {
      setVault(v);
      setOpenPath(null);
      setText("");
      setSavedText("");
      setConflict(null);
      setNotice(null);
      setCollapsed(new Set());
      try {
        await loadTree(v);
        if (openAfter) await openFile(v, openAfter);
      } catch (e) {
        setFiles([]);
        setError(e instanceof Error ? e.message : "failed to load vault");
      }
    },
    [loadTree, openFile],
  );

  useEffect(() => {
    apiGet<{ vaults: VaultMeta[] }>("/api/vaults")
      .then((r) => {
        setVaults(r.vaults);
        if (!vaultRef.current && r.vaults.length > 0 && seenNonce.current === 0) {
          const first = r.vaults.find((v) => v.kind === "shared") ?? r.vaults[0];
          void selectVault(first.name);
        }
      })
      .catch(() => {});
  }, [selectVault]);

  // Citation navigation from Chat.
  useEffect(() => {
    if (target && target.nonce !== seenNonce.current) {
      seenNonce.current = target.nonce;
      void selectVault(target.vault, target.path);
    }
  }, [target, selectVault]);

  // Another session saved a file we may have open.
  useEffect(
    () =>
      wsSubscribe((ev) => {
        if (ev.type !== "vault_changed") return;
        if (ev.vault !== vaultRef.current) return;
        void loadTree(ev.vault).catch(() => {});
        if (ev.path === openPathRef.current) {
          if (dirtyRef.current) {
            setNotice("This file changed on the server. Saving will surface a conflict.");
          } else {
            void openFile(ev.vault, ev.path);
          }
        }
      }),
    [loadTree, openFile],
  );

  const save = useCallback(
    async (forceBase?: number) => {
      const v = vaultRef.current;
      const p = openPathRef.current;
      if (!v || !p || (!p.endsWith(".md") && !p.endsWith(".txt"))) return;
      if (savingRef.current) return;
      savingRef.current = true;
      setError(null);
      try {
        const body: Record<string, unknown> = { vault: v, path: p, text };
        const base = forceBase ?? baseMtime;
        if (base !== null && base !== undefined) body.base_mtime = base;
        const r = await apiSend<{ mtime: number }>("PUT", "/api/vault/file", body);
        setBaseMtime(r.mtime);
        setSavedText(text);
        setConflict(null);
        setNotice(null);
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          setConflict(typeof e.body.server_mtime === "number" ? e.body.server_mtime : 0);
        } else {
          setError(e instanceof Error ? e.message : "save failed");
        }
      } finally {
        savingRef.current = false;
      }
    },
    [text, baseMtime],
  );

  // Ctrl/Cmd-S also works outside the CodeMirror focus (e.g. preview mode).
  const onKeyDown = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      void save();
    }
  };

  const filePaths = files.map((f) => f.path);

  const resolveEmbed = useCallback(
    (targetName: string): string | null => {
      const v = vaultRef.current;
      if (!v) return null;
      const resolved = resolveTarget(targetName, files.map((f) => f.path));
      if (resolved && isImagePath(resolved)) return rawUrl(v, resolved);
      return null;
    },
    [files],
  );

  const onWikilink = (targetName: string) => {
    const v = vaultRef.current;
    if (!v) return;
    const resolved = resolveTarget(targetName, filePaths);
    if (resolved) void openFile(v, resolved);
    else setNotice(`No note matching [[${targetName}]] in this vault.`);
  };

  const onTaskToggle = (index: number) => {
    const next = toggleTask(text, index);
    if (next !== null) {
      setText(next);
      // write-through: toggling a checkbox saves the file
      void (async () => {
        const v = vaultRef.current;
        const p = openPathRef.current;
        if (!v || !p) return;
        try {
          const body: Record<string, unknown> = { vault: v, path: p, text: next };
          if (baseMtime !== null) body.base_mtime = baseMtime;
          const r = await apiSend<{ mtime: number }>("PUT", "/api/vault/file", body);
          setBaseMtime(r.mtime);
          setSavedText(next);
        } catch (e) {
          if (e instanceof ApiError && e.status === 409) {
            setConflict(typeof e.body.server_mtime === "number" ? e.body.server_mtime : 0);
          } else {
            setError(e instanceof Error ? e.message : "save failed");
          }
        }
      })();
    }
  };

  const createFile = async (e: React.FormEvent) => {
    e.preventDefault();
    const v = vaultRef.current;
    const p = newPath?.trim();
    if (!v || !p) return;
    const path = p.endsWith(".md") || p.includes(".") ? p : `${p}.md`;
    try {
      await apiSend("POST", "/api/vault/file", { vault: v, path, text: "" });
      setNewPath(null);
      await loadTree(v);
      await openFile(v, path);
    } catch (err) {
      setError(err instanceof Error ? err.message : "create failed");
    }
  };

  const deleteFile = async () => {
    const v = vaultRef.current;
    const p = openPathRef.current;
    if (!v || !p) return;
    if (!window.confirm(`Delete ${p} from ${v}?`)) return;
    try {
      await apiSend("DELETE", `/api/vault/file?vault=${encodeURIComponent(v)}&path=${encodeURIComponent(p)}`);
      setOpenPath(null);
      setText("");
      setSavedText("");
      await loadTree(v);
    } catch (err) {
      setError(err instanceof Error ? err.message : "delete failed");
    }
  };

  const reloadServer = () => {
    const v = vaultRef.current;
    const p = openPathRef.current;
    if (v && p) void openFile(v, p);
  };

  const editable = !!openPath && (openPath.endsWith(".md") || openPath.endsWith(".txt"));
  const fm = editable && mode === "preview" ? splitFrontmatter(text) : null;

  return (
    <div className="vault split" onKeyDown={onKeyDown}>
      <aside className="side vault-side">
        <div className="side-head">
          <select
            className="vault-picker mono"
            value={vault ?? ""}
            onChange={(e) => void selectVault(e.target.value)}
          >
            {vault === null && <option value="">— vault —</option>}
            {vaults.map((v) => (
              <option key={v.name} value={v.name}>
                {v.name} ({v.kind}, {v.files})
              </option>
            ))}
          </select>
          <button
            className="btn btn-sm"
            title="New file"
            onClick={() => setNewPath(newPath === null ? "" : null)}
          >
            +
          </button>
        </div>
        {newPath !== null && (
          <form className="side-form" onSubmit={createFile}>
            <input
              autoFocus
              placeholder="folder/note.md"
              value={newPath}
              onChange={(e) => setNewPath(e.target.value)}
            />
          </form>
        )}
        <div className="side-list tree">
          <TreeView
            nodes={buildTree(files)}
            collapsed={collapsed}
            openPath={openPath}
            onToggle={(p) =>
              setCollapsed((c) => {
                const next = new Set(c);
                if (next.has(p)) next.delete(p);
                else next.add(p);
                return next;
              })
            }
            onOpen={(p) => vault && void openFile(vault, p)}
            depth={0}
          />
          {files.length === 0 && <p className="side-empty muted">Empty vault.</p>}
        </div>
      </aside>

      <section className="pane">
        {openPath ? (
          <>
            <div className="editor-bar">
              <span className="mono editor-path" title={openPath}>
                {openPath}
                {dirty && <span className="dirty-dot" title="unsaved changes" />}
              </span>
              <div className="editor-actions">
                {editable && (
                  <>
                    <div className="seg">
                      <button
                        className={mode === "edit" ? "seg-btn active" : "seg-btn"}
                        onClick={() => setMode("edit")}
                      >
                        Edit
                      </button>
                      <button
                        className={mode === "preview" ? "seg-btn active" : "seg-btn"}
                        onClick={() => setMode("preview")}
                      >
                        Preview
                      </button>
                    </div>
                    <button
                      className="btn btn-sm"
                      onClick={() => void save()}
                      disabled={!dirty}
                      title="Ctrl+S"
                    >
                      Save
                    </button>
                  </>
                )}
                <button className="btn btn-sm danger" onClick={() => void deleteFile()}>
                  Delete
                </button>
              </div>
            </div>

            {conflict !== null && (
              <div className="banner banner-conflict">
                <span>
                  ✗ Conflict — this file changed on the server since you loaded it.
                </span>
                <span className="banner-actions">
                  <button className="btn btn-sm" onClick={reloadServer}>
                    Load server version
                  </button>
                  <button className="btn btn-sm danger" onClick={() => void save(conflict)}>
                    Overwrite server
                  </button>
                </span>
              </div>
            )}
            {notice && (
              <div className="banner banner-notice">
                <span>{notice}</span>
                <button className="btn btn-sm" onClick={() => setNotice(null)}>
                  Dismiss
                </button>
              </div>
            )}
            {error && (
              <div className="banner banner-error">
                <span>✗ {error}</span>
                <button className="btn btn-sm" onClick={() => setError(null)}>
                  Dismiss
                </button>
              </div>
            )}

            {editable ? (
              mode === "edit" ? (
                <Editor
                  docKey={`${vault}:${openPath}`}
                  initialText={text}
                  onChange={setText}
                  onSave={() => void save()}
                />
              ) : (
                <div className="preview-scroll">
                  {fm && fm.pairs.length > 0 && (
                    <div className="table-scroll frontmatter">
                      <table>
                        <tbody>
                          {fm.pairs.map(([k, v], i) => (
                            <tr key={i}>
                              <td className="fm-key mono">{k}</td>
                              <td>{v}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  <Markdown
                    text={fm ? fm.body : text}
                    resolveEmbed={resolveEmbed}
                    interactiveTasks
                    onWikilink={onWikilink}
                    onTaskToggle={onTaskToggle}
                  />
                </div>
              )
            ) : isImagePath(openPath) && vault ? (
              <div className="preview-scroll attachment">
                <img src={rawUrl(vault, openPath)} alt={openPath} />
              </div>
            ) : (
              <div className="preview-scroll attachment">
                <p className="muted">
                  Binary attachment.{" "}
                  {vault && (
                    <a href={rawUrl(vault, openPath)} target="_blank" rel="noreferrer">
                      Open raw
                    </a>
                  )}
                </p>
              </div>
            )}
          </>
        ) : (
          <div className="pane-empty">
            <p className="label">Vault</p>
            <p className="muted">Pick a file from the tree, or create one with +.</p>
          </div>
        )}
      </section>
    </div>
  );
}
