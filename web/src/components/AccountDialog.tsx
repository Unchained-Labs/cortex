import { useEffect, useRef, useState } from "react";
import { apiSend } from "../api";
import { useModalKeys } from "../lib/modal";
import type { Me } from "../types";

/**
 * Your own account: who you are signed in as, and a way to change the
 * password without asking an admin to do it for you.
 */
export default function AccountDialog({ user, onClose }: { user: Me; onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const card = useRef<HTMLDivElement>(null);
  const first = useRef<HTMLInputElement>(null);

  useModalKeys(card, onClose);
  useEffect(() => {
    first.current?.focus();
  }, []);

  const mismatch = next !== "" && again !== "" && next !== again;
  const ready = current !== "" && next.length >= 8 && next === again && !busy;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      await apiSend("POST", "/api/me/password", {
        current_password: current,
        new_password: next,
      });
      setDone(true);
      setCurrent("");
      setNext("");
      setAgain("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not change the password");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="modal-scrim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="account-title"
        ref={card}
      >
        <div className="modal-head">
          <h3 id="account-title">Account</h3>
          <span className="mono muted">
            {user.username} · {user.role}
          </span>
        </div>

        {done ? (
          <>
            <div className="banner banner-ok">✓ Password changed. Other sessions are signed out.</div>
            <div className="modal-actions">
              <button className="btn" onClick={onClose}>
                Close
              </button>
            </div>
          </>
        ) : (
          <form onSubmit={submit}>
            <p className="muted modal-note">At least 8 characters.</p>
            <label className="field">
              <span>Current password</span>
              <input
                ref={first}
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
              />
            </label>
            <label className="field">
              <span>New password</span>
              <input
                type="password"
                autoComplete="new-password"
                value={next}
                onChange={(e) => setNext(e.target.value)}
              />
            </label>
            <label className="field">
              <span>New password again</span>
              <input
                type="password"
                autoComplete="new-password"
                value={again}
                onChange={(e) => setAgain(e.target.value)}
              />
            </label>
            {mismatch && <p className="form-error">✗ The two new passwords do not match.</p>}
            {error && <p className="form-error">✗ {error}</p>}
            <div className="modal-actions">
              <button className="btn" type="button" onClick={onClose}>
                Cancel
              </button>
              <button className="btn primary" type="submit" disabled={!ready}>
                {busy ? "Saving…" : "Change password"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
