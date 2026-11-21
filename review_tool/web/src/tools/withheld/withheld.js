// Labels and small pure helpers of the Withheld page (no React, so node --test can import them).
import { countText, formatCount, quote } from "@shell";

export const TOOL_ID = "withheld";
export const VETOES_KEY = "withheld_vetoes";
export const RUN_COMMAND = "python -m step_5_same_complaint.run";
// The Conversations page, where a claim's conversation opens (`?thread=<id>`) at the first
// message the claim rests on (`&message=<id>`), with all of those marked (`&marked=<id>,<id>`).
export const THREADS_TOOL = "threads";
export const CONVERSATION_LINK_ID = "withheld-conversation-link";

export const RELEASED_VIEW = "released";
export const WITHHELD_VIEW = "withheld";
export const VIEWS = [
  { value: RELEASED_VIEW, label: "Released", key: "r" },
  { value: WITHHELD_VIEW, label: "Still withheld", key: "w" },
];
// How the "Still withheld" view orders its list: by the entity each claim is filed under (the
// most specific thing it names), or as the claims came in the chat.
export const BY_ENTITY = "entity";
export const CHAT_ORDER = "chat";
export const ORDERS = [
  { value: BY_ENTITY, label: "By entity" },
  { value: CHAT_ORDER, label: "Chat order" },
];
// The first row of the Released view's list: the claims released by the owner's hand.
export const BY_YOU_KEY = "by-you";

// Where a claim of a released group stands.
export const STATUS = {
  released: { label: "Released by the check", badge: "badge-emerald" },
  kept: { label: "Kept withheld", badge: "badge-amber" },
  by_you: { label: "Released by you", badge: "badge-sky" },
  short: { label: "Still withheld", badge: "badge-rose" },
  published: { label: "Published anyway", badge: "badge-slate" },
};

/** Whether a claim can carry the "keep withheld" mark (the rule withholds it). */
export function canKeep(claim) {
  return claim.status !== "published";
}

/** "backed by 1 person" / "backed by 3 people". */
export function peopleText(count) {
  return `backed by ${countText(count, "person", formatCount, "people")}`;
}

/** A group's backing: "3 people back these claims", plus what the owner's marks took away. */
export function groupPeopleText(group, minSupporters) {
  const base = `${countText(group.people, "person", formatCount, "people")} ${group.people === 1 ? "backs" : "back"} these claims`;
  const marked = group.people < group.people_unmarked ? ` (${formatCount(group.people_unmarked)} before the claims you kept withheld)` : "";
  const short = group.people < minSupporters ? `: fewer than ${minSupporters}, so nothing is released` : "";
  return `${base}${marked}${short}`;
}

/** Why a claim stays withheld, in a sentence. */
export function whyText(why, minSupporters = 2) {
  switch (why) {
    case "kept":
      return "You marked it “keep withheld”: the check never releases it.";
    case "waiting":
      return `The check has no answer yet for a list this claim is in (new or changed claims). Run the check: ${RUN_COMMAND}`;
    case "unusable":
      return "The model's answer about an entity it names was unusable, so nothing of that entity is released.";
    case "short":
      return `The check grouped it with the same complaint, but without the claims you kept withheld fewer than ${minSupporters} people back the group.`;
    case "same_person":
      return `The check found the same complaint in another claim, but the same member made both: one person who says a thing twice still counts as one, and the rule asks for ${minSupporters} different people.`;
    case "unmatched":
      return "The check found no other claim that makes the same complaint.";
    case "alone":
      return "No other member complains about what it names.";
    default:
      return why;
  }
}

/** The short label of a reason, for the list. */
export const WHY_LABELS = {
  kept: "Kept withheld by you",
  waiting: "Waiting for the check",
  unusable: "Answer unusable",
  short: "Too few people",
  same_person: "Same person twice",
  unmatched: "No same complaint",
  alone: "Only complaint",
};

/** The toolbar's counters. */
export function countsText(counts) {
  if (!counts) return "";
  return [
    `${formatCount(counts.withheld)} withheld by the rule`,
    `${formatCount(counts.still_withheld)} withheld now`,
    `${formatCount(counts.released)} released by the check`,
    `${formatCount(counts.released_by_you)} released by you`,
    `${formatCount(counts.kept)} kept withheld by you`,
  ].join(" · ");
}

