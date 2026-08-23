import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import type { NoteTemplate, TemplateList } from "../types";
import TemplateEditor, { type TemplateTarget } from "./TemplateEditor";
import { NEW_TEMPLATE_BODY } from "../lib/templates";

/**
 * The shared template set, edited.
 *
 * It sits in Extend because Extend is already "what the brain can reach
 * for" — a template is a shape a note starts from, alongside the skills and
 * connectors, rather than a tab of its own. Using one lives in the Vault,
 * where the notes are; only writing one is admin work.
 */
export default function TemplatesSection({ active }: { active: boolean }) {
  const [templates, setTemplates] = useState<NoteTemplate[]>([]);
  const [placeholders, setPlaceholders] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [installed, setInstalled] = useState<string | null>(null);
  const [target, setTarget] = useState<TemplateTarget | null>(null);

  const load = useCallback(() => {
    apiGet<TemplateList>("/api/templates")
      .then((r) => {
        setTemplates(r.templates ?? []);
        setPlaceholders(r.placeholders ?? []);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load templates"));
  }, []);

  useEffect(() => {
    if (active) load();
  }, [active, load]);

  const install = async () => {
    setBusy(true);
    setError(null);
    setInstalled(null);
    try {
      const r = await apiSend<{ written: string[] }>("POST", "/api/templates/install");
      const written = r.written ?? [];
      setInstalled(
        written.length === 0
          ? "Nothing to add — every starter template is already here."
          : `Wrote ${written.length} template${written.length === 1 ? "" : "s"}: ${written.join(", ")}.`,
      );
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not add the starter set");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (t: NoteTemplate) => {
    if (!window.confirm(`Delete the ${t.title} template? Notes already made from it stay.`)) {
      return;
    }
    setError(null);
    setInstalled(null);
    try {
      await apiSend("DELETE", `/api/templates/${encodeURIComponent(t.name)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "delete failed");
    }
    load();
  };

  const openNew = () =>
    setTarget({
      name: "",
      isNew: true,
      title: "",
      target: "{{slug}}.md",
      body: NEW_TEMPLATE_BODY,
      placeholders,
      nonce: Date.now(),
    });

  const openEdit = (t: NoteTemplate) =>
    setTarget({
      name: t.name,
      isNew: false,
      title: t.title,
      target: t.target,
      body: t.body,
      placeholders,
      nonce: Date.now(),
    });

  return (
    <section className="card ext-section">
      <div className="ext-section-head">
        <h3>Note templates</h3>
        <button className="btn btn-sm" onClick={openNew}>
          Write your own
        </button>
      </div>
      <p className="ext-blurb">
        A shape a note starts from — a meeting, a person, a trip. Anyone picks one from the
        Vault with "New from template"; the template says where the note lands.
      </p>

      {error && <p className="ext-error">✗ {error}</p>}
      {installed && <p className="tpl-said">✓ {installed}</p>}

      {templates.length === 0 ? (
        <p className="muted ext-empty">
          No templates yet. The starter set covers meetings, people, trips, recipes and a
          weekly review.
        </p>
      ) : (
        templates.map((t) => (
          <div className="ext-row" key={t.name}>
            <div className="ext-head">
              <span className="ext-name">{t.title}</span>
              <span className="mono tpl-row-name">{t.name}</span>
              <div className="ext-actions">
                <button className="btn btn-sm" onClick={() => openEdit(t)}>
                  Edit
                </button>
                <button className="btn btn-sm danger" onClick={() => void remove(t)}>
                  Delete
                </button>
              </div>
            </div>
            <div className="ext-provides">
              <span className="mono ext-detail">→ {t.target}</span>
            </div>
          </div>
        ))
      )}

      <div className="tpl-foot">
        <button className="btn btn-sm" onClick={() => void install()} disabled={busy}>
          {busy ? "Adding…" : "Add the starter set"}
        </button>
        <span className="muted tpl-placeholders">
          Placeholders:{" "}
          {placeholders.length === 0
            ? "—"
            : placeholders.map((p) => (
                <span className="mono tpl-ph" key={p}>{`{{${p}}}`}</span>
              ))}
        </span>
      </div>

      {target && (
        <TemplateEditor
          key={target.nonce}
          target={target}
          onClose={() => setTarget(null)}
          onSaved={() => {
            setTarget(null);
            setInstalled(null);
            load();
          }}
        />
      )}
    </section>
  );
}
