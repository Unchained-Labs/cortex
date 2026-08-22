import { useCallback, useEffect, useRef, useState } from "react";
import { apiSend } from "../api";
import { useModalKeys } from "../lib/modal";
import {
  ACTION_HINTS,
  ACTION_LABELS,
  MATCH_HINTS,
  MATCH_LABELS,
  describeRule,
} from "../lib/automation";
import type { Rule, RuleAction, RuleMatch } from "../types";

/** One open builder. `nonce` keys the component so every open starts clean. */
export interface RuleTarget {
  rule: Rule | null;
  nonce: number;
}

export default function RuleBuilder({
  target,
  vaults,
  matchKinds,
  actionKinds,
  onClose,
  onSaved,
}: {
  target: RuleTarget;
  vaults: string[];
  matchKinds: string[];
  actionKinds: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const existing = target.rule;
  const isNew = existing === null;

  const [name, setName] = useState(existing?.name ?? "");
  const [vault, setVault] = useState(existing?.vault ?? vaults[0] ?? "shared");
  const [matches, setMatches] = useState<RuleMatch[]>(
    existing?.matches?.length
      ? existing.matches.map((m) => ({ ...m }))
      : [{ kind: matchKinds[0] ?? "tag", value: "" }],
  );
  const [action, setAction] = useState<RuleAction>(
    existing?.action
      ? { ...existing.action }
      : { kind: actionKinds[0] ?? "move", value: "" },
  );
  // A rule someone sat down and built is meant to run; only the ready-made
  // suggestions arrive switched off. Preview and apply skip disabled rules,
  // so this is the difference between a rule that works and one that looks
  // saved and does nothing.
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const drawer = useRef<HTMLElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);

  const titleId = `rule-builder-${target.nonce}`;

  const requestClose = useCallback(() => onClose(), [onClose]);
  useModalKeys(drawer, requestClose);

  useEffect(() => {
    const el = nameInput.current;
    if (el && !el.disabled) el.focus();
    else drawer.current?.querySelector<HTMLElement>("select")?.focus();
  }, [target.nonce]);

  const setMatch = (index: number, patch: Partial<RuleMatch>) =>
    setMatches((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const save = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await apiSend("PUT", "/api/rules", {
        rule: {
          name: name.trim(),
          vault,
          matches: matches.map((m) => ({ kind: m.kind, value: m.value.trim() })),
          action: { kind: action.kind, value: action.value.trim() },
          enabled,
        },
      });
      onSaved();
    } catch (e) {
      // 422 carries a plain reason. The form stays open with every value
      // untouched — a rule that was rejected must never look saved.
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
            rule · {isNew ? "new" : existing.name}
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
              {busy ? "Saving…" : "Save rule"}
            </button>
          </div>
        </div>

        {error && <p className="drawer-error">✗ {error}</p>}

        <div className="drawer-scroll">
          {/* The rule as a sentence, rewritten on every keystroke, so what is
              being built is legible before it is saved. */}
          <p className="auto-sentence builder-sentence">{describeRule({ matches, action })}</p>

          <div className="drawer-fields">
            <label className="field">
              <span>Name</span>
              <input
                ref={nameInput}
                className="mono"
                autoComplete="off"
                placeholder="file recipes"
                value={name}
                disabled={!isNew}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Vault</span>
              <select value={vault} onChange={(e) => setVault(e.target.value)}>
                {vaults.includes(vault) ? null : <option value={vault}>{vault}</option>}
                {vaults.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <p className="label">When a note…</p>
          <p className="drawer-hint muted">
            Every condition has to hold. A rule with no conditions would match every note,
            so there is always at least one.
          </p>
          {matches.map((row, i) => (
            <div className="cond-row" key={i}>
              <select
                aria-label={`Condition ${i + 1} kind`}
                value={row.kind}
                onChange={(e) => setMatch(i, { kind: e.target.value })}
              >
                {matchKinds.map((k) => (
                  <option key={k} value={k}>
                    {MATCH_LABELS[k] ?? k}
                  </option>
                ))}
              </select>
              <input
                className="mono"
                autoComplete="off"
                aria-label={`Condition ${i + 1} value`}
                inputMode={row.kind === "older_than_days" ? "numeric" : "text"}
                placeholder={MATCH_HINTS[row.kind] ?? "value"}
                value={row.value}
                onChange={(e) => setMatch(i, { value: e.target.value })}
              />
              <button
                className="btn btn-sm danger"
                disabled={matches.length === 1}
                title={matches.length === 1 ? "a rule needs at least one condition" : "Remove"}
                onClick={() => setMatches((rows) => rows.filter((_, j) => j !== i))}
              >
                Remove
              </button>
            </div>
          ))}
          <button
            className="btn btn-sm"
            onClick={() =>
              setMatches((rows) => [...rows, { kind: matchKinds[0] ?? "tag", value: "" }])
            }
          >
            + Add condition
          </button>

          <p className="label builder-then">…then</p>
          <div className="cond-row">
            <select
              aria-label="Action"
              value={action.kind}
              onChange={(e) => setAction((a) => ({ ...a, kind: e.target.value }))}
            >
              {actionKinds.map((k) => (
                <option key={k} value={k}>
                  {ACTION_LABELS[k] ?? k}
                </option>
              ))}
            </select>
            <input
              className="mono"
              autoComplete="off"
              aria-label="Action value"
              placeholder={ACTION_HINTS[action.kind] ?? "value"}
              value={action.value}
              onChange={(e) => setAction((a) => ({ ...a, value: e.target.value }))}
            />
          </div>
          <div className="builder-enable">
            <label className="toggle">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              <span>Enabled — a switched-off rule is skipped by preview and by apply</span>
            </label>
          </div>

          <p className="drawer-hint muted">
            A rule can move, tag or archive. There is no delete action, and preview always
            shows what a run would do before anything moves.
          </p>
        </div>
      </section>
    </div>
  );
}
