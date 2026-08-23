import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import type { AdminUser, BrainInfo } from "../types";
import IdentitySection from "../components/IdentitySection";

/** `2026-08-21T16:04:46+00:00` → `21 Aug 2026`. */
function readableDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** The figures a self-hoster actually reads off this page. */
const STAT_LABELS: Record<string, string> = {
  files: "Files",
  chunks: "Chunks",
  vectors: "Vectors",
  facts: "Facts",
};

function ResetPassword({ username, onDone }: { username: string; onDone: () => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = `reset-${username}`;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (value.length < 8 || busy) return;
    setBusy(true);
    setError(null);
    try {
      await apiSend("POST", `/api/admin/users/${encodeURIComponent(username)}/password`, {
        new_password: value,
      });
      setValue("");
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "reset failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="admin-reset" onSubmit={submit}>
      <label className="field">
        <span>New password for {username}</span>
        <input
          id={id}
          type="password"
          autoComplete="new-password"
          placeholder="8+ characters"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
      </label>
      <div className="admin-reset-actions">
        <button className="btn btn-sm" type="button" onClick={onDone}>
          Cancel
        </button>
        <button className="btn btn-sm primary" type="submit" disabled={value.length < 8 || busy}>
          {busy ? "Setting…" : "Set password"}
        </button>
      </div>
      {error && <p className="form-error">✗ {error}</p>}
    </form>
  );
}

export default function Admin({ self, active }: { self: string; active: boolean }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [info, setInfo] = useState<BrainInfo | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"member" | "admin">("member");
  const [resetting, setResetting] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    apiGet<{ users: AdminUser[] }>("/api/admin/users")
      .then((r) => setUsers(r.users))
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load users"));
    apiGet<BrainInfo>("/api/info")
      .then(setInfo)
      .catch(() => setInfo(null));
  }, []);

  useEffect(() => {
    if (active) load();
  }, [active, load]);

  const createUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setError(null);
    try {
      await apiSend("POST", "/api/admin/users", {
        username: username.trim(),
        password,
        role,
      });
      setUsername("");
      setPassword("");
      setRole("member");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "create failed");
    }
  };

  const deleteUser = async (name: string) => {
    // Deleting the account leaves the writing behind; say so before, and say
    // where after, so nobody goes looking for notes they think are gone.
    const ok = window.confirm(
      `Delete the user ${name}?\n\nTheir notes are kept: the vault stays on disk and a ` +
        `future user with the same name would inherit it.`,
    );
    if (!ok) return;
    setError(null);
    setNote(null);
    try {
      const r = await apiSend<{ vault_kept: string }>(
        "DELETE",
        `/api/admin/users/${encodeURIComponent(name)}`,
      );
      setNote(
        r.vault_kept
          ? `${name} deleted. Their notes are kept at ${r.vault_kept}`
          : `${name} deleted. They had no vault on disk.`,
      );
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "delete failed");
    }
  };

  const stats = info?.stats ?? {};
  const statKeys = Object.keys(stats);

  return (
    <div className="admin-view">
      <div className="wrap">
        <h2>Admin</h2>
        {error && <div className="banner banner-error">✗ {error}</div>}
        {note && (
          <div className="banner banner-ok">
            <span>✓ {note}</span>
            <button className="btn btn-sm" onClick={() => setNote(null)}>
              Dismiss
            </button>
          </div>
        )}

        <div className="grid two">
          <section className="card">
            <h3>Users</h3>
            <div className="table-scroll">
              <table className="admin-users">
                <thead>
                  <tr>
                    <th>Username</th>
                    <th>Role</th>
                    <th>Created</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.username}>
                      <td className="mono">{u.username}</td>
                      <td>
                        <span className={u.role === "admin" ? "badge accent" : "badge"}>
                          {u.role}
                        </span>
                      </td>
                      <td className="muted admin-when">{readableDate(u.created_at)}</td>
                      <td>
                        <div className="admin-row-actions">
                          <button
                            className="btn btn-sm"
                            onClick={() => setResetting(resetting === u.username ? null : u.username)}
                          >
                            Reset password
                          </button>
                          {u.username !== self && (
                            <button
                              className="btn btn-sm danger"
                              onClick={() => void deleteUser(u.username)}
                            >
                              Delete
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {resetting && (
              <ResetPassword
                key={resetting}
                username={resetting}
                onDone={() => setResetting(null)}
              />
            )}

            <form className="admin-create" onSubmit={createUser}>
              <p className="label">New user</p>
              <div className="admin-create-row">
                <label className="field">
                  <span>Username</span>
                  <input
                    autoComplete="off"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Password</span>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Role</span>
                  <select
                    value={role}
                    onChange={(e) => setRole(e.target.value as "member" | "admin")}
                  >
                    <option value="member">member</option>
                    <option value="admin">admin</option>
                  </select>
                </label>
                <button
                  className="btn primary"
                  type="submit"
                  disabled={!username.trim() || !password}
                >
                  Create
                </button>
              </div>
            </form>
          </section>

          <section className="card">
            <h3>Brain</h3>
            {info ? (
              <>
                <div className="info-figures">
                  {statKeys.map((k) => (
                    <div className="info-fig" key={k}>
                      <span className="fig">{stats[k]}</span>
                      <p className="label">{STAT_LABELS[k] ?? k.replace(/_/g, " ")}</p>
                    </div>
                  ))}
                </div>

                <div className="info-rows">
                  <div className="info-row">
                    <p className="label">Index</p>
                    <p className="info-row-value">
                      {info.indexing
                        ? "Indexing now"
                        : info.indexed
                          ? "Indexed"
                          : "Nothing indexed yet"}
                    </p>
                  </div>
                  <div className="info-row">
                    <p className="label">Brain</p>
                    <p className="info-row-value mono">{info.brain}</p>
                  </div>
                  <div className="info-row">
                    <p className="label">Chat model</p>
                    <p className="info-row-value mono">{info.chat_model || "—"}</p>
                  </div>
                  <div className="info-row">
                    <p className="label">Chat endpoint</p>
                    <p className="info-row-value mono">{info.chat_endpoint || "—"}</p>
                  </div>
                  <div className="info-row">
                    <p className="label">Embedding model</p>
                    <p className="info-row-value mono">{info.embed_model || "—"}</p>
                  </div>
                  {info.model_error && (
                    <div className="info-row">
                      <p className="label">Last model error</p>
                      <p className="info-row-value run-fail">{info.model_error}</p>
                    </div>
                  )}
                  <div className="info-row">
                    <p className="label">Tools · {info.tools.length}</p>
                    <div className="info-pills">
                      {info.tools.map((t) => (
                        <span className="pill mono" key={t}>
                          {t}
                        </span>
                      ))}
                      {info.tools.length === 0 && <span className="muted">None loaded.</span>}
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <p className="muted">No info available.</p>
            )}
          </section>
        </div>

        <IdentitySection active={active} />
      </div>
    </div>
  );
}
