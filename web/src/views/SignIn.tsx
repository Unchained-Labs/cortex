import { useState } from "react";
import { apiSend, ApiError } from "../api";
import type { Me } from "../types";

export default function SignIn({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password || busy) return;
    setBusy(true);
    setError(null);
    try {
      const me = await apiSend<Me>(
        "POST",
        "/api/auth/login",
        { username, password },
        { skip401: true },
      );
      onSignedIn(me);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "network error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="signin">
      <form className="card signin-card" onSubmit={submit}>
        <img
          className="signin-lockup"
          src="/lockup-horizontal.svg"
          alt="cortex"
          onError={(e) => {
            // dev without a public copy: fall back to the backend asset route
            const img = e.currentTarget;
            if (!img.src.endsWith("/assets/lockup-horizontal.svg")) {
              img.src = "/assets/lockup-horizontal.svg";
            }
          }}
        />
        <p className="label">Sign in</p>
        <label className="field">
          <span>Username</span>
          <input
            autoFocus
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <p className="form-error">✗ {error}</p>}
        <button className="btn primary" type="submit" disabled={busy || !username || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
