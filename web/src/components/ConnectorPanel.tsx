import { useState } from "react";
import { apiSend } from "../api";
import type { Extension } from "../types";

/** Settings box + "Run now" for one connector. Settings are merged over
 *  cortex.yaml server-side, so what you send is an overlay, not a replacement
 *  of the file's values. */
export default function ConnectorPanel({ ext }: { ext: Extension }) {
  const [text, setText] = useState(() => JSON.stringify(ext.detail.settings ?? {}, null, 2));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "run" | null>(null);

  const path = `/api/extensions/connector/${encodeURIComponent(ext.name)}`;

  const saveSettings = async () => {
    let settings: unknown;
    try {
      settings = JSON.parse(text);
    } catch (e) {
      setError(e instanceof Error ? e.message : "settings must be JSON");
      return;
    }
    if (settings === null || typeof settings !== "object" || Array.isArray(settings)) {
      setError("settings must be a JSON object");
      return;
    }
    setBusy("save");
    setError(null);
    setSaved(false);
    try {
      await apiSend("POST", `${path}/settings`, { settings });
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(null);
    }
  };

  const run = async () => {
    setBusy("run");
    setError(null);
    setResult(null);
    try {
      const r = await apiSend<{ name: string; result: string }>("POST", `${path}/run`);
      setResult(r.result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "run failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="ext-panel">
      <label className="field">
        <span>Settings — JSON object</span>
        <textarea
          className="mono"
          rows={6}
          spellCheck={false}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setSaved(false);
          }}
        />
      </label>
      {error && <p className="ext-error">✗ {error}</p>}
      <div className="ext-panel-actions">
        <button className="btn btn-sm" onClick={() => void saveSettings()} disabled={busy !== null}>
          {busy === "save" ? "Saving…" : "Save settings"}
        </button>
        <button className="btn btn-sm" onClick={() => void run()} disabled={busy !== null}>
          {busy === "run" ? "Running…" : "Run now"}
        </button>
        {saved && <span className="run-ok">✓ settings saved</span>}
        {result !== null && (
          <span className={result === "ok" ? "run-ok" : "run-fail"}>
            {result === "ok" ? "✓ ok" : `✗ ${result}`}
          </span>
        )}
      </div>
    </div>
  );
}
