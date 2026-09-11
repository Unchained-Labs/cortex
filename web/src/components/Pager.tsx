/**
 * One page of a long list, and the controls to move between pages.
 *
 * Written as a shared component rather than per view because the failure it
 * fixes is not a Memory problem — it is what every unbounded list here does.
 * Memory was the visible one: 200 rows in a 21,000px column inside an 839px
 * box, which is an endless scroll with no sense of how far in you are and no
 * way to reach the end. The other lists differ only in how much data happens to
 * be in them today.
 *
 * Two decisions.
 *
 * IT ALWAYS SAYS THE TOTAL. "Next" without "of 8" is a scrollbar with extra
 * clicks: you cannot tell whether you are near the end, and you cannot tell
 * whether the thing you are looking for is even in the set. The total is the
 * part that makes a pager better than scrolling, not the buttons.
 *
 * IT RENDERS NOTHING FOR A SINGLE PAGE. A pager under a list of four items is
 * furniture — it occupies the space where the list should end and implies there
 * is more when there is not.
 */
export function Pager({
  total,
  limit,
  offset,
  onOffset,
  noun = "item",
  plural,
  busy = false,
}: {
  total: number;
  limit: number;
  offset: number;
  onOffset: (next: number) => void;
  /** Singular form. */
  noun?: string;
  /** Plural form. Required for any noun where adding "s" is wrong — "memory"
   *  became "memorys" the first time this shipped, which is the kind of detail
   *  that makes a careful page look careless. */
  plural?: string;
  busy?: boolean;
}) {
  const pages = Math.max(1, Math.ceil(total / Math.max(1, limit)));
  const page = Math.floor(offset / Math.max(1, limit)) + 1;
  if (total <= limit) return null;

  const first = offset + 1;
  const last = Math.min(offset + limit, total);

  return (
    <nav className="pager" aria-label={`${noun} pages`}>
      <button
        className="btn btn-sm"
        disabled={busy || offset <= 0}
        onClick={() => onOffset(Math.max(0, offset - limit))}
      >
        ← Previous
      </button>
      <span className="pager-at mono">
        {first}–{last} of {total} {total === 1 ? noun : (plural ?? `${noun}s`)}{" "}
        · page {page} of {pages}
      </span>
      <button
        className="btn btn-sm"
        disabled={busy || last >= total}
        onClick={() => onOffset(offset + limit)}
      >
        Next →
      </button>
    </nav>
  );
}
