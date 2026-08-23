import { useEffect, useRef, useState } from "react";
import { apiGet, apiSend, apiUpload } from "../api";
import { useModalKeys } from "../lib/modal";
import type { VaultMeta } from "../types";

interface ImportResult {
  imported: number;
  skipped: number;
}

/**
 * Importing is a vault operation someone does once, not a place they live, so
 * it opens over the Vault view instead of holding a tab of its own. The forms
 * and the result summary are unchanged — only where they live moved.
 */
export default function ImportDrawer({
  vault: initialVault,
  onClose,
  onImported,
}: {
  /** the vault currently open in the tree, preselected as the target */
  vault: string | null;
  onClose: () => void;
  /** an import landed in this vault — the tree behind is now stale */
  onImported: (vault: string) => void;
}) {
  const [vaults, setVaults] = useState<VaultMeta[]>([]);
  const [vault, setVault] = useState(initialVault ?? "");
  const [zip, setZip] = useState<File | null>(null);
  const [gitUrl, setGitUrl] = useState("");
  const [srcPath, setSrcPath] = useState("");
  const [busy, setBusy] = useState<string | null>(null); // which form is running
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const drawer = useRef<HTMLElement>(null);

  useModalKeys(drawer, onClose);

  useEffect(() => {
    apiGet<{ vaults: VaultMeta[] }>("/api/vaults")
      .then((r) => {
        setVaults(r.vaults);
        if (r.vaults.length > 0) setVault((v) => v || r.vaults[0].name);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    drawer.current?.querySelector<HTMLElement>("select")?.focus();
  }, []);

  const run = async (kind: string, fn: () => Promise<ImportResult>) => {
    setBusy(kind);
    setResult(null);
    setError(null);
    try {
      const r = await fn();
      setResult(r);
      onImported(vault);
    } catch (e) {
      setError(e instanceof Error ? e.message : "import failed");
    } finally {
      setBusy(null);
    }
  };

  const uploadZip = (e: React.FormEvent) => {
    e.preventDefault();
    if (!zip || !vault) return;
    const form = new FormData();
    form.append("file", zip);
    form.append("vault", vault);
    void run("zip", async () => {
      const r = await apiUpload<ImportResult>("/api/vault/import", form);
      setZip(null);
      if (fileInput.current) fileInput.current.value = "";
      return r;
    });
  };

  const importGit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!gitUrl.trim() || !vault) return;
    void run("git", () =>
      apiSend<ImportResult>("POST", "/api/vault/import", { vault, git_url: gitUrl.trim() }),
    );
  };

  const importPath = (e: React.FormEvent) => {
    e.preventDefault();
    if (!srcPath.trim() || !vault) return;
    void run("path", () =>
      apiSend<ImportResult>("POST", "/api/vault/import", { vault, src_path: srcPath.trim() }),
    );
  };

  return (
    <div
      className="drawer-scrim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-drawer-title"
        ref={drawer}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="editor-bar">
          <span className="mono editor-path" id="import-drawer-title">
            import
          </span>
          <div className="editor-actions">
            <button className="btn btn-sm" onClick={onClose}>
              Close
            </button>
          </div>
        </div>

        <div className="import-drawer-body">
          <p className="muted import-lead">
            Bring existing notes into a vault. Writes re-index in the background — search may
            lag an import by a few seconds. Esc closes.
          </p>

          <label className="field">
            <span>Target vault</span>
            <select value={vault} onChange={(e) => setVault(e.target.value)}>
              {vaults.map((v) => (
                <option key={v.name} value={v.name}>
                  {v.name} ({v.kind})
                </option>
              ))}
            </select>
          </label>

          {result && (
            <div className="banner banner-ok">
              ✓ Imported {result.imported} file{result.imported === 1 ? "" : "s"}, skipped{" "}
              {result.skipped}.
            </div>
          )}
          {error && <div className="banner banner-error">✗ {error}</div>}

          <div className="import-grid">
            <form className="card" onSubmit={uploadZip}>
              <h3>Zip upload</h3>
              <p>
                Upload a .zip of markdown + attachments. Entries outside the vault root are
                skipped.
              </p>
              <label className="field">
                <span>Zip file</span>
                <input
                  ref={fileInput}
                  type="file"
                  accept=".zip,application/zip"
                  onChange={(e) => setZip(e.target.files?.[0] ?? null)}
                />
              </label>
              <button
                className="btn primary"
                type="submit"
                disabled={!zip || !vault || busy !== null}
              >
                {busy === "zip" ? "Uploading…" : "Upload"}
              </button>
            </form>

            <form className="card" onSubmit={importGit}>
              <h3>Git repository</h3>
              <p>The server clones the repository and imports its markdown.</p>
              <label className="field">
                <span>Repository URL</span>
                <input
                  type="url"
                  placeholder="https://github.com/you/notes.git"
                  value={gitUrl}
                  onChange={(e) => setGitUrl(e.target.value)}
                />
              </label>
              <button
                className="btn primary"
                type="submit"
                disabled={!gitUrl.trim() || !vault || busy !== null}
              >
                {busy === "git" ? "Cloning…" : "Import"}
              </button>
            </form>

            <form className="card" onSubmit={importPath}>
              <h3>Server path</h3>
              <p>Import from a directory that already exists on the server box.</p>
              <label className="field">
                <span>Directory on the server</span>
                <input
                  placeholder="/home/you/obsidian-vault"
                  value={srcPath}
                  onChange={(e) => setSrcPath(e.target.value)}
                />
              </label>
              <button
                className="btn primary"
                type="submit"
                disabled={!srcPath.trim() || !vault || busy !== null}
              >
                {busy === "path" ? "Importing…" : "Import"}
              </button>
            </form>
          </div>
        </div>
      </section>
    </div>
  );
}
