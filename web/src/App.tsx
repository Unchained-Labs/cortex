import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, setUnauthorizedHandler } from "./api";
import { wsConnect, wsDisconnect } from "./ws";
import type { Me } from "./types";
import SignIn from "./views/SignIn";
import Chat from "./views/Chat";
import Channels from "./views/Channels";
import Vault from "./views/Vault";
import ImportView from "./views/ImportView";
import Admin from "./views/Admin";

export type Tab = "chat" | "channels" | "vault" | "import" | "admin";

export interface VaultTarget {
  vault: string;
  path: string;
  /** forces re-navigation when the same file is cited twice */
  nonce: number;
}

const TABS: { id: Tab; label: string; adminOnly?: boolean }[] = [
  { id: "chat", label: "Chat" },
  { id: "channels", label: "Channels" },
  { id: "vault", label: "Vault" },
  { id: "import", label: "Import" },
  { id: "admin", label: "Admin", adminOnly: true },
];

export default function App() {
  const [user, setUser] = useState<Me | null | undefined>(undefined); // undefined = loading
  const [tab, setTab] = useState<Tab>("chat");
  const [vaultTarget, setVaultTarget] = useState<VaultTarget | null>(null);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      wsDisconnect();
      setUser(null);
    });
    apiGet<Me>("/api/me")
      .then(setUser)
      .catch(() => setUser(null));
  }, []);

  useEffect(() => {
    if (user) wsConnect();
    return () => wsDisconnect();
  }, [user]);

  /** `vaults/<name>/<path>` citation → open Vault view on that file. */
  const openVaultPath = useCallback((full: string) => {
    const m = /^vaults\/([^/]+)\/(.+)$/.exec(full);
    if (!m) return;
    setVaultTarget({ vault: m[1], path: m[2], nonce: Date.now() });
    setTab("vault");
  }, []);

  const signOut = async () => {
    try {
      await apiSend("POST", "/api/auth/logout");
    } catch {
      /* cookie may already be gone */
    }
    wsDisconnect();
    setUser(null);
  };

  if (user === undefined) {
    return <div className="app-loading mono">cortex…</div>;
  }
  if (user === null) {
    return <SignIn onSignedIn={(me) => setUser(me)} />;
  }

  const isAdmin = user.role === "admin";

  return (
    <div className="app">
      <header className="site-head">
        <div className="wrap app-head">
          <a className="brand" href="/" onClick={(e) => e.preventDefault()}>
            <img
              src="/lockup-horizontal.svg"
              alt="cortex"
              onError={(e) => {
                const img = e.currentTarget;
                if (!img.src.endsWith("/assets/lockup-horizontal.svg")) {
                  img.src = "/assets/lockup-horizontal.svg";
                }
              }}
            />
          </a>
          <nav className="app-tabs">
            {TABS.filter((t) => !t.adminOnly || isAdmin).map((t) => (
              <button
                key={t.id}
                className={tab === t.id ? "tab active" : "tab"}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>
          <div className="app-user">
            <span className="mono user-name">
              {user.username}
              {isAdmin && <span className="user-role"> · admin</span>}
            </span>
            <button className="btn btn-sm" onClick={signOut}>
              Sign out
            </button>
          </div>
        </div>
      </header>

      {/* Views stay mounted so streams, sockets and drafts survive tab switches. */}
      <main className="app-main">
        <div className={tab === "chat" ? "view" : "view hidden"}>
          <Chat onVaultPath={openVaultPath} />
        </div>
        <div className={tab === "channels" ? "view" : "view hidden"}>
          <Channels username={user.username} active={tab === "channels"} />
        </div>
        <div className={tab === "vault" ? "view" : "view hidden"}>
          <Vault target={vaultTarget} />
        </div>
        <div className={tab === "import" ? "view" : "view hidden"}>
          <ImportView />
        </div>
        {isAdmin && (
          <div className={tab === "admin" ? "view" : "view hidden"}>
            <Admin self={user.username} active={tab === "admin"} />
          </div>
        )}
      </main>
    </div>
  );
}
