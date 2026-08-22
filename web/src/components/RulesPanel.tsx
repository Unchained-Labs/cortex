import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import { describeRule, ruleSentence, runWhen } from "../lib/automation";
import type {
  PlannedAction,
  Rule,
  RuleApply,
  RuleList,
  RulePreview,
  RuleRun,
  VaultMeta,
} from "../types";
import RuleBuilder, { type RuleTarget } from "./RuleBuilder";

const EMPTY: RuleList = { rules: [], suggested: [], match_kinds: [], action_kinds: [] };

/** Preview rows read as "everything this rule would touch", not one flat list. */
function byRule(planned: PlannedAction[]): { rule: string; rows: PlannedAction[] }[] {
  const groups: { rule: string; rows: PlannedAction[] }[] = [];
  for (const row of planned) {
    const found = groups.find((g) => g.rule === row.rule);
    if (found) found.rows.push(row);
    else groups.push({ rule: row.rule, rows: [row] });
  }
  return groups;
}

function RuleRow({
  rule,
  onToggle,
  onEdit,
  onDelete,
}: {
  rule: Rule;
  onToggle: (rule: Rule, enabled: boolean) => void;
  onEdit: (rule: Rule) => void;
  onDelete: (rule: Rule) => void;
}) {
  // A rule whose stored spec no longer parses comes back as a name and a
  // reason. It cannot be toggled or edited into shape — say so, offer delete.
  if (rule.error) {
    return (
      <div className="auto-row">
        <div className="auto-row-head">
          <p className="auto-sentence auto-broken">{rule.name} will not load</p>
          <div className="auto-row-actions">
            <button className="btn btn-sm danger" onClick={() => onDelete(rule)}>
              Delete
            </button>
          </div>
        </div>
        <p className="ext-error">✗ {rule.error}</p>
      </div>
    );
  }

  return (
    <div className="auto-row">
      <div className="auto-row-head">
        <p className={rule.enabled ? "auto-sentence" : "auto-sentence auto-off"}>
          {ruleSentence(rule)}
        </p>
        <div className="auto-row-actions">
          <label className="toggle">
            <input
              type="checkbox"
              checked={rule.enabled ?? false}
              onChange={(e) => onToggle(rule, e.target.checked)}
            />
            <span>Enabled</span>
          </label>
          <button className="btn btn-sm" onClick={() => onEdit(rule)}>
            Edit
          </button>
          <button className="btn btn-sm danger" onClick={() => onDelete(rule)}>
            Delete
          </button>
        </div>
      </div>
      <p className="auto-row-meta">
        <span className="mono">{rule.name}</span>
        <span className="muted"> · in {rule.vault ?? "shared"}</span>
        {!rule.enabled && <span className="muted"> · switched off</span>}
      </p>
    </div>
  );
}

