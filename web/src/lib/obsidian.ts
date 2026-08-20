/** Obsidian-flavored helpers: frontmatter, task toggling, path resolution. */

export interface Frontmatter {
  pairs: [string, string][];
  /** body without the frontmatter block */
  body: string;
  /** number of source lines the frontmatter block occupies (incl. fences) */
  lines: number;
}

/** Minimal YAML subset: `key: value` pairs and `- item` list continuations. */
export function splitFrontmatter(text: string): Frontmatter {
  if (!text.startsWith("---\n") && text !== "---") {
    return { pairs: [], body: text, lines: 0 };
  }
  const lines = text.split("\n");
  let end = -1;
  for (let i = 1; i < lines.length; i++) {
    if (/^---\s*$/.test(lines[i])) {
      end = i;
      break;
    }
  }
  if (end < 0) return { pairs: [], body: text, lines: 0 };

  const pairs: [string, string][] = [];
  for (let i = 1; i < end; i++) {
    const kv = /^([\w][\w .-]*):\s*(.*)$/.exec(lines[i]);
    if (kv) {
      pairs.push([kv[1], kv[2].replace(/^["']|["']$/g, "")]);
    } else {
      const item = /^\s*-\s+(.*)$/.exec(lines[i]);
      if (item && pairs.length > 0) {
        const last = pairs[pairs.length - 1];
        last[1] = last[1] ? `${last[1]}, ${item[1]}` : item[1];
      }
    }
  }
  return { pairs, body: lines.slice(end + 1).join("\n"), lines: end + 1 };
}

/**
 * Flip the n-th task checkbox (`- [ ]` / `- [x]`) in the source, counting the
 * same way the renderer does: frontmatter and fenced code blocks are skipped.
 * Returns the new text, or null if not found.
 */
export function toggleTask(text: string, index: number): string | null {
  const fm = splitFrontmatter(text);
  const lines = text.split("\n");
  let inFence = false;
  let count = 0;
  for (let i = fm.lines; i < lines.length; i++) {
    if (/^\s*(```|~~~)/.test(lines[i])) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    // also match tasks nested in blockquotes/callouts: "> - [ ] ..."
    const m = /^(\s*(?:>\s*)*(?:[-*+]|\d+[.)])\s+\[)([ xX])(\].*)$/.exec(lines[i]);
    if (!m) continue;
    if (count === index) {
      lines[i] = m[1] + (m[2] === " " ? "x" : " ") + m[3];
      return lines.join("\n");
    }
    count += 1;
  }
  return null;
}

const IMAGE_EXT = /\.(png|jpe?g|gif|svg|webp|bmp|avif)$/i;

export function isImagePath(path: string): boolean {
  return IMAGE_EXT.test(path);
}

/**
 * Resolve a wikilink/embed target against a vault file list, Obsidian-style:
 * exact path, suffix match, with `.md` appended when the target has no
 * extension. Case-insensitive fallback.
 */
export function resolveTarget(target: string, files: string[]): string | null {
  const clean = target.replace(/#.*$/, "").trim(); // drop heading anchors
  if (!clean) return null;
  const candidates = /\.\w+$/.test(clean) ? [clean] : [`${clean}.md`, clean];
  for (const cand of candidates) {
    for (const f of files) {
      if (f === cand || f.endsWith(`/${cand}`)) return f;
    }
    const lower = cand.toLowerCase();
    for (const f of files) {
      const fl = f.toLowerCase();
      if (fl === lower || fl.endsWith(`/${lower}`)) return f;
    }
  }
  return null;
}
