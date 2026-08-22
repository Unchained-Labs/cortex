import { useCallback, useEffect, useRef, useState } from "react";
import { apiSend } from "../api";
import { useModalKeys } from "../lib/modal";
import { INTERVALS, JOB_KIND_LABELS, describeJob, everyLabel } from "../lib/automation";
import type { Job } from "../types";

/** One open job form. `nonce` keys the component so every open starts clean. */
export interface JobTarget {
  job: Job | null;
  nonce: number;
}

const text = (settings: Record<string, unknown>, key: string, fallback: string): string => {
  const value = settings[key];
  return value === undefined || value === null ? fallback : String(value);
};

export default function JobForm({
  target,
  kinds,
  connectors,
  vaults,
  onClose,
  onSaved,
}: {
  target: JobTarget;
  kinds: string[];
  connectors: string[];
  vaults: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const existing = target.job;
  const isNew = existing === null;
  const settings = existing?.settings ?? {};

  const [name, setName] = useState(existing?.name ?? "");
  const [kind, setKind] = useState(existing?.kind ?? kinds[0] ?? "rules");
  const [hours, setHours] = useState(existing?.interval_hours ?? 24);
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  // One piece of state per kind, so switching the dropdown to look at another
  // kind and back does not quietly lose what was typed.
  const [connector, setConnector] = useState(text(settings, "connector", connectors[0] ?? ""));
  const [vault, setVault] = useState(text(settings, "vault", vaults[0] ?? "shared"));
  const [channel, setChannel] = useState(text(settings, "channel", "general"));
  const [dryRun, setDryRun] = useState(Boolean(settings.dry_run));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const drawer = useRef<HTMLElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);

  const titleId = `job-form-${target.nonce}`;
  const requestClose = useCallback(() => onClose(), [onClose]);
  useModalKeys(drawer, requestClose);

  useEffect(() => {
    const el = nameInput.current;
    if (el && !el.disabled) el.focus();
    else drawer.current?.querySelector<HTMLElement>("select")?.focus();
  }, [target.nonce]);

  const composed = (): Record<string, unknown> => {
    if (kind === "connector") return { connector };
    if (kind === "rules") return { dry_run: dryRun };
    if (kind === "digest") return { vault };
    if (kind === "channel_digest") return { channel: channel.trim() || "general" };
    return {};
  };

  const save = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await apiSend("PUT", "/api/jobs", {
        job: {
          name: name.trim(),
          kind,
          interval_hours: hours,
          settings: composed(),
          enabled,
        },
      });
      onSaved();
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
            job · {isNew ? "new" : existing.name}
          </span>
          <div className="editor-actions">
            <button className="btn btn-sm" onClick={requestClose}>
              Close
            </button>
            <button
              className="btn btn-sm primary"
              onClick={() => void save()}
              disabled={!name.trim() || busy}
            >
              {busy ? "Saving…" : "Save job"}
            </button>
          </div>
        </div>

        {error && <p className="drawer-error">✗ {error}</p>}

        <div className="drawer-scroll">
          <p className="auto-sentence builder-sentence">
            {describeJob({ kind, interval_hours: hours, settings: composed() })}
          </p>

          <div className="drawer-fields">
            <label className="field">
              <span>Name</span>
              <input
                ref={nameInput}
                className="mono"
                autoComplete="off"
                placeholder="nightly tidy"
                value={name}
                disabled={!isNew}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="field">
              <span>What it does</span>
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                {kinds.map((k) => (
                  <option key={k} value={k}>
                    {JOB_KIND_LABELS[k] ?? k}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>How often</span>
              <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
                {INTERVALS.every((i) => i.hours !== hours) && (
                  <option value={hours}>{everyLabel(hours)}</option>
                )}
                {INTERVALS.map((i) => (
                  <option key={i.hours} value={i.hours}>
                    {i.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {kind === "connector" && (
            <div className="drawer-fields">
              <label className="field">
                <span>Which connector</span>
                {connectors.length === 0 ? (
                  <span className="muted auto-none">
                    No connectors are set up yet — add one in Extend first.
                  </span>
                ) : (
                  <select value={connector} onChange={(e) => setConnector(e.target.value)}>
                    {connectors.includes(connector) ? null : (
                      <option value={connector}>{connector}</option>
                    )}
                    {connectors.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                )}
              </label>
            </div>
          )}

          {kind === "rules" && (
            <div className="builder-enable">
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={dryRun}
                  onChange={(e) => setDryRun(e.target.checked)}
                />
                <span>Preview only (dry run) — count the changes, move nothing</span>
              </label>
            </div>
          )}

          {kind === "digest" && (
            <div className="drawer-fields">
              <label className="field">
                <span>Which vault</span>
                <select value={vault} onChange={(e) => setVault(e.target.value)}>
                  {vaults.includes(vault) ? null : <option value={vault}>{vault}</option>}
                  {vaults.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
              <p className="drawer-hint muted">Written to briefings/&lt;day&gt;.md.</p>
            </div>
          )}

          {kind === "channel_digest" && (
            <div className="drawer-fields">
              <label className="field">
                <span>Which channel</span>
                <input
                  className="mono"
                  autoComplete="off"
                  placeholder="general"
                  value={channel}
                  onChange={(e) => setChannel(e.target.value)}
                />
              </label>
              <p className="drawer-hint muted">
                An empty digest posts nothing, so a quiet day stays quiet.
              </p>
            </div>
          )}

          <div className="builder-enable">
            <label className="toggle">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              <span>Enabled — a switched-off job never runs on its own</span>
            </label>
          </div>

          <p className="drawer-hint muted">
            Jobs run on an interval, not a clock time. Run now works whatever the interval
            says.
          </p>
        </div>
      </section>
    </div>
  );
}
