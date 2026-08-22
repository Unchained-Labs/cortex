import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiSend, setUnauthorizedHandler } from "./api";
import { wsConnect, wsDisconnect } from "./ws";
import type { Me } from "./types";
import SignIn from "./views/SignIn";
import Today from "./views/Today";
import Chat from "./views/Chat";
import Search from "./views/Search";
import Channels from "./views/Channels";
import Vault from "./views/Vault";
import ImportView from "./views/ImportView";
import Extend from "./views/Extend";
import Automation from "./views/Automation";
import Admin from "./views/Admin";
import CaptureBox from "./components/CaptureBox";
import HealthBanner from "./components/HealthBanner";
import AccountDialog from "./components/AccountDialog";

export type Tab =
  | "today"
  | "chat"
  | "search"
  | "channels"
  | "vault"
  | "import"
  | "extend"
  | "automation"
  | "admin";

export interface VaultTarget {
  /** the vault name, or "" for an index key outside `vaults/` */
  vault: string;
  /** the path inside the vault, or the whole key when `vault` is "" */
  path: string;
  /** the full index key, as the digest and search endpoints report it */
  key: string;
  /** forces re-navigation when the same file is cited twice */
  nonce: number;
}

const TABS: { id: Tab; label: string; adminOnly?: boolean }[] = [
  { id: "today", label: "Today" },
  { id: "chat", label: "Chat" },
  { id: "search", label: "Search" },
  { id: "channels", label: "Channels" },
  { id: "vault", label: "Vault" },
  { id: "import", label: "Import" },
  { id: "extend", label: "Extend", adminOnly: true },
  { id: "automation", label: "Automation", adminOnly: true },
  { id: "admin", label: "Admin", adminOnly: true },
];

/** Shortcuts must never fire while someone is writing. */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

