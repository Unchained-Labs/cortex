import type { ReactNode } from "react";

/**
 * The "⋯" at the end of a row. Edit, pause and delete are things a person
 * does to a row a few times in its life; showing them as three buttons on
 * every row made the one action that matters (Sync now, Review now) one of
 * four identical-looking choices.
 *
 * A native <details> so it opens and closes without state, works with the
 * keyboard, and dismisses on the next click anywhere.
 */
export function Menu({ label, children }: { label: string; children: ReactNode }) {
  return (
    <details
      className="menu"
      onClick={(e) => {
        // any click inside the popover (an item) closes the menu
        const target = e.target as HTMLElement;
        if (target.closest(".menu-pop")) e.currentTarget.removeAttribute("open");
      }}
      onBlur={(e) => {
        const next = e.relatedTarget as Node | null;
        if (!next || !e.currentTarget.contains(next)) e.currentTarget.removeAttribute("open");
      }}
    >
      <summary className="btn btn-sm menu-btn" aria-label={label} title={label}>
        ⋯
      </summary>
      <div className="menu-pop" role="menu">
        {children}
      </div>
    </details>
  );
}

export function MenuItem({
  children,
  onClick,
  danger = false,
}: {
  children: ReactNode;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      className={danger ? "menu-item danger" : "menu-item"}
      role="menuitem"
      onClick={onClick}
    >
      {children}
    </button>
  );
}
