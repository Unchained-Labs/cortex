import { useEffect, useMemo, useRef } from "react";
import { renderMarkdown, VAULT_PATH_RE, type RenderOptions } from "../lib/markdown";

interface Props extends RenderOptions {
  text: string;
  /** click on a [[wikilink]] */
  onWikilink?: (target: string) => void;
  /** click on a `vaults/...` path citation (Chat) */
  onVaultPath?: (path: string) => void;
  /** a task checkbox was toggled; index counts tasks in document order */
  onTaskToggle?: (index: number, checked: boolean) => void;
}

/** Wrap `vaults/...` path mentions in text/code nodes with clickable cites. */
function linkifyVaultPaths(root: HTMLElement): void {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      if (parent.closest("a, .vault-cite, pre")) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const targets: Text[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (VAULT_PATH_RE.test((n as Text).data)) targets.push(n as Text);
    VAULT_PATH_RE.lastIndex = 0;
  }
  for (const node of targets) {
    const frag = document.createDocumentFragment();
    let last = 0;
    const text = node.data;
    VAULT_PATH_RE.lastIndex = 0;
    for (let m = VAULT_PATH_RE.exec(text); m; m = VAULT_PATH_RE.exec(text)) {
      frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const a = document.createElement("a");
      a.className = "vault-cite";
      a.href = "#";
      a.dataset.vaultPath = m[0];
      a.textContent = m[0];
      frag.appendChild(a);
      last = m.index + m[0].length;
    }
    frag.appendChild(document.createTextNode(text.slice(last)));
    node.replaceWith(frag);
  }
}

export default function Markdown({
  text,
  resolveEmbed,
  interactiveTasks,
  onWikilink,
  onVaultPath,
  onTaskToggle,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const html = useMemo(
    () => renderMarkdown(text, { resolveEmbed, interactiveTasks }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [text, resolveEmbed, interactiveTasks],
  );

  useEffect(() => {
    if (ref.current && onVaultPath) linkifyVaultPaths(ref.current);
  }, [html, onVaultPath]);

  const onClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = e.target as HTMLElement;
    const wiki = el.closest<HTMLElement>("a[data-wikilink]");
    if (wiki?.dataset.wikilink !== undefined) {
      e.preventDefault();
      onWikilink?.(wiki.dataset.wikilink);
      return;
    }
    const cite = el.closest<HTMLElement>("a.vault-cite");
    if (cite?.dataset.vaultPath) {
      e.preventDefault();
      onVaultPath?.(cite.dataset.vaultPath);
      return;
    }
    if (el instanceof HTMLInputElement && el.dataset.task !== undefined) {
      if (!interactiveTasks || !onTaskToggle) {
        e.preventDefault();
        return;
      }
      onTaskToggle(Number(el.dataset.task), el.checked);
    }
  };

  return (
    <div
      ref={ref}
      className="md"
      onClick={onClick}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
