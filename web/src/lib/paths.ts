/** Index keys and timestamps as the digest and search endpoints report them. */

/**
 * The index keys a digest/search hit carries look like
 * `vaults/<name>/<relative path>`. The Vault view wants the two halves apart.
 * Anything else (`sources/…`) has no vault to open — callers render it plain.
 */
export function splitVaultKey(key: string): { vault: string; path: string } | null {
  const m = /^vaults\/([^/]+)\/(.+)$/.exec(key);
  return m ? { vault: m[1], path: m[2] } : null;
}

/** Last path segment — enough to recognise a file in a list. */
export function baseName(key: string): string {
  const i = key.lastIndexOf("/");
  return i === -1 ? key : key.slice(i + 1);
}

/** Coarse "how long ago", from an epoch-seconds mtime. */
export function shortAgo(mtime: number): string {
  const secs = Date.now() / 1000 - mtime;
  if (secs < 90) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 36) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** `2026-08-21T09:30:00+02:00` → `09:30` today, `2026-08-23 09:30` later. */
export function eventWhen(start: string, today: boolean): string {
  const time = start.includes("T") ? start.slice(11, 16) : "";
  if (today) return time || "all day";
  const day = start.slice(0, 10);
  return time ? `${day} ${time}` : day;
}
