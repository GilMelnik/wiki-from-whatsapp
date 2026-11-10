// Labels and pure helpers of the claim-groups tool (no React, so node --test can import them).

export const PAGE_SIZE = 100;
export const SUGGESTION_PAGE_SIZE = 50;
export const SEARCH_DEBOUNCE_MS = 250;
export const PICKER_LIMIT = 30;

export const MODES = [
  { value: "suggestions", label: "הצעות מיזוג", key: "p" },
  { value: "groups", label: "כל הקבוצות", key: "g" },
];

export const STANCES = {
  positive: { label: "חיובית", badge: "badge-emerald" },
  negative: { label: "שלילית", badge: "badge-rose" },
  neutral: { label: "ניטרלית", badge: "badge-slate" },
  factual: { label: "עובדתית", badge: "badge-sky" },
};

export const STANCE_FILTERS = [
  { value: "", label: "כל העמדות" },
  ...Object.entries(STANCES).map(([value, { label }]) => ({ value, label })),
];

export const SORTS = [
  { value: "size", label: "מספר טענות" },
  { value: "supporters", label: "תומכים" },
  { value: "last_date", label: "תאריך אחרון" },
  { value: "created", label: "סדר יצירה" },
];

export const ORDERS = [
  { value: "desc", label: "יורד" },
  { value: "asc", label: "עולה" },
];

export const DEFAULT_VIEW = {
  multi: false,
  contradictions: false,
  suggested: false,
  entity: "",
  stance: "",
  q: "",
  sort: "size",
  order: "desc",
};

/** The list query for the server: false flags and empty texts are left out. */
export function viewParams(view) {
  return Object.fromEntries(Object.entries(view).filter(([, value]) => value !== false && value !== ""));
}

export function stanceOf(stance) {
  return STANCES[stance] ?? { label: stance || "—", badge: "badge-slate" };
}

/** "2024-01-05" or "2024-01-05 – 2024-02-01" (C6 / C3 dates are YYYY-MM-DD). */
export function dateRange(first, last) {
  if (!first) return "—";
  return !last || last === first ? first : `${first} – ${last}`;
}

/** A cosine or margin with three decimals; margins get an explicit sign. */
export function formatScore(value, { signed = false } = {}) {
  const text = Math.abs(value).toFixed(3);
  if (!signed) return value.toFixed(3);
  return value < 0 ? `−${text}` : `+${text}`;
}

/** The entity filter's options: every entity that some group mentions, most groups first. */
export function entityOptions(entities = []) {
  return [
    { value: "", label: "כל הישויות" },
    ...entities.map((entity) => ({ value: entity.entity_id, label: `${entity.name} (${entity.groups})` })),
  ];
}

/** Width (percent) of a timeline bar; a non-zero value is always visible. */
export function barPercent(value, max) {
  if (!value || !max) return 0;
  return Math.max(4, Math.round((value / max) * 100));
}

/** The claim ids picked in the current group (picks of another group do not count). */
export function pickedIn(picked, groupId) {
  return picked.groupId === groupId ? picked.claimIds : [];
}

export function togglePick(picked, groupId, claimId) {
  const current = pickedIn(picked, groupId);
  const claimIds = current.includes(claimId) ? current.filter((id) => id !== claimId) : [...current, claimId];
  return { groupId, claimIds };
}

/**
 * Which suggestion to show after the list changed: the same pair if it is still listed,
 * otherwise the one now at the position it had (the next one, after a decision removed it),
 * or the last one when the list got shorter.
 */
export function followSuggestion(items, selectedKey, lastIndex) {
  if (items.length === 0) return null;
  if (items.some((item) => item.key === selectedKey)) return selectedKey;
  return items[Math.min(Math.max(lastIndex, 0), items.length - 1)].key;
}