export default function App() {
  const [user, setUser] = useState<Me | null | undefined>(undefined); // undefined = loading
  const [tab, setTab] = useState<Tab>("today");
  const [vaultTarget, setVaultTarget] = useState<VaultTarget | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [digestKey, setDigestKey] = useState(0); // bumped to re-read the digest
  const [searchFocus, setSearchFocus] = useState(0);
  const [shortcuts, setShortcuts] = useState(false);
  const [account, setAccount] = useState(false);
  const capturingRef = useRef(capturing);
  capturingRef.current = capturing;

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

  const openSearch = useCallback(() => {
    setTab("search");
    setSearchFocus(Date.now());
  }, []);

  // Global shortcuts: c = capture, / = search, ? = the hint. Never while
  // typing, never with a chord modifier held (shift is part of "?" and "/").
  useEffect(() => {
    if (!user) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (capturingRef.current || isTyping(e.target)) return;
      if (e.key === "c") {
        e.preventDefault();
        setCapturing(true);
      } else if (e.key === "/") {
        e.preventDefault();
        openSearch();
      } else if (e.key === "?") {
        e.preventDefault();
        setShortcuts((s) => !s);
      } else if (e.key === "Escape") {
        setShortcuts(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [user, openSearch]);

  /**
   * Any index key → the Vault view. `vaults/<name>/<path>` opens for editing;
   * anything else (a `sources/…` calendar event, say) opens read-only through
   * /api/file, which is why those items are clickable at all now.
   */
  const openVaultPath = useCallback((full: string) => {
    const m = /^vaults\/([^/]+)\/(.+)$/.exec(full);
    setVaultTarget(
      m
        ? { vault: m[1], path: m[2], key: full, nonce: Date.now() }
        : { vault: "", path: full, key: full, nonce: Date.now() },
    );
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
          <nav className="app-tabs" role="tablist" aria-label="Sections">
            {TABS.filter((t) => !t.adminOnly || isAdmin).map((t) => (
              <button
                key={t.id}
                role="tab"
                id={`tab-${t.id}`}
                aria-controls={`panel-${t.id}`}
                aria-selected={tab === t.id}
                className={tab === t.id ? "tab active" : "tab"}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>
          <div className="app-user">
            <button className="btn btn-sm capture-btn" onClick={() => setCapturing(true)}>
              Capture <kbd>c</kbd>
            </button>
            <button
              className="btn btn-sm shortcut-btn"
              title="Keyboard shortcuts"
              aria-label="Keyboard shortcuts"
              onClick={() => setShortcuts((s) => !s)}
            >
              ?
            </button>
            {/* your own name is the way into your own account */}
            <button
              className="user-button"
              title="Account settings"
              aria-label={`Account settings for ${user.username}`}
              aria-haspopup="dialog"
              onClick={() => setAccount(true)}
            >
              {user.username}
              {isAdmin && <span className="user-role"> · admin</span>}
            </button>
            <button className="btn btn-sm" onClick={signOut}>
              Sign out
            </button>
          </div>
        </div>
        {shortcuts && (
          <div className="shortcut-pop">
            <p className="label">Shortcuts</p>
            <p>
              <kbd>c</kbd> capture a line
            </p>
            <p>
              <kbd>/</kbd> search
            </p>
            <p>
              <kbd>?</kbd> this list · <kbd>Esc</kbd> close
            </p>
          </div>
        )}
      </header>

      <HealthBanner isAdmin={isAdmin} />

      {/* Views stay mounted so streams, sockets and drafts survive tab switches. */}
      <main className="app-main">
        <div
          className={tab === "today" ? "view" : "view hidden"}
          role="tabpanel"
          id="panel-today"
          aria-labelledby="tab-today"
        >
          <Today
            active={tab === "today"}
            refreshKey={digestKey}
            isAdmin={isAdmin}
            onVaultPath={openVaultPath}
            onCapture={() => setCapturing(true)}
            onImport={() => setTab("import")}
            onExtend={() => setTab("extend")}
          />
        </div>
        <div
          className={tab === "chat" ? "view" : "view hidden"}
          role="tabpanel"
          id="panel-chat"
          aria-labelledby="tab-chat"
        >
          <Chat onVaultPath={openVaultPath} />
        </div>
        <div
          className={tab === "search" ? "view" : "view hidden"}
          role="tabpanel"
          id="panel-search"
          aria-labelledby="tab-search"
        >
          <Search focusNonce={searchFocus} onVaultPath={openVaultPath} />
        </div>
        <div
          className={tab === "channels" ? "view" : "view hidden"}
          role="tabpanel"
          id="panel-channels"
          aria-labelledby="tab-channels"
        >
          <Channels username={user.username} active={tab === "channels"} />
        </div>
        <div
          className={tab === "vault" ? "view" : "view hidden"}
          role="tabpanel"
          id="panel-vault"
          aria-labelledby="tab-vault"
        >
          <Vault target={vaultTarget} />
        </div>
        <div
          className={tab === "import" ? "view" : "view hidden"}
          role="tabpanel"
          id="panel-import"
          aria-labelledby="tab-import"
        >
          <ImportView />
        </div>
        {isAdmin && (
          <div
          className={tab === "extend" ? "view" : "view hidden"}
          role="tabpanel"
          id="panel-extend"
          aria-labelledby="tab-extend"
        >
            <Extend active={tab === "extend"} />
          </div>
        )}
        {isAdmin && (
          <div
          className={tab === "automation" ? "view" : "view hidden"}
          role="tabpanel"
          id="panel-automation"
          aria-labelledby="tab-automation"
        >
            <Automation active={tab === "automation"} />
          </div>
        )}
        {isAdmin && (
          <div
          className={tab === "admin" ? "view" : "view hidden"}
          role="tabpanel"
          id="panel-admin"
          aria-labelledby="tab-admin"
        >
            <Admin self={user.username} active={tab === "admin"} />
          </div>
        )}
      </main>

      {capturing && (
        <CaptureBox
          onClose={() => setCapturing(false)}
          onCaptured={() => setDigestKey((k) => k + 1)}
        />
      )}

      {account && <AccountDialog user={user} onClose={() => setAccount(false)} />}
    </div>
  );
}
