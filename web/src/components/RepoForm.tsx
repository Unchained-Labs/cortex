import { useCallback, useEffect, useRef, useState } from "react";
import { apiSend } from "../api";
import { useModalKeys } from "../lib/modal";
import type { Repo, RepoList } from "../types";

/** One open repo form. `nonce` keys the component so every open starts clean. */
export interface RepoTarget {
  repo: Repo | null;
  nonce: number;
}

/** How often a clone is refreshed. 0 is manual — Sync now, or a review. */
const SYNC_CHOICES: { hours: number; label: string }[] = [
  { hours: 1, label: "hourly" },
  { hours: 6, label: "every 6 hours" },
  { hours: 12, label: "every 12 hours" },
  { hours: 24, label: "daily" },
  { hours: 0, label: "only when asked" },
];

/** `Unchained-Labs/cortex`, from anything the field accepts, for the preview. */
function slugOf(source: string): string {
  const s = source.trim().replace(/\.git$/, "");
  const m = /^(?:https?:\/\/[^/]+\/|git@[^:]+:)?(.+)$/.exec(s);
  return (m ? m[1] : s).replace(/^\/+|\/+$/g, "");
}

/** A pasted URL says where it is hosted; only the obvious cases are guessed. */
function providerOf(source: string): string | null {
  const m = /^(?:https?:\/\/([^/]+)\/|git@([^:]+):)/.exec(source.trim());
  const host = (m?.[1] ?? m?.[2] ?? "").toLowerCase();
  if (!host) return null;
  if (host === "github.com" || host.endsWith(".github.com")) return "github";
  if (host.includes("gitlab")) return "gitlab";
  return null;
}

