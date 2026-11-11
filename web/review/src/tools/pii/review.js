// Labels and small pure helpers of the PII tool (no React, so node --test can import them).

export const PAGE_SIZE = 100;

// The mark step 3 puts in the claim text in place of a removed detail (content, kept as is).
export const MARK = "[הוסר]";

export const FILTERS = [
  { value: "pending", label: "Pending" },
  { value: "reviewed", label: "Reviewed" },
  { value: "all", label: "All" },
];

export const SORTS = [
  { value: "claim_id", label: "ID" },
  { value: "thread_id", label: "Thread" },
  { value: "date", label: "Date" },
  { value: "redactions", label: "Removals" },
  { value: "supporters", label: "Supporters" },
];

export const ORDERS = [
  { value: "asc", label: "Ascending" },
  { value: "desc", label: "Descending" },
];

export const STATUS = {
  pending: { label: "Pending", badge: "badge-amber" },
  accepted: { label: "Removal accepted", badge: "badge-slate" },
  restored: { label: "Restored", badge: "badge-emerald" },
  partial: { label: "Partly restored", badge: "badge-sky" },
};

export const DECISION_TOASTS = {
  accepted: "Removal accepted",
  restored: "Details restored",
  partial: "Marked details restored",
};

export const TYPE_LABELS = { phone: "Phone", email: "Email" };

export function typeLabel(type) {
  return TYPE_LABELS[type] ?? type;
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
  if (chosenCount === 0 || chosenCount === total) return total === 1 ? "Restore the detail" : "Restore all details";
  return `Restore ${chosenCount} of ${total} details`;
}

/** "2024-01-05" or "2024-01-05 – 2024-02-01" (C3 dates are YYYY-MM-DD). */
export function dateRange(first, last) {
  if (!first) return "—";
  return !last || last === first ? first : `${first} – ${last}`;
}

/** "Phone 2 · Email 1" from `{phone: 2, email: 1}`. */
export function typeCounts(counts) {
  return Object.entries(counts ?? {})
    .map(([type, count]) => `${typeLabel(type)} ${count}`)
    .join(" · ");
}
