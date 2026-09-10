import { useCallback, useEffect, useRef, useState } from "react";
import { apiSend } from "../api";
import { useModalKeys } from "../lib/modal";
import { INTERVALS, describeJob, everyLabel } from "../lib/automation";
import type { Job } from "../types";

/** One open review form. `nonce` keys the component so every open starts clean. */
export interface ReviewTarget {
  job: Job | null;
  nonce: number;
}

const text = (settings: Record<string, unknown>, key: string, fallback: string): string => {
  const value = settings[key];
  return value === undefined || value === null ? fallback : String(value);
};

/**
 * Scheduling a review is making a job of kind `code_review`: the same
 * clock as Automation, with the settings a review needs — which repo, how
 * far to look, what to look for, and where to say it happened.
 */
export default function ReviewForm({
  target,
  repos,
  modes,
  defaultFocus,
  onClose,
  onSaved,
}: {
  target: ReviewTarget;
  repos: string[];
  modes: string[];
  defaultFocus: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const existing = target.job;
  const isNew = existing === null;
  const settings = existing?.settings ?? {};

  const [repo, setRepo] = useState(text(settings, "repo", repos[0] ?? ""));
  const [name, setName] = useState(existing?.name ?? "");
  const [nameTouched, setNameTouched] = useState(!isNew);
  const [hours, setHours] = useState(existing?.interval_hours ?? 24);
  const [mode, setMode] = useState(text(settings, "mode", modes[0] ?? "changes"));
  const [focus, setFocus] = useState(text(settings, "focus", defaultFocus));
  const [channel, setChannel] = useState(text(settings, "channel", ""));
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const drawer = useRef<HTMLElement>(null);
  const first = useRef<HTMLSelectElement>(null);

  const titleId = `review-form-${target.nonce}`;
  const requestClose = useCallback(() => onClose(), [onClose]);
  useModalKeys(drawer, requestClose);

  useEffect(() => {
    first.current?.focus();
  }, [target.nonce]);

  const effectiveName = nameTouched ? name : repo ? `review ${repo}` : "";
  const composed = () => ({
    repo,
    mode,
    focus: focus.trim(),
    channel: channel.trim().replace(/^#/, ""),
  });

  const save = async () => {
    if (!effectiveName.trim() || !repo || busy) return;
    setBusy(true);
    setError(null);
    try {
      await apiSend("PUT", "/api/jobs", {
        job: {
          name: effectiveName.trim(),
          kind: "code_review",
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
            review · {isNew ? "new" : existing.name}
          </span>
          <div className="editor-actions">
            <button className="btn btn-sm" onClick={requestClose}>
              Close
            </button>
            <button
              className="btn btn-sm primary"
              onClick={() => void save()}
              disabled={!effectiveName.trim() || !repo || busy}
            >
              {busy ? "Saving…" : "Save review"}
            </button>
          </div>
        </div>

        {error && <p className="drawer-error">✗ {error}</p>}

        <div className="drawer-scroll">
          <p className="auto-sentence builder-sentence">
            {describeJob({ kind: "code_review", interval_hours: hours, settings: composed() })}
          </p>

          <div className="drawer-fields">
            <label className="field">
              <span>Repository</span>
              <select ref={first} value={repo} onChange={(e) => setRepo(e.target.value)}>
                {repos.includes(repo) ? null : <option value={repo}>{repo || "—"}</option>}
                {repos.map((r) => (
                  <option key={r} value={r}>
                    {r}
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

          <div className="drawer-fields">
            <label className="field">
              <span>What to review</span>
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="changes">new commits since the last review</option>
                <option value="full">the whole repository, every time</option>
              </select>
            </label>
            <label className="field">
              <span>Name</span>
              <input
                className="mono"
                autoComplete="off"
                placeholder={repo ? `review ${repo}` : "nightly review"}
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
            The first run of a “new commits” review looks at the whole repository once, then
            only at what changed. A run with nothing new writes nothing.
          </p>

          <div className="drawer-fields">
            <label className="field">
              <span>Look for</span>
              <textarea
                rows={3}
                value={focus}
                placeholder={defaultFocus}
                onChange={(e) => setFocus(e.target.value)}
              />
            </label>
          </div>
          <p className="drawer-hint muted">
            In your own words. The reviewer reads the brain first — conventions, decisions,
            earlier reviews — and cites a file and line for every finding.
          </p>

          <div className="drawer-fields">
            <label className="field">
              <span>Say so in a channel</span>
              <input
                className="mono"
                autoComplete="off"
                placeholder="none"
                value={channel}
                onChange={(e) => setChannel(e.target.value)}
              />
            </label>
          </div>
          <p className="drawer-hint muted">
            Optional. One line naming the note and how many findings wait, only when a review
            was written.
          </p>

          <div className="builder-enable">
            <label className="toggle">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              <span>Enabled — a switched-off review never runs on its own</span>
            </label>
          </div>
        </div>
      </section>
    </div>
  );
}
