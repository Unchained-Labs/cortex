import { useCallback, useEffect, useRef, useState } from "react";
import { apiSend } from "../api";
import { useModalKeys, useUnloadGuard } from "../lib/modal";
import { composeTemplate } from "../lib/templates";
import Editor from "./Editor";

/** One open editing session. `nonce` keys the component so every open starts
 *  from a clean state and a fresh CodeMirror document. */
export interface TemplateTarget {
  name: string;
  /** a new template — the name is still editable */
  isNew: boolean;
  title: string;
  target: string;
  body: string;
  /** placeholder names from the list response, rendered as a hint */
  placeholders: string[];
  nonce: number;
}

export default function TemplateEditor({
  target,
  onClose,
  onSaved,
}: {
  target: TemplateTarget;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(target.name);
  const [title, setTitle] = useState(target.title);
  const [pattern, setPattern] = useState(target.target);
  const [body, setBody] = useState(target.body);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const drawer = useRef<HTMLElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);

  const dirty =
    name !== target.name ||
    title !== target.title ||
    pattern !== target.target ||
    body !== target.body;

  const titleId = `template-drawer-title-${target.nonce}`;

  const requestClose = useCallback(() => {
    if (dirty && !window.confirm("Discard the changes in this template?")) return;
    onClose();
  }, [dirty, onClose]);

  useModalKeys(drawer, requestClose);
  useUnloadGuard(dirty);

  useEffect(() => {
    const el = nameInput.current;
    if (el && !el.disabled) el.focus();
    else drawer.current?.querySelector<HTMLElement>("button")?.focus();
  }, [target.nonce]);

  const save = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await apiSend("PUT", "/api/templates", {
        name: name.trim(),
        body: composeTemplate(title, pattern, body),
      });
      onSaved();
    } catch (e) {
      // 422 carries the server's own reason. The drawer stays open with the
      // text untouched — something that was refused must never look saved.
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="drawer-scrim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) requestClose();
      }}
    >
      <section
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={drawer}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="editor-bar">
          <span className="mono editor-path" id={titleId}>
            template · {target.isNew ? "new" : target.name}
            {dirty && <span className="dirty-dot" title="unsaved changes" />}
          </span>
          <div className="editor-actions">
            <button className="btn btn-sm" onClick={requestClose}>
              Close
            </button>
            <button
              className="btn btn-sm primary"
              onClick={() => void save()}
              disabled={!name.trim() || busy}
              title="Ctrl+S"
            >
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
        </div>

        {error && <p className="drawer-error">✗ {error}</p>}

        <div className="drawer-fields">
          <label className="field">
            <span>File name</span>
            <input
              ref={nameInput}
              className="mono"
              autoComplete="off"
              placeholder="meeting"
              value={name}
              disabled={!target.isNew}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Shown as</span>
            <input
              autoComplete="off"
              placeholder="Meeting"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label className="field tpl-target-field">
            <span>Target</span>
            <input
              className="mono"
              autoComplete="off"
              placeholder="meetings/{{date}}-{{slug}}.md"
              value={pattern}
              onChange={(e) => setPattern(e.target.value)}
            />
          </label>
        </div>

        <p className="drawer-hint muted">
          Markdown. Placeholders:{" "}
          <span className="pills tpl-hint-pills">
            {target.placeholders.map((p) => (
              <span className="pill mono" key={p}>{`{{${p}}}`}</span>
            ))}
          </span>{" "}
          An unknown one is left visible rather than blanked. Esc closes.
        </p>

        <Editor
          docKey={`tpl:${target.name}:${target.nonce}`}
          initialText={body}
          onChange={setBody}
          onSave={() => void save()}
          language="plain"
        />
      </section>
    </div>
  );
}
