import { Marked } from "marked";
import type { Tokens, TokenizerAndRendererExtension } from "marked";

/**
 * Obsidian-flavored markdown → HTML (marked v12, string renderers).
 *
 * Interactive elements carry data attributes; the Markdown component wires
 * clicks via delegation:
 *   a[data-wikilink]        — wikilink target (unresolved name)
 *   input[data-task]        — task checkbox, index = position among tasks
 *   .md-embed img           — embedded image (resolved through resolveEmbed)
 */

export interface RenderOptions {
  /** resolve an ![[embed]] target to an image URL; null → unresolvable */
  resolveEmbed?: (target: string) => string | null;
  /** render task checkboxes enabled (clickable) */
  interactiveTasks?: boolean;
}

function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

interface CalloutSpec {
  cls: string;
  glyph: string;
  label: string;
}

const CALLOUTS: Record<string, CalloutSpec> = {
  note: { cls: "note", glyph: "ⓘ", label: "Note" },
  info: { cls: "note", glyph: "ⓘ", label: "Info" },
  tip: { cls: "tip", glyph: "✦", label: "Tip" },
  warning: { cls: "warn", glyph: "⚠", label: "Warning" },
  important: { cls: "down", glyph: "‼", label: "Important" },
};

interface WikiToken extends Tokens.Generic {
  type: "wikilink";
  embed: boolean;
  target: string;
  alias: string;
}

interface TagToken extends Tokens.Generic {
  type: "obstag";
  tag: string;
}

export function renderMarkdown(text: string, opts: RenderOptions = {}): string {
  const wikilink: TokenizerAndRendererExtension = {
    name: "wikilink",
    level: "inline",
    start(src: string) {
      const m = /!?\[\[/.exec(src);
      return m ? m.index : undefined;
    },
    tokenizer(src: string) {
      const m = /^(!?)\[\[([^[\]|\n]+)(?:\|([^[\]\n]+))?\]\]/.exec(src);
      if (!m) return undefined;
      const tok: WikiToken = {
        type: "wikilink",
        raw: m[0],
        embed: m[1] === "!",
        target: m[2].trim(),
        alias: (m[3] ?? m[2]).trim(),
      };
      return tok;
    },
    renderer(token) {
      const tok = token as WikiToken;
      if (tok.embed) {
        const url = opts.resolveEmbed?.(tok.target) ?? null;
        if (url) {
          return `<span class="md-embed"><img src="${esc(url)}" alt="${esc(tok.target)}" loading="lazy" /></span>`;
        }
        return `<span class="md-embed-missing" title="unresolved embed">![[${esc(tok.target)}]]</span>`;
      }
      return `<a class="wikilink" data-wikilink="${esc(tok.target)}" href="#">${esc(tok.alias)}</a>`;
    },
  };

  const obstag: TokenizerAndRendererExtension = {
    name: "obstag",
    level: "inline",
    start(src: string) {
      const m = /(^|\s)#[A-Za-z_]/.exec(src);
      return m ? m.index + m[1].length : undefined;
    },
    tokenizer(src: string) {
      const m = /^#([A-Za-z_][\w/-]*)/.exec(src);
      if (!m) return undefined;
      const tok: TagToken = { type: "obstag", raw: m[0], tag: m[1] };
      return tok;
    },
    renderer(token) {
      const tok = token as TagToken;
      return `<span class="md-tag">#${esc(tok.tag)}</span>`;
    },
  };

  let taskIndex = 0;
  const md = new Marked({
    gfm: true,
    breaks: true,
    extensions: [wikilink, obstag],
    renderer: {
      // Neutralize raw HTML: channel/vault content is peer-authored.
      html(html: string) {
        return esc(html);
      },
      checkbox(checked: boolean) {
        const idx = taskIndex++;
        return (
          `<input type="checkbox" data-task="${idx}"` +
          `${checked ? " checked" : ""}${opts.interactiveTasks ? "" : " disabled"} />`
        );
      },
      link(href: string, title: string | null | undefined, text: string) {
        const t = title ? ` title="${esc(title)}"` : "";
        const ext = /^https?:\/\//i.test(href) ? ` target="_blank" rel="noreferrer noopener"` : "";
        return `<a href="${esc(href)}"${t}${ext}>${text}</a>`;
      },
      blockquote(quote: string) {
        // Obsidian callout: blockquote whose first paragraph starts [!type]
        const m = /^<p>\[!(\w+)\][ \t]*([^\n<]*)\s*(?:<br\s*\/?>)?\s*/i.exec(quote);
        const spec = m ? CALLOUTS[m[1].toLowerCase()] : undefined;
        if (m && spec) {
          const title = m[2].trim() || spec.label;
          let rest = quote.slice(m[0].length).replace(/^\s*/, "");
          if (rest.startsWith("</p>")) rest = rest.replace(/^<\/p>\s*/, "");
          else if (!rest.startsWith("<")) rest = `<p>${rest}`;
          return (
            `<div class="callout callout-${spec.cls}">` +
            `<div class="callout-title"><span class="callout-glyph">${spec.glyph}</span>${esc(title)}</div>` +
            `<div class="callout-body">${rest}</div></div>`
          );
        }
        return `<blockquote>${quote}</blockquote>`;
      },
    },
  });

  return md.parse(text, { async: false }) as string;
}

/** Matches vault-relative file paths mentioned in agent answers. */
export const VAULT_PATH_RE = /vaults\/[\w.-]+\/[^\s"'`)\]>,;]*[\w)]/g;