/** The warning about lists the check has not answered (or answered unusably), or null. */
export function listsWarning(lists) {
  if (!lists) return null;
  const parts = [];
  if (lists.waiting > 0) parts.push(`${countText(lists.waiting, "entity", formatCount, "entities")} ${lists.waiting === 1 ? "waits" : "wait"} for the check (run ${RUN_COMMAND})`);
  if (lists.unusable > 0) parts.push(`the answer about ${countText(lists.unusable, "entity", formatCount, "entities")} was unusable`);
  return parts.length > 0 ? `${parts.join("; ")}: nothing of them is released.` : null;
}

/** "2024-03" -> "2024-03-15", a date the shell's month format reads (mid-month: no time zone
 * moves it to the month before). */
export function monthDate(month) {
  return month ? `${month}-15` : null;
}

export function keepToast(keep) {
  return keep ? "Kept withheld: the check will not release this claim" : "Mark removed: the check may release this claim again";
}

export function releaseToast(release) {
  return release ? "Released: the pages may use this claim" : "Withheld again: the claim stays off every page";
}

/** Where a claim of the "Still withheld" view stands now: withheld, released by you, or
 * released by the check (after you took your "keep withheld" mark away). */
export function claimState(claim) {
  if (claim.withheld) return "withheld";
  return claim.hand_released ? "by_you" : "released";
}

/** The rows of the "Still withheld" view: the claims withheld now, and the ones released in
 * this view since it was loaded (`releasedHere`), so a card keeps its place under the cursor. */
export function shownClaims(items, releasedHere) {
  return items.filter((claim) => claim.withheld || releasedHere.has(claim.short_id));
}

/** The Released view's list: the claims you released by hand first, then the entities. */
export function releasedRows(entities, byYou) {
  return [{ entity_id: BY_YOU_KEY, by_you: byYou }, ...entities];
}

/** The claims by the entity each is filed under: the entity the most claims of the whole chat
 * name first (a country or a public body before a small provider), then by name; the claims of
 * one entity stay in the order they came. */
export function byEntity(claims) {
  return claims
    .map((claim, position) => ({ claim, position }))
    .sort(
      (a, b) =>
        b.claim.filed_under_claims - a.claim.filed_under_claims ||
        a.claim.filed_under.localeCompare(b.claim.filed_under) ||
        a.position - b.position
    )
    .map(({ claim }) => claim);
}

/** The list's heading over each entity's claims: “entity” · 3 withheld · named by 1,464 claims
 * (how many of the listed claims are withheld now, and how many claims of the whole chat name
 * the entity). Returns the heading of a claim, for the list's `sectionOf`. */
export function entitySections(claims, format = formatCount) {
  const withheld = new Map();
  for (const claim of claims) withheld.set(claim.filed_under, (withheld.get(claim.filed_under) ?? 0) + (claim.withheld ? 1 : 0));
  return (claim) =>
    `${quote(claim.filed_under)} · ${format(withheld.get(claim.filed_under))} withheld · named by ${countText(claim.filed_under_claims, "claim", format)}`;
}

/** "No same complaint 232 · Only complaint 32": how many of the claims withheld now stay
 * withheld for each reason, the most common first. */
export function reasonCounts(claims, format = formatCount) {
  const counts = new Map();
  for (const claim of claims) if (claim.withheld) counts.set(claim.why, (counts.get(claim.why) ?? 0) + 1);
  return [...counts]
    .sort((a, b) => b[1] - a[1])
    .map(([why, count]) => `${WHY_LABELS[why] ?? why} ${format(count)}`)
    .join(" · ");
}

/** The claims that are one piece of a message the wiki tells in part: a published claim rests
 * on a message they rest on. */
export function piecesOnly(claims) {
  return claims.filter((claim) => claim.published_siblings > 0);
}

/** What the detail says about such a claim, or null. */
export function piecesNote(claim, format = formatCount) {
  const count = claim.published_siblings ?? 0;
  if (count === 0) return null;
  return `One piece of a longer message: ${countText(count, "published claim", format)} ${count === 1 ? "rests" : "rest"} on the same message. The wiki will tell the rest of what was said there without this piece.`;
}

/** The Conversations page's link parameters for a claim's conversation: opened at the first
 * message the claim rests on, with all of them marked. */
export function conversationParams(claim) {
  const ids = claim.message_ids ?? [];
  return ids.length > 0 ? { thread: claim.thread_id, message: ids[0], marked: ids.join(",") } : { thread: claim.thread_id };
}

/** The selection after the list changed: the same key while listed, else the first. */
export function keepSelection(items, getKey, key) {
  return items.some((item) => getKey(item) === key) ? key : items[0] ? getKey(items[0]) : null;
}
