// Labels and small pure helpers of the PII tool (no React, so node --test can import them).

export const PAGE_SIZE = 100;

export const FILTERS = [
  { value: "pending", label: "ממתינות" },
  { value: "reviewed", label: "נבדקו" },
  { value: "all", label: "הכל" },
];

export const SORTS = [
  { value: "claim_id", label: "מזהה" },
  { value: "thread_id", label: "שיחה" },
  { value: "date", label: "תאריך" },
  { value: "redactions", label: "מספר הסרות" },
  { value: "supporters", label: "תומכים" },
];

export const ORDERS = [
  { value: "asc", label: "עולה" },
  { value: "desc", label: "יורד" },
];

export const STATUS = {
  pending: { label: "ממתינה", badge: "badge-amber" },
  accepted: { label: "ההסרה אושרה", badge: "badge-slate" },
  restored: { label: "שוחזרה", badge: "badge-emerald" },
  partial: { label: "שוחזרה חלקית", badge: "badge-sky" },
};

export const DECISION_TOASTS = {
  accepted: "ההסרה אושרה",
  restored: "הפרטים שוחזרו",
  partial: "הפרטים המסומנים שוחזרו",
};

export const TYPE_LABELS = { phone: "טלפון", email: "דוא״ל" };

export const STANCE_LABELS = {
  positive: "חיובית",
  negative: "שלילית",
  neutral: "ניטרלית",
  factual: "עובדתית",
};

export function typeLabel(type) {
  return TYPE_LABELS[type] ?? type;
}

/** First index of the page that shows queue position `position`. */
export function pageStart(position, pageSize) {
  return Math.floor(position / pageSize) * pageSize;
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
  if (chosenCount === 0 || chosenCount === total) return total === 1 ? "שחזור הפרט" : "שחזור כל הפרטים";
  return `שחזור ${chosenCount} מתוך ${total} הפרטים`;
}

/** "2024-01-05" or "2024-01-05 – 2024-02-01" (C3 dates are YYYY-MM-DD). */
export function dateRange(first, last) {
  if (!first) return "—";
  return !last || last === first ? first : `${first} – ${last}`;
}

/** "טלפון 2 · דוא״ל 1" from `{phone: 2, email: 1}`. */
export function typeCounts(counts) {
  return Object.entries(counts ?? {})
    .map(([type, count]) => `${typeLabel(type)} ${count}`)
    .join(" · ");
}
