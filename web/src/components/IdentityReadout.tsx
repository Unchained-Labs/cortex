import { useEffect, useState } from "react";
import { apiGet } from "../api";
import type { IdentityState } from "../types";

/**
 * The identity file, read-only, for people who are not admins.
 *
 * Any signed-in user may read it, but members never see the Admin tab where
 * it is edited. It sits on Memory because it is the adjacent idea — what the
 * brain believes about us, next to what it knows — and it stays collapsed so
 * it costs a line until someone wants it.
 */
export default function IdentityReadout({ active }: { active: boolean }) {
  const [state, setState] = useState<IdentityState | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!active || state) return;
    apiGet<IdentityState>("/api/identity")
      .then(setState)
      .catch(() => setState(null));
  }, [active, state]);

  if (!state) return null;

  const persona = state.persona.trim();
  const body = state.untouched ? "" : state.text.trim();

  return (
    <details className="id-readout" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>
        What this brain knows about us
        <span className="muted id-readout-hint">
          {" "}
          · read into every conversation{state.untouched ? " — not set yet" : ""}
        </span>
      </summary>
      <div className="id-readout-body">
        {persona && <pre className="id-text mono">{persona}</pre>}
        {body ? (
          <pre className="id-text mono">{body}</pre>
        ) : (
          !persona && (
            <p className="muted">
              Nobody has written it yet, so nothing about us is being sent to the model. An
              admin sets it in Admin → Identity.
            </p>
          )
        )}
        <p className="muted id-readout-note">Admins edit this in Admin → Identity.</p>
      </div>
    </details>
  );
}
