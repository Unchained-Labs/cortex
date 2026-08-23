import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiSend } from "../api";
import { useModalKeys } from "../lib/modal";
import type { NewNoteResult, NoteTemplate, TemplateList } from "../types";

/**
 * "New from template", over the Vault view.
 *
 * Capture handles the thought you have no shape for; this is the other half.
 * Picking is the whole interaction — a template, a title, done — so the panel
 * shows each template's `target` pattern rather than making someone guess
 * where the note is about to appear.
 */
export default function TemplateDrawer({
  vault,
  isAdmin,
  onClose,
  onCreated,
}: {
  /** the vault currently open in the tree — the note lands here */
  vault: string | null;
  /** only an admin may write the shared template set */
  isAdmin: boolean;
  onClose: () => void;
  /** a note was created: refresh the tree behind and open it */
  onCreated: (result: NewNoteResult) => void;
}) {
  const [templates, setTemplates] = useState<NoteTemplate[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const drawer = useRef<HTMLElement>(null);
  const titleInput = useRef<HTMLInputElement>(null);

  useModalKeys(drawer, onClose);

  const load = useCallback(async () => {
    try {
      const r = await apiGet<TemplateList>("/api/templates");
      const list = r.templates ?? [];
      setTemplates(list);
      setPicked((p) => (p && list.some((t) => t.name === p) ? p : (list[0]?.name ?? null)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load templates");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Once there is something to pick, the title is the only thing left to type.
  useEffect(() => {
    if (templates.length > 0) titleInput.current?.focus();
  }, [templates.length]);

  const install = async () => {
    setInstalling(true);
    setError(null);
    try {
      await apiSend<{ written: string[] }>("POST", "/api/templates/install");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not add the starter set");
    } finally {
      setInstalling(false);
    }
  };

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!vault || !picked || !title.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await apiSend<NewNoteResult>("POST", "/api/templates/new-note", {
        template: picked,
        vault,
        title: title.trim(),
      });
      onCreated(r);
    } catch (err) {
      // 422 "<path> already exists" is a normal thing to hit — a template
      // starts something and never overwrites. Keep the panel open with the
      // typed title, so the fix is one word rather than a re-entry.
      setError(err instanceof Error ? err.message : "could not create the note");
    } finally {
      setBusy(false);
    }
  };

  const current = templates.find((t) => t.name === picked) ?? null;

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
        aria-labelledby="template-drawer-title"
        ref={drawer}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="editor-bar">
          <span className="mono editor-path" id="template-drawer-title">
            new from template
          </span>
          <div className="editor-actions">
            <button className="btn btn-sm" onClick={onClose}>
              Close
            </button>
          </div>
        </div>

        {error && <p className="drawer-error">✗ {error}</p>}

        <div className="tpl-drawer-body">
          <p className="muted import-lead">
            Pick a shape, give it a title. The note is created where the template says and
            opens straight away, in{" "}
            {vault ? <span className="mono">{vault}</span> : "the selected vault"}. Esc closes.
          </p>

          {loaded && templates.length === 0 ? (
            <div className="tpl-empty">
              <p className="muted">
                No templates yet. A template is a markdown file under{" "}
                <span className="mono">templates/</span> in the brain, with a{" "}
                <span className="mono">target:</span> line saying where its notes land.
              </p>
              {isAdmin ? (
                <button className="btn primary" onClick={() => void install()} disabled={installing}>
                  {installing ? "Adding…" : "Add the starter set"}
                </button>
              ) : (
                <p className="muted">An admin can add the starter set from the Extend tab.</p>
              )}
            </div>
          ) : (
            <form onSubmit={create}>
              <p className="label tpl-pick-label">Template</p>
              <div className="tpl-grid" role="radiogroup" aria-label="Template">
                {templates.map((t) => (
                  <button
                    type="button"
                    key={t.name}
                    role="radio"
                    aria-checked={t.name === picked}
                    className={t.name === picked ? "tpl-card picked" : "tpl-card"}
                    onClick={() => setPicked(t.name)}
                  >
                    <span className="tpl-title">{t.title}</span>
                    <span className="mono tpl-name">{t.name}</span>
                    <span className="mono tpl-target" title={t.target}>
                      {t.target}
                    </span>
                  </button>
                ))}
              </div>

              <label className="field tpl-title-field">
                <span>Title</span>
                <input
                  ref={titleInput}
                  autoComplete="off"
                  placeholder="Kitchen rota"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </label>

              {current && (
                <p className="tpl-lands muted">
                  Lands at <span className="mono">{current.target}</span> in{" "}
                  <span className="mono">{vault ?? "—"}</span>
                </p>
              )}

              <button
                className="btn primary"
                type="submit"
                disabled={!vault || !picked || !title.trim() || busy}
              >
                {busy ? "Creating…" : "Create note"}
              </button>
            </form>
          )}
        </div>
      </section>
    </div>
  );
}
