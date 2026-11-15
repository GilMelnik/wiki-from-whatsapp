// Labels and small pure helpers of the privacy tool (no React, so node --test can import them).
import { countText } from "@shell";

export const PAGE_SIZE = 100;

// The mark step 3 puts in the claim text in place of a removed detail (content, kept as is).
export const MARK = "[הוסר]";

// The list's sections, in the server's order: to decide, hidden (the removal stays), shown
// (the details are back in the claim). "Hide" and "show" say what happens to the detail.
export const SECTION_LABELS = {
  pending: "To decide",
  accepted: "Hidden",
  restored: "Shown",
  partial: "Shown",
};

export const STATUS_LABELS = {
  pending: "To decide",
  accepted: "Hidden",
  restored: "Shown",
  partial: "Partly shown",
};

export const DECISION_TOASTS = {
  accepted: "Hidden: the detail stays out of the claim",
  restored: "Shown: the details are back in the claim",
  partial: "Shown: the marked details are back in the claim",
};

export const TYPE_LABELS = { phone: "Phone", email: "Email" };
const TYPE_NOUNS = { phone: ["phone number", "phone numbers"], email: ["e-mail address", "e-mail addresses"] };

// What an entity's contact list says about a removed value.
export const OWNER_LABELS = {
  contact: "a contact of",
  uncertain: "an uncertain contact of",
  rejected: "rejected as a contact of",
};

export function typeLabel(type) {
  return TYPE_LABELS[type] ?? type;
}

/** The list section a row belongs to. */
export function sectionOf(row) {
  return SECTION_LABELS[row.status] ?? null;
}

/** "1 phone number, 2 e-mail addresses" from `{phone: 1, email: 2}`. */
export function removedText(counts) {
  return Object.entries(counts ?? {})
    .map(([type, count]) => {
      const [singular, plural] = TYPE_NOUNS[type] ?? [type, `${type}s`];
      return countText(count, singular, undefined, plural);
    })
    .join(", ");
}

/** The mark numbers chosen for restoring, with `index` switched on or off (kept sorted). */
export function toggleMark(marks, index) {
  if (marks.includes(index)) return marks.filter((mark) => mark !== index);
  return [...marks, index].sort((a, b) => a - b);
}

/** What "restore" will put back: the chosen marks, or every mark when none is chosen. */
export function marksToRestore(chosen, count) {
  return chosen.length > 0 ? chosen : Array.from({ length: count }, (_, index) => index);
}

export function restoreLabel(chosenCount, total) {
  if (chosenCount === 0 || chosenCount === total) return total === 1 ? "Show: put the detail back" : "Show: put all details back";
  return `Show: put ${chosenCount} of ${total} details back`;
}

/** "2024-01-05" or "2024-01-05 – 2024-02-01" (C3 dates are YYYY-MM-DD). */
export function dateRange(first, last) {
  if (!first) return "—";
  return !last || last === first ? first : `${first} – ${last}`;
}

/** The claim's messages that hold removed detail `index`. */
export function messagesHolding(messages, index) {
  return (messages ?? []).filter((message) => message.holds.includes(index));
}

/** The claim's messages that hold none of the removed details. */
export function otherMessages(messages) {
  return (messages ?? []).filter((message) => message.holds.length === 0);
}
