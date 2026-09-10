import { useEffect, useState } from "react";
import { apiGet } from "../api";
import type { GraphNeighbor, GraphNeighbors } from "../types";

/**
 * What the open file is connected to, from the brain's graph: the notes
 * that link here and that it links to, its tags, the code that imports or
 * uses it, and the files that changed in the same commits. Every row is a
 * reason a person can check, so every row is a link.
 */
export default function Connections({
  fileKey,
  onOpen,
}: {
  /** the index key of the open file, or null when it has none (a new, unsaved note) */
  fileKey: string | null;
  onOpen: (key: string) => void;
}) {
  const [data, setData] = useState<GraphNeighbors | null>(null);

  useEffect(() => {
    setData(null);
    if (!fileKey) return;
    let live = true;
    apiGet<GraphNeighbors>(`/api/graph/neighbors?path=${encodeURIComponent(fileKey)}`)
      .then((r) => {
        if (live) setData(r);
      })
      .catch(() => {
        if (live) setData(null);
      });
    return () => {
      live = false;
    };
  }, [fileKey]);

  if (!data || !data.indexed || data.groups.length === 0) return null;

  const render = (item: GraphNeighbor, i: number) => {
    if (item.kind === "file" || item.kind === "sym") {
      const label = item.kind === "sym" ? `${item.label} · ${item.path}` : item.path;
      return (
        <button
          key={`${item.kind}:${item.path}:${item.label}:${i}`}
          className="mono path-link conn-item"
          onClick={() => onOpen(item.path)}
          title={`Open ${item.path}`}
        >
          {label}
        </button>
      );
    }
    return (
      <span key={`${item.kind}:${item.label}:${i}`} className="mono conn-item conn-plain">
        {item.label}
      </span>
    );
  };

  return (
    <aside className="connections" aria-label="Connections">
      <p className="label">Connections</p>
      <dl className="conn-groups">
        {data.groups.map((g) => (
          <div className="conn-group" key={g.relation}>
            <dt className="muted">{g.relation}</dt>
            <dd>{g.items.map(render)}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}
