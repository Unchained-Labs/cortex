import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import type { AdminUser } from "../types";

/** /api/info's exact shape is backend-defined; render it generically. */
type Info = Record<string, unknown>;

function InfoValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="faint">—</span>;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return <span className="mono">{String(value)}</span>;
  }
  return <pre className="info-json">{JSON.stringify(value, null, 2)}</pre>;
}

export default function Admin({ self, active }: { self: string; active: boolean }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [info, setInfo] = useState<Info | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"member" | "admin">("member");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    apiGet<{ users: AdminUser[] }>("/api/admin/users")
      .then((r) => setUsers(r.users))
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load users"));
    apiGet<Info>("/api/info")
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
    if (!window.confirm(`Delete user ${name}?`)) return;
    setError(null);
    try {
      await apiSend("DELETE", `/api/admin/users/${encodeURIComponent(name)}`);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "delete failed");
    }
  };

  return (
    <div className="admin-view">
      <div className="wrap">
        <h2>Admin</h2>
        {error && <div className="banner banner-error">✗ {error}</div>}

        <div className="grid two">
          <section className="card">
            <h3>Users</h3>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Username</th>
                    <th>Role</th>
                    <th>Created</th>
                    <th></th>
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
                      <td className="muted">{u.created_at}</td>
                      <td>
                        {u.username !== self && (
                          <button
                            className="btn btn-sm danger"
                            onClick={() => void deleteUser(u.username)}
                          >
                            Delete
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <form className="admin-create" onSubmit={createUser}>
              <p className="label">New user</p>
              <div className="admin-create-row">
                <input
                  placeholder="username"
                  autoComplete="off"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
                <input
                  type="password"
                  placeholder="password"
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value as "member" | "admin")}
                >
                  <option value="member">member</option>
                  <option value="admin">admin</option>
                </select>
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
              <div className="table-scroll">
                <table>
                  <tbody>
                    {Object.entries(info).map(([k, v]) => (
                      <tr key={k}>
                        <td className="fm-key mono">{k}</td>
                        <td>
                          <InfoValue value={v} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">No info available.</p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
