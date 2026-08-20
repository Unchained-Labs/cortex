import { useState } from "react";
import { apiSend } from "../api";
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

  const isSkill = target.kind === "skill";

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
    <div className="drawer-scrim" onMouseDown={onClose}>
      <section className="drawer" onMouseDown={(e) => e.stopPropagation()}>
        <div className="editor-bar">
          <span className="mono editor-path">
            {target.kind} · {target.isNew ? "new" : target.name}
          </span>
          <div className="editor-actions">
            <button className="btn btn-sm" onClick={onClose}>
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
            <span>Name</span>
            <input
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
            : "Python. Saving runs this code on the server to check that it loads."}
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
