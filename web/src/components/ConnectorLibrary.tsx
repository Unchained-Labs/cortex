import { useState } from "react";
import { apiSend } from "../api";
import type { LibraryConnector } from "../types";

/**
 * Ready-made connectors.
 *
 * A built-in already ships and only needs configuring; a template writes
 * starter code into the brain that you then edit. Either way the point is
 * the same as the skill library: an empty section should offer something,
 * not an empty list and a blank editor.
 */
export default function ConnectorLibrary({
  connectors,
  onAdded,
}: {
  connectors: LibraryConnector[];
  onAdded: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (connectors.length === 0) return null;

  const add = async (connector: LibraryConnector) => {
    setBusy(connector.name);
    setError(null);
    try {
      await apiSend(
        "POST",
        `/api/extensions/library/connector/${encodeURIComponent(connector.name)}`,
      );
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : `could not add ${connector.name}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="lib">
      <p className="label">Ready-made connectors</p>
      <p className="ext-blurb">
        Adding one fills in example settings — open Settings on the row and put your own
        calendar link or feed URL in before it will fetch anything.
      </p>
      {error && <p className="ext-error">✗ {error}</p>}
      <div className="lib-grid">
        {connectors.map((connector) => (
          <div
            className={connector.installed ? "lib-card lib-have" : "lib-card"}
            key={connector.name}
          >
            <p className="mono lib-name">
              {connector.name}
              <span className="lib-kind"> · {connector.kind}</span>
            </p>
            <p className="lib-desc">{connector.description}</p>
            {connector.installed ? (
              <span className="lib-installed">Added</span>
            ) : (
              <button
                className="btn btn-sm"
                disabled={busy !== null}
                onClick={() => void add(connector)}
              >
                {busy === connector.name ? "Adding…" : "Add"}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
