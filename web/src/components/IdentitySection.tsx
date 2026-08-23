import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import type { IdentityProposal, IdentityState } from "../types";
import { collapseUnchanged, countChanges, diffLines } from "../lib/diff";
import Editor from "./Editor";

/** `2026-08-21T16:04:46+00:00` → `21 Aug 2026, 16:04`. */
function readableWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const GLYPH: Record<string, string> = { add: "+", remove: "-", same: " " };

/**
 * What a proposal would change, as lines. Colour is never the only signal —
 * every line carries its `+`/`-` in the text itself, so this reads the same
 * to someone who cannot tell the two tints apart.
 */
function ProposedText({ current, proposed }: { current: string; proposed: string }) {
  const lines = diffLines(current, proposed);
  if (!lines) {
    // Too long to diff cheaply. The whole proposed file is never wrong,
    // only longer — better that than a table nobody can read.
    return (
      <pre className="id-text id-prop-text mono" aria-label="proposed identity text">
        {proposed}
      </pre>
    );
  }
  const { added, removed } = countChanges(lines);
  const rows = collapseUnchanged(lines);
  return (
    <>
      <p className="id-diff-summary muted">
        {added} line{added === 1 ? "" : "s"} added, {removed} removed
        {added + removed === 0 && " — this proposal changes nothing"}
      </p>
      <pre className="id-text id-diff mono" aria-label="proposed changes">
        {rows.map((row, i) =>
          "gap" in row ? (
            <span className="id-diff-gap" key={i}>
              ⋯ {row.gap} unchanged line{row.gap === 1 ? "" : "s"}
            </span>
          ) : (
            <span className={`id-diff-line id-diff-${row.kind}`} key={i}>
              {GLYPH[row.kind]} {row.text}
            </span>
          ),
        )}
      </pre>
    </>
  );
}

