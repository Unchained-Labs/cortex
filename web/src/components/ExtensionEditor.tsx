import { useCallback, useEffect, useRef, useState } from "react";
import { apiSend } from "../api";
import { useModalKeys, useUnloadGuard } from "../lib/modal";
import Editor from "./Editor";

/** One open editing session. `nonce` keys the component so every open starts
 *  from a clean state and a fresh CodeMirror document. */
export interface EditorTarget {
  kind: "plugin" | "connector" | "skill";
  name: string;
  /** a new extension — the name is still editable */
  isNew: boolean;
  code: string;
  description: string;
  instructions: string;
  nonce: number;
}

export default function ExtensionEditor({
  target,
  onClose,
  onSaved,
}: {
  target: EditorTarget;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(target.name);
  const [code, setCode] = useState(target.code);
  const [description, setDescription] = useState(target.description);
  const [instructions, setInstructions] = useState(target.instructions);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const drawer = useRef<HTMLElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);

  const isSkill = target.kind === "skill";

  // What the drawer opened with, so "did the author change anything?" is a
  // comparison rather than a guess.
  const dirty =
    name !== target.name ||
    code !== target.code ||
    description !== target.description ||
    instructions !== target.instructions;

  const titleId = `drawer-title-${target.kind}-${target.nonce}`;

  /** Escape and a backdrop click both land here: unsaved code is never
   *  discarded without asking first. */
  const requestClose = useCallback(() => {
    if (dirty && !window.confirm("Discard the changes in this editor?")) return;
    onClose();
  }, [dirty, onClose]);

  useModalKeys(drawer, requestClose);
  useUnloadGuard(dirty);

  // Focus lands inside the drawer on open — the Name field when it is
  // editable, the first control otherwise.
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
      const body = isSkill
        ? { name: name.trim(), description, instructions }
        : { name: name.trim(), code };
      await apiSend("PUT", `/api/extensions/${target.kind}`, body);
      onSaved();
    } catch (e) {
      // 422 carries the loader's own message. Keep the drawer open with the
      // text untouched — something that will not load must never look saved.
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
            {target.kind} · {target.isNew ? "new" : target.name}
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

        {/* The warning belongs where code is written, not at the top of a page
            most people open to read a list. Same words, one step closer. */}
        {!isSkill && (
          <div className="banner banner-notice drawer-warning">
            <span>
              Saving a plugin or connector runs that code on the server, as the cortex user.
              It is the same trust level as a stdio MCP server, and the reason this panel is
              admin-only.
            </span>
          </div>
        )}

        <div className="drawer-fields">
          <label className="field">
            <span>Name</span>
            <input
              ref={nameInput}
              className="mono"
              autoComplete="off"
              placeholder="lowercase letters, digits, - or _"
              value={name}
              disabled={!target.isNew}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          {isSkill && (
            <label className="field">
              <span>Description</span>
              <input
                autoComplete="off"
                placeholder="when the agent should reach for this skill"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
          )}
        </div>

        <p className="drawer-hint muted">
          {isSkill
            ? "Instructions the agent follows literally."
            : "Python. Saving runs this code on the server to check that it loads."}{" "}
          Esc closes.
        </p>

        <Editor
          docKey={`ext:${target.kind}:${target.nonce}`}
          initialText={isSkill ? instructions : code}
          onChange={isSkill ? setInstructions : setCode}
          onSave={() => void save()}
          language="plain"
        />
      </section>
    </div>
  );
}