function nameOf(source: string): string {
  const slug = slugOf(source);
  const last = slug.split("/").pop() ?? "";
  return last
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

export default function RepoForm({
  target,
  meta,
  onClose,
  onSaved,
}: {
  target: RepoTarget;
  meta: RepoList;
  onClose: () => void;
  onSaved: (repo: Repo, isNew: boolean) => void;
}) {
  const existing = target.repo;
  const isNew = existing === null;

  const [provider, setProvider] = useState(existing?.provider ?? meta.providers[0] ?? "github");
  const [source, setSource] = useState(existing?.slug ?? "");
  const [host, setHost] = useState(existing?.host ?? "");
  const [branch, setBranch] = useState(existing?.branch ?? "");
  const [name, setName] = useState(existing?.name ?? "");
  const [nameTouched, setNameTouched] = useState(!isNew);
  const [tokenEnv, setTokenEnv] = useState(existing?.token_env ?? "");
  const [tokenTouched, setTokenTouched] = useState(!isNew);
  const [hours, setHours] = useState(existing?.sync_hours ?? 6);
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const drawer = useRef<HTMLElement>(null);
  const first = useRef<HTMLInputElement>(null);

  const titleId = `repo-form-${target.nonce}`;
  const requestClose = useCallback(() => onClose(), [onClose]);
  useModalKeys(drawer, requestClose);

  useEffect(() => {
    first.current?.focus();
  }, [target.nonce]);

  const defaultToken = meta.token_envs[provider] ?? "";
  const effectiveName = nameTouched ? name : nameOf(source);
  const effectiveToken = tokenTouched ? tokenEnv : defaultToken;
  const urlHost = /^(?:https?:\/\/([^/]+)\/|git@([^:]+):)/.exec(source.trim());
  const defaultHost =
    urlHost?.[1] ?? urlHost?.[2] ?? (provider === "github" ? "github.com" : "gitlab.com");

  const save = async () => {
    if (!source.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await apiSend<Repo>("PUT", "/api/repos", {
        repo: {
          name: effectiveName,
          provider,
          slug: source.trim(),
          host: host.trim(),
          branch: branch.trim(),
          token_env: effectiveToken.trim(),
          sync_hours: hours,
          enabled,
        },
      });
      onSaved(saved, isNew);
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="drawer-scrim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) requestClose();
      }}
    >
      <section
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={drawer}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="editor-bar">
          <span className="mono editor-path" id={titleId}>
            repository · {isNew ? "new" : existing.name}
          </span>
          <div className="editor-actions">
            <button className="btn btn-sm" onClick={requestClose}>
              Close
            </button>
            <button
              className="btn btn-sm primary"
              onClick={() => void save()}
              disabled={!source.trim() || busy}
            >
              {busy ? "Saving…" : isNew ? "Add and sync" : "Save"}
            </button>
          </div>
        </div>

        {error && <p className="drawer-error">✗ {error}</p>}

        <div className="drawer-scroll">
          <p className="auto-sentence builder-sentence">
            {source.trim()
              ? `read ${slugOf(source)}${branch.trim() ? ` (${branch.trim()})` : ""} as code/${effectiveName || "…"}`
              : "which repository?"}
          </p>

          <div className="drawer-fields">
            <label className="field">
              <span>Hosted on</span>
              <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                {meta.providers.map((p) => (
                  <option key={p} value={p}>
                    {p === "github" ? "GitHub" : p === "gitlab" ? "GitLab" : p}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Repository</span>
              <input
                ref={first}
                className="mono"
                autoComplete="off"
                placeholder="owner/name, or its URL"
                value={source}
                disabled={!isNew}
                onChange={(e) => {
                  setSource(e.target.value);
                  const guessed = providerOf(e.target.value);
                  if (guessed && meta.providers.includes(guessed)) setProvider(guessed);
                }}
              />
            </label>
          </div>

          <div className="drawer-fields">
            <label className="field">
              <span>Branch</span>
              <input
                className="mono"
                autoComplete="off"
                placeholder="the default branch"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Name in the brain</span>
              <input
                className="mono"
                autoComplete="off"
                placeholder={nameOf(source) || "name"}
                value={effectiveName}
                disabled={!isNew}
                onChange={(e) => {
                  setNameTouched(true);
                  setName(e.target.value);
                }}
              />
            </label>
          </div>
          <p className="drawer-hint muted">
            Files are read as <span className="mono">code/{effectiveName || "name"}/…</span>{" "}
            by everyone on this brain and by the agent. Adding a repo is the decision to
            share it.
          </p>

          {provider !== "github" && (
            <div className="drawer-fields">
              <label className="field">
                <span>Host</span>
                <input
                  className="mono"
                  autoComplete="off"
                  placeholder={defaultHost}
                  value={host}
                  onChange={(e) => setHost(e.target.value)}
                />
              </label>
              <p className="drawer-hint muted">Leave blank for {defaultHost}; set it for a self-hosted GitLab.</p>
            </div>
          )}

          <div className="drawer-fields">
            <label className="field">
              <span>Token lives in</span>
              <input
                className="mono"
                autoComplete="off"
                placeholder={defaultToken}
                value={effectiveToken}
                onChange={(e) => {
                  setTokenTouched(true);
                  setTokenEnv(e.target.value.toUpperCase());
                }}
              />
            </label>
            <label className="field">
              <span>Refresh</span>
              <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
                {SYNC_CHOICES.every((c) => c.hours !== hours) && (
                  <option value={hours}>every {hours} hours</option>
                )}
                {SYNC_CHOICES.map((c) => (
                  <option key={c.hours} value={c.hours}>
                    {c.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="drawer-hint muted">
            The name of an environment variable, not the token. Put the token in{" "}
            <span className="mono">{meta.env_path}</span> as{" "}
            <span className="mono">{effectiveToken || defaultToken}=…</span> and it is read on
            the next sync; a public repository needs none.
          </p>

          <div className="builder-enable">
            <label className="toggle">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              <span>Enabled — switched off, it leaves the index and reviews of it stop</span>
            </label>
          </div>
        </div>
      </section>
    </div>
  );
}