/** One pending proposal: why it was asked, what it would write, and a decision. */
function ProposalCard({
  proposal,
  current,
  onDecided,
}: {
  proposal: IdentityProposal;
  current: string;
  onDecided: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const decide = async (decision: "accept" | "discard") => {
    if (busy) return;
    if (
      decision === "accept" &&
      !window.confirm(
        "Accept this proposal?\n\nIt replaces the identity file with the proposed " +
          "text — whatever is there now is overwritten.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await apiSend("POST", `/api/identity/proposals/${proposal.id}/${decision}`);
      onDecided();
    } catch (e) {
      setError(e instanceof Error ? e.message : `could not ${decision} that`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="id-proposal">
      <div className="id-prop-head">
        <span className="badge warn id-pending">Pending — nothing changed yet</span>
        <span className="faint id-prop-when">{readableWhen(proposal.created_at)}</span>
      </div>
      <p className="id-prop-reason">{proposal.reason || "No reason given."}</p>
      <p className="muted id-prop-note">
        The agent is asking. The identity above is unchanged until you accept, and accepting
        replaces it with the text below.
      </p>

      <ProposedText current={current} proposed={proposal.text} />

      {error && <p className="form-error">✗ {error}</p>}

      <div className="id-prop-actions">
        <button className="btn btn-sm primary" onClick={() => void decide("accept")} disabled={busy}>
          {busy ? "Working…" : "Accept"}
        </button>
        <button className="btn btn-sm" onClick={() => void decide("discard")} disabled={busy}>
          Discard
        </button>
      </div>
    </article>
  );
}

/**
 * Identity, edited.
 *
 * It sits in Admin because Admin is where the brain's own settings live and
 * editing is admin-only. Two halves: the file itself, and the proposals the
 * agent has queued. The agent may propose and may not write — so the pending
 * half has to read as a question, never as something already done.
 */
export default function IdentitySection({ active }: { active: boolean }) {
  const [state, setState] = useState<IdentityState | null>(null);
  /** the server's text as last loaded — what "dirty" is measured against */
  const [base, setBase] = useState("");
  /** what CodeMirror was last seeded with; `docKey` re-seeds it */
  const [seed, setSeed] = useState("");
  const [text, setText] = useState("");
  /** bumped on every load that replaces the buffer, to re-seed CodeMirror */
  const [docKey, setDocKey] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    async (keepBuffer: boolean) => {
      try {
        const r = await apiGet<IdentityState>("/api/identity");
        setState(r);
        setLoadError(null);
        // Switching tabs must never eat an unsaved edit, so a re-read only
        // replaces the buffer when nothing is being written.
        if (!keepBuffer) {
          setBase(r.text ?? "");
          setSeed(r.text ?? "");
          setText(r.text ?? "");
          setDocKey((k) => k + 1);
        }
      } catch (e) {
        setLoadError(e instanceof Error ? e.message : "failed to load the identity");
      }
    },
    [],
  );

  const dirty = text !== base;

  useEffect(() => {
    if (active) void load(dirty);
    // `dirty` is read, not depended on: a keystroke must not re-fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, load]);

  const save = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await apiSend("PUT", "/api/identity", { text });
      setBase(text);
      setSaved(true);
      await load(true);
    } catch (e) {
      // 422 over max_chars carries the server's own sentence. The buffer
      // stays exactly as typed — something refused must never look saved.
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  };

  const max = state?.max_chars ?? 8000;
  const over = text.length > max;
  const pending = (state?.proposals ?? []).filter((p) => !p.status || p.status === "pending");

  return (
    <section className="card id-section">
      <div className="ext-section-head">
        <h3>Identity</h3>
        <span className={over ? "id-count id-count-over mono" : "id-count mono"}>
          {text.length.toLocaleString()} / {max.toLocaleString()} characters
        </span>
      </div>
      <p className="ext-blurb">
        Who this brain is for and how you like things done. It is read into every
        conversation, so keep it short and true — a page of stale detail is worse than three
        accurate lines, and every character costs you again on every single turn.
      </p>

      {loadError && (
        <div className="banner banner-error">
          <span>✗ {loadError}</span>
          <button className="btn btn-sm" onClick={() => void load(false)}>
            Retry
          </button>
        </div>
      )}

      {state?.untouched && (
        <p className="id-untouched">
          {state.text.trim() === "" ? (
            <>
              Nothing written yet, so <strong>nothing</strong> about you is reaching the
              model. Write a few true lines below and they start being used.
            </>
          ) : (
            <>
              This is still the starter text, so it is <strong>not</strong> being sent to the
              model. A placeholder in every conversation is worse than nothing. Replace it
              with something real and it starts being used.
            </>
          )}
        </p>
      )}

      {state?.persona && (
        <div className="id-persona">
          <p className="label">Persona, from cortex.yaml</p>
          <pre className="id-text mono">{state.persona}</pre>
          <p className="muted id-persona-note">
            Set in <span className="mono">cortex.yaml</span>, not here. It is prepended to
            the identity text below, so the model sees both. Edit the file to change it.
          </p>
        </div>
      )}

      <div className={dirty ? "id-editor id-editor-dirty" : "id-editor"}>
        <Editor
          docKey={`identity:${docKey}`}
          initialText={seed}
          onChange={(t) => {
            setText(t);
            setSaved(false);
          }}
          onSave={() => void save()}
          language="plain"
        />
      </div>

      {error && <p className="drawer-error id-error">✗ {error}</p>}
      {saved && !dirty && (
        <p className="tpl-said id-saved">
          {state?.untouched
            ? "✓ Saved — but it is still the starter text, so it is still not being used."
            : "✓ Saved. Every new conversation reads it."}
        </p>
      )}

      <div className="id-actions">
        <button className="btn btn-sm primary" onClick={() => void save()} disabled={busy} title="Ctrl+S">
          {busy ? "Saving…" : "Save identity"}
        </button>
        {dirty && (
          <button
            className="btn btn-sm"
            onClick={() => {
              setSeed(base);
              setText(base);
              setDocKey((k) => k + 1);
              setError(null);
            }}
          >
            Revert
          </button>
        )}
        {text.trim() === "" && state && (
          <button
            className="btn btn-sm"
            onClick={() => {
              // A starting shape, not a saved file: it is dirty until saved.
              setSeed(state.starter);
              setText(state.starter);
              setDocKey((k) => k + 1);
            }}
          >
            Start from the outline
          </button>
        )}
        {dirty && <span className="muted id-dirty">Unsaved changes.</span>}
        {over && (
          <span className="id-over">
            Over the limit — saving will be refused until it is shorter.
          </span>
        )}
      </div>

      <div className="id-proposals">
        <h4>
          Proposed changes
          {pending.length > 0 && <span className="badge accent id-prop-count">{pending.length}</span>}
        </h4>
        {pending.length === 0 ? (
          <p className="muted ext-empty">
            Nothing proposed. When you tell the agent something that should always be true, it
            can propose a change here — and it will wait for you.
          </p>
        ) : (
          pending.map((p) => (
            <ProposalCard
              key={p.id}
              proposal={p}
              current={state?.text ?? ""}
              onDecided={() => {
                setSaved(false);
                setError(null);
                void load(false);
              }}
            />
          ))
        )}
      </div>
    </section>
  );
}
