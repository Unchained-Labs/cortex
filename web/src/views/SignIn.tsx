import { useEffect, useState } from "react";
import { apiSend, ApiError } from "../api";
import type { Me } from "../types";

export default function SignIn({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Which brain this is. The card showed the company lockup and the words
  // "Sign in" and nothing else, so someone arriving at a bookmarked URL had no
  // way to tell what they were signing into — or which household, if they have
  // more than one. /health is public and already knows.
  const [brain, setBrain] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/health")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { brain?: string } | null) => {
        if (!cancelled && d?.brain) setBrain(d.brain);
      })
      .catch(() => {
        /* the name is a courtesy; sign-in works without it */
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
        <p className="label">Sign in{brain ? ` · ${brain}` : ""}</p>
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
        {/* There is no self-service reset on a self-hosted brain, and saying so
            is better than leaving someone stuck at a form that cannot help. */}
        <p className="signin-hint">
          Forgotten your password? Whoever set this up can reset it with{" "}
          <code>cortex users passwd {username || "<name>"}</code>.
        </p>
      </form>
    </div>
  );
}
