import { useEffect, useRef, useState } from "react";
import { apiSend } from "../api";
import { useModalKeys } from "../lib/modal";
import type { CaptureResult } from "../types";

/**
 * The one-line capture box: <c> anywhere, Enter files it, Escape cancels.
 * It appends to today's daily note in the caller's own vault.
 */
export default function CaptureBox({
  onClose,
  onCaptured,
}: {
  onClose: () => void;
  /** a line landed — Today should re-read the digest */
  onCaptured: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [landed, setLanded] = useState<CaptureResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<number | null>(null);

  useEffect(() => {
    input.current?.focus();
    return () => {
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    };
  }, []);

  // Escape closes even if focus wandered out of the input, and Tab stays in.
  useModalKeys(box, onClose);

  const submit = async () => {
    const body = text.trim();
    if (!body || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await apiSend<CaptureResult>("POST", "/api/capture", { text: body });
      setText("");
      setLanded(r);
      onCaptured();
      // long enough to read where it went, short enough to stay out of the way
      closeTimer.current = window.setTimeout(onClose, 2200);
    } catch (e) {
      setError(e instanceof Error ? e.message : "capture failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="capture-scrim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="capture-box"
        role="dialog"
        aria-modal="true"
        aria-labelledby="capture-title"
        ref={box}
      >
        <p className="label capture-label" id="capture-title">
          Capture
        </p>
        <input
          ref={input}
          className="capture-input"
          aria-label="Line to capture"
          placeholder="One line — Enter to file it, Esc to cancel"
          value={text}
          disabled={busy}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void submit();
            }
          }}
        />
        {landed && (
          <p className="capture-result" role="status">
            <span className="capture-ok">✓ Filed in {landed.vault}</span>{" "}
            {/* path:line, so the line is findable in the file, not just the file */}
            <span className="mono capture-where">
              {landed.path}:{landed.line}
            </span>
          </p>
        )}
        {error && <p className="form-error capture-error">✗ {error}</p>}
        {!landed && !error && (
          <p className="muted capture-hint">Goes to today's daily note in your own vault.</p>
        )}
      </div>
    </div>
  );
}
