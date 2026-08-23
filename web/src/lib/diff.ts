/**
 * A line-level diff, for showing what a proposal would actually change.
 *
 * Identity files are a page long, so the plain O(n·m) longest-common-
 * subsequence table is both fast enough and easy to read. Anything larger
 * than the guard below is not worth a table — the caller falls back to
 * showing the proposed text whole, which is never wrong, only longer.
 */

export type DiffKind = "same" | "add" | "remove";

export interface DiffLine {
  kind: DiffKind;
  text: string;
}

/** Above this many lines on either side, `diffLines` returns null. */
const MAX_LINES = 600;

function split(text: string): string[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  // A trailing newline is a file convention, not a blank line anybody edited.
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/**
 * `before` → `after` as a list of kept, added and removed lines, or null when
 * the texts are too long to diff cheaply.
 */
export function diffLines(before: string, after: string): DiffLine[] | null {
  const a = split(before);
  const b = split(after);
  if (a.length > MAX_LINES || b.length > MAX_LINES) return null;

  // lcs[i][j] = length of the longest common subsequence of a[i:] and b[j:]
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  );
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: "same", text: a[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ kind: "remove", text: a[i] });
      i++;
    } else {
      out.push({ kind: "add", text: b[j] });
      j++;
    }
  }
  for (; i < a.length; i++) out.push({ kind: "remove", text: a[i] });
  for (; j < b.length; j++) out.push({ kind: "add", text: b[j] });
  return out;
}

/** A run of unchanged lines that was folded away. */
export interface DiffGap {
  gap: number;
}

/**
 * Long runs of unchanged text between changes are noise. Keep `context` lines
 * either side of every change and fold the rest into a gap carrying how many
 * lines it swallowed, so nothing goes missing without saying so.
 */
export function collapseUnchanged(lines: DiffLine[], context = 2): (DiffLine | DiffGap)[] {
  const keep = new Array<boolean>(lines.length).fill(false);
  lines.forEach((line, idx) => {
    if (line.kind === "same") return;
    for (let k = Math.max(0, idx - context); k <= Math.min(lines.length - 1, idx + context); k++) {
      keep[k] = true;
    }
  });
  const out: (DiffLine | DiffGap)[] = [];
  let hidden = 0;
  lines.forEach((line, idx) => {
    if (keep[idx]) {
      if (hidden > 0) {
        out.push({ gap: hidden });
        hidden = 0;
      }
      out.push(line);
    } else {
      hidden++;
    }
  });
  if (hidden > 0) out.push({ gap: hidden });
  return out;
}

export function countChanges(lines: DiffLine[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const line of lines) {
    if (line.kind === "add") added++;
    else if (line.kind === "remove") removed++;
  }
  return { added, removed };
}