export default function RulesPanel({ active }: { active: boolean }) {
  const [list, setList] = useState<RuleList>(EMPTY);
  const [history, setHistory] = useState<RuleRun[]>([]);
  const [vaults, setVaults] = useState<string[]>([]);
  const [preview, setPreview] = useState<RulePreview | null>(null);
  const [applied, setApplied] = useState<RuleApply | null>(null);
  const [busy, setBusy] = useState<"preview" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<RuleTarget | null>(null);

  const load = useCallback(() => {
    apiGet<RuleList>("/api/rules")
      .then((r) => setList({ ...EMPTY, ...r }))
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load rules"));
    apiGet<{ history: RuleRun[] }>("/api/rules/history")
      .then((r) => setHistory(r.history))
      .catch(() => setHistory([]));
  }, []);

  useEffect(() => {
    if (!active) return;
    load();
    apiGet<{ vaults: VaultMeta[] }>("/api/vaults")
      .then((r) => setVaults(r.vaults.map((v) => v.name)))
      .catch(() => setVaults(["shared"]));
  }, [active, load]);

  const runPreview = async () => {
    setBusy("preview");
    setError(null);
    setApplied(null);
    try {
      setPreview(await apiGet<RulePreview>("/api/rules/preview"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "preview failed");
    } finally {
      setBusy(null);
    }
  };

  /**
   * Apply is deliberately second. It confirms with the number preview found
   * when there is one, because "apply 4 changes" is a decision and "apply"
   * on its own is a guess.
   */
  const runApply = async () => {
    const counted =
      preview === null
        ? "Apply the enabled rules now?"
        : `Apply the enabled rules now? Preview found ${preview.count} ${
            preview.count === 1 ? "change" : "changes"
          }.`;
    if (!window.confirm(`${counted}\n\nNotes are moved and tagged, never deleted, and every change is logged below.`)) {
      return;
    }
    setBusy("apply");
    setError(null);
    try {
      const result = await apiSend<RuleApply>("POST", "/api/rules/apply");
      setApplied(result);
      setPreview(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "apply failed");
    } finally {
      setBusy(null);
    }
  };

  const save = async (rule: Rule) => {
    setError(null);
    try {
      await apiSend("PUT", "/api/rules", { rule });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not save that rule");
    }
  };

  const toggle = (rule: Rule, enabled: boolean) => {
    setPreview(null);
    void save({ ...rule, enabled });
  };

  const remove = async (rule: Rule) => {
    if (!window.confirm(`Delete the rule "${rule.name}"? Notes it already filed stay where they are.`)) {
      return;
    }
    setError(null);
    try {
      await apiSend("DELETE", `/api/rules/${encodeURIComponent(rule.name)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "delete failed");
    }
    setPreview(null);
    load();
  };

  const known = new Set(list.rules.map((r) => r.name));
  const offers = list.suggested.filter((s) => !known.has(s.name));
  const groups = preview ? byRule(preview.planned) : [];
  const failures = applied ? applied.actions.filter((a) => a.action === "error") : [];

  return (
    <section className="card auto-panel">
      <div className="auto-panel-head">
        <h3>Rules</h3>
        <button
          className="btn btn-sm"
          onClick={() => setTarget({ rule: null, nonce: Date.now() })}
        >
          + New rule
        </button>
      </div>
      <p className="auto-blurb">
        Rules tidy the vault while you are not looking: they match notes, then move, tag or
        archive them. Nothing is ever deleted, and preview shows every change before it
        happens.
      </p>

      {error && (
        <div className="banner banner-error">
          <span>✗ {error}</span>
          <button className="btn btn-sm" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {list.rules.length === 0 ? (
        <p className="muted auto-none">
          No rules yet. Add one of the ready-made ones below, or build your own.
        </p>
      ) : (
        <div className="auto-rows">
          {list.rules.map((rule) => (
            <RuleRow
              key={rule.name}
              rule={rule}
              onToggle={toggle}
              onEdit={(r) => setTarget({ rule: r, nonce: Date.now() })}
              onDelete={(r) => void remove(r)}
            />
          ))}
        </div>
      )}

      {offers.length > 0 && (
        <div className="auto-suggested">
          <p className="label">Ready-made rules</p>
          <p className="auto-blurb">
            Added switched off, so nothing moves until you have read it and turned it on.
          </p>
          <div className="auto-chips">
            {offers.map((s) => (
              <button
                key={s.name}
                className="auto-chip"
                onClick={() => void save({ ...s, enabled: false })}
                title={`Add the rule "${s.name}"`}
              >
                <span className="auto-chip-add">Add</span>
                <span className="auto-chip-text">{describeRule(s)}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="auto-run">
        <button
          className="btn primary"
          onClick={() => void runPreview()}
          disabled={busy !== null}
        >
          {busy === "preview" ? "Checking…" : "Preview changes"}
        </button>
        <button className="btn" onClick={() => void runApply()} disabled={busy !== null}>
          {busy === "apply" ? "Applying…" : "Apply"}
        </button>
        <span className="muted auto-run-hint">
          Preview reads the vault and changes nothing.
        </span>
      </div>

      {preview && (
        <div className="auto-result">
          <p className="label">
            {preview.count === 0
              ? "Preview — nothing to do"
              : `Preview — ${preview.count} ${preview.count === 1 ? "change" : "changes"}`}
          </p>
          {preview.count === 0 ? (
            <p className="muted auto-none">
              No note in an enabled rule's vault matches it right now.
            </p>
          ) : (
            groups.map((group) => (
              <div className="auto-group" key={group.rule}>
                <p className="auto-group-head">
                  <span className="mono">{group.rule}</span>
                  <span className="muted">
                    {" "}
                    · {group.rows.length} {group.rows.length === 1 ? "note" : "notes"}
                  </span>
                </p>
                <div className="table-scroll">
                  <table className="auto-table">
                    <thead>
                      <tr>
                        <th>Note</th>
                        <th>Becomes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {group.rows.map((row, i) => (
                        <tr key={`${row.path}:${i}`}>
                          <td className="mono auto-path">{row.path}</td>
                          <td className="mono auto-path">
                            {row.action === "tag" ? `#${row.target}` : row.target}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {applied && (
        <div className="auto-result">
          <p className="label">Applied</p>
          <p className={failures.length > 0 ? "run-fail" : "run-ok"}>
            {applied.count === 0
              ? "Nothing matched — no note was touched."
              : `${applied.count - failures.length} filed${
                  failures.length > 0 ? `, ${failures.length} failed` : ""
                }.`}
          </p>
          {applied.actions.map((a, i) => (
            <p className="auto-applied-row" key={`${a.path}:${i}`}>
              {a.action === "error" ? (
                <span className="run-fail">
                  ✗ <span className="mono">{a.path}</span> — {a.target}
                </span>
              ) : (
                <span>
                  <span className="mono">{a.path}</span>
                  <span className="auto-arrow"> → </span>
                  <span className="mono">{a.action === "tag" ? `#${a.target}` : a.target}</span>
                </span>
              )}
            </p>
          ))}
        </div>
      )}

      <div className="auto-history">
        <p className="label">Where your notes went</p>
        <p className="auto-blurb">
          Every change a rule has ever made, newest first — the answer to "where did my note
          go".
        </p>
        {history.length === 0 ? (
          <p className="muted auto-none">No rule has moved anything yet.</p>
        ) : (
          <ul className="auto-history-list">
            {history.map((h, i) => (
              <li key={`${h.at}:${h.path}:${i}`}>
                <span className="auto-when">{runWhen(h.at)}</span>
                <span className="auto-hist-rule mono">{h.rule}</span>
                <span className="auto-hist-move">
                  <span className="mono">{h.path}</span>
                  <span className="auto-arrow"> → </span>
                  <span className="mono">{h.action === "tag" ? `#${h.target}` : h.target}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {target && (
        <RuleBuilder
          key={target.nonce}
          target={target}
          vaults={vaults}
          matchKinds={list.match_kinds}
          actionKinds={list.action_kinds}
          onClose={() => setTarget(null)}
          onSaved={() => {
            setTarget(null);
            setPreview(null);
            load();
          }}
        />
      )}
    </section>
  );
}
