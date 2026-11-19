// Labels and small pure helpers of the Withheld page (no React, so node --test can import them).
import { countText, formatCount } from "@shell";

export const TOOL_ID = "withheld";
export const VETOES_KEY = "withheld_vetoes";
export const RUN_COMMAND = "python -m step_5_same_complaint.run";

export const RELEASED_VIEW = "released";
export const WITHHELD_VIEW = "withheld";
export const VIEWS = [
  { value: RELEASED_VIEW, label: "Released by the check", key: "r" },
  { value: WITHHELD_VIEW, label: "Still withheld", key: "w" },
];

// Where a claim of a released group stands.
export const STATUS = {
  released: { label: "Released", badge: "badge-emerald" },
  kept: { label: "Kept withheld", badge: "badge-amber" },
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
  unmatched: "No same complaint",
  alone: "Only complaint",
};

/** The toolbar's counters. */
export function countsText(counts) {
  if (!counts) return "";
  return [
    `${formatCount(counts.withheld)} withheld by the rule`,
    `${formatCount(counts.released)} released by the check`,
    `${formatCount(counts.still_withheld)} still withheld`,
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

/** The selection after the list changed: the same key while listed, else the first. */
export function keepSelection(items, getKey, key) {
  return items.some((item) => getKey(item) === key) ? key : items[0] ? getKey(items[0]) : null;
}
