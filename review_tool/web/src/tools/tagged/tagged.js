// Labels and small pure helpers of the Tagged members page (no React, so node --test can import them).
import { countText, formatDay } from "@shell";

export const TOOL_ID = "tagged";
export const TABLE_KEY = "tagged_members";
export const THREADS_TOOL = "threads";

export const STATUS_LABELS = {
  provider: "Provider",
  not_provider: "Not a provider",
  undecided: "Undecided",
};

export const STATUS_BADGES = {
  provider: "badge-emerald",
  not_provider: "badge-slate",
  undecided: "badge-amber",
};

export const DEFAULT_SHOW = "waiting";

/** The list's filters; the first (the default) uses the server's `min_taggers`. */
export function showOptions(minTaggers) {
  return [
    { value: DEFAULT_SHOW, label: `Undecided, tagged by ${minTaggers ?? "N"} or more members` },
    { value: "undecided", label: "All undecided" },
    { value: "decided", label: "Decided" },
    { value: "all", label: "All tagged members" },
  ];
}

export function emptyListText(show) {
  if (show === DEFAULT_SHOW) return "No undecided member is tagged by that many members. Pick another filter to see the rest.";
  if (show === "decided") return "No decision yet.";
  return "The chat export tags no member.";
}

/** How the chat writes the member's tag: "@972…" (the first of their tags). */
export function tagText(row) {
  return `@${row.tags?.[0] ?? "?"}`;
}

/** "47 tags by 12 members · wrote 310 messages". */
export function statsText(row) {
  return [
    `${countText(row.tag_count, "tag")} by ${countText(row.tagger_count, "member")}`,
    `wrote ${countText(row.written, "message")}`,
  ].join(" · ");
}

/** The first and the last tag, by day ("Fri 5 Jan 2024", or "Fri 5 Jan 2024 – Sat 2 Mar 2024"). */
export function tagSpan(row) {
  if (!row.first) return "—";
  const first = formatDay(row.first);
  const last = formatDay(row.last);
  return first === last ? first : `${first} – ${last}`;
}

/** The tag of the member a message holds (to mark it), else the member's first tag. */
export function tagIn(content, tags) {
  return (tags ?? []).find((tag) => (content ?? "").includes(`@${tag}`)) ?? tags?.[0] ?? "";
}

/** The member's first tagging message in a conversation (its id), where a link opens it; null when none. */
export function firstTagIn(messages, threadId) {
  return (messages ?? []).find((message) => message.thread_id === threadId)?.id ?? null;
}

/** The name typed or picked, as the server will save it (spaces squashed). */
export function cleanName(name) {
  return (name ?? "").split(/\s+/).filter(Boolean).join(" ");
}

export function decisionToast(member) {
  if (member.status === "provider") return `Saved: a provider, shown to step 3 as “@${member.public_name}”`;
  if (member.status === "not_provider") return "Saved: not a provider";
  return "Decision cleared: undecided again";
}

/** After a save: the member itself when still listed, else the one listed after it before the save, else the first. */
export function nextSelection(before, after, id) {
  const ids = new Set(after.map((row) => row.member));
  if (ids.has(id)) return id;
  const index = before.findIndex((row) => row.member === id);
  const following = index === -1 ? [] : before.slice(index + 1).map((row) => row.member);
  return following.find((member) => ids.has(member)) ?? after[0]?.member ?? null;
}
