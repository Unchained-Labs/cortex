/**
 * The sentences the rules and jobs panels speak.
 *
 * Every saved rule and job comes down with a `describes` string, and that is
 * what gets rendered — the panel never reassembles a rule from its parts. Two
 * places have no server sentence to render, though: the `suggested` entries
 * arrive as bare specs, and a rule being built does not exist yet. So the same
 * sentence is composed here, phrased to match `cortex/rules.py` and
 * `cortex/jobs.py` exactly, and the two sources stay indistinguishable.
 */

import type { Job, JobSpec, Rule, RuleAction, RuleMatch } from "../types";
import { shortAgo } from "./paths";

/** `'recipe'` — Python's repr of a plain string, which is what the server prints. */
function quoted(value: string): string {
  return `'${value}'`;
}

export function describeMatch(match: RuleMatch): string {
  if (match.kind === "older_than_days") return `older than ${match.value} days`;
  return `${match.kind} matches ${quoted(match.value)}`;
}

export function describeAction(action: RuleAction): string {
  if (action.kind === "move") return `move into ${action.value}/`;
  if (action.kind === "archive") return `archive into ${action.value || "archive"}/`;
  return `add #${action.value}`;
}

/** "tag matches 'recipe' → move into recipes/" */
export function describeRule(rule: {
  matches?: RuleMatch[];
  action?: RuleAction;
}): string {
  const conditions = (rule.matches ?? []).map(describeMatch).join(" and ") || "everything";
  const action = rule.action ? describeAction(rule.action) : "";
  return `${conditions} → ${action}`;
}

/** The server's sentence when there is one; the same sentence when there is not. */
export function ruleSentence(rule: Rule): string {
  return rule.describes ?? describeRule(rule);
}

/**
 * The only intervals on offer. Hours are the unit the API takes, but nobody
 * thinks in hours — so these five labels are the whole vocabulary, and there
 * is no way to type a number or a cron string.
 */
export const INTERVALS: { hours: number; label: string }[] = [
  { hours: 1, label: "hourly" },
  { hours: 6, label: "every 6 hours" },
  { hours: 12, label: "every 12 hours" },
  { hours: 24, label: "daily" },
  { hours: 168, label: "weekly" },
];

/** An interval in the words a person would use, for any value the API returns. */
export function everyLabel(hours: number): string {
  const known = INTERVALS.find((i) => i.hours === hours);
  if (known) return known.label;
  if (hours < 1) return `every ${Math.round(hours * 60)} minutes`;
  if (hours < 24) return `every ${hours} hours`;
  return `every ${hours / 24} days`;
}

function jobPhrase(job: { kind: string; settings: Record<string, unknown> }): string {
  const settings = job.settings ?? {};
  const text = (key: string, fallback: string) => {
    const value = settings[key];
    return value === undefined ? fallback : String(value);
  };
  if (job.kind === "connector") return `sync the ${text("connector", "?")} connector`;
  if (job.kind === "index") return "re-index the brain";
  if (job.kind === "rules") {
    return `${settings.dry_run ? "preview" : "apply"} the tidying rules`;
  }
  if (job.kind === "digest") return `write today's digest into ${text("vault", "shared")}`;
  if (job.kind === "channel_digest") {
    return `post today's digest into #${text("channel", "general")}`;
  }
  return job.kind;
}

/** "apply the tidying rules daily" */
export function describeJob(job: {
  kind: string;
  interval_hours: number;
  settings: Record<string, unknown>;
}): string {
  return `${jobPhrase(job)} ${everyLabel(job.interval_hours)}`;
}

export function jobSentence(job: JobSpec | Job): string {
  return job.describes ?? describeJob(job);
}

/**
 * "2 hours ago", from the ISO stamps the store writes. `shortAgo` counts in
 * epoch seconds, which is what the digest speaks; jobs speak ISO.
 */
export function isoAgo(iso: string): string {
  const at = Date.parse(iso);
  return Number.isNaN(at) ? iso : shortAgo(at / 1000);
}

/** `2026-08-22T09:30:00+00:00` → `22 Aug, 09:30` — when a rule moved a note. */
export function runWhen(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  const day = at.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const time = at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return `${day}, ${time}`;
}

/** Placeholders that show the shape of a value, per condition kind. */
export const MATCH_HINTS: Record<string, string> = {
  path: "recipes/*.md",
  tag: "recipe",
  frontmatter: "area: garden",
  content: "boiler",
  older_than_days: "90",
};

/** The same, per action kind. */
export const ACTION_HINTS: Record<string, string> = {
  move: "recipes",
  tag: "sorted",
  archive: "archive/clips",
};

/** What each condition kind means, in the dropdown's own words. */
export const MATCH_LABELS: Record<string, string> = {
  path: "path matches",
  tag: "has the tag",
  frontmatter: "frontmatter has",
  content: "text contains",
  older_than_days: "older than (days)",
};

export const ACTION_LABELS: Record<string, string> = {
  move: "move into a folder",
  tag: "add a tag",
  archive: "archive into a folder",
};

export const JOB_KIND_LABELS: Record<string, string> = {
  connector: "sync a connector",
  index: "re-index the brain",
  rules: "run the tidying rules",
  digest: "write today's digest into a vault",
  channel_digest: "post today's digest into a channel",
};
