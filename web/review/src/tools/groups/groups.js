// Labels and pure helpers of the claim-groups tool (no React, so node --test can import them).

import { isolate, STANCES } from "@shell";

export const PAGE_SIZE = 100;
export const SUGGESTION_PAGE_SIZE = 50;
export const SEARCH_DEBOUNCE_MS = 250;
export const PICKER_LIMIT = 30;

export const MODES = [
  { value: "suggestions", label: "Merge suggestions", key: "p" },
  { value: "groups", label: "All groups", key: "g" },
];

export const STANCE_FILTERS = [
  { value: "", label: "All stances" },
  ...Object.entries(STANCES).map(([value, { label }]) => ({ value, label })),
];

export const SORTS = [
  { value: "size", label: "Number of claims" },
  { value: "supporters", label: "Supporters" },
  { value: "last_date", label: "Last date" },
  { value: "created", label: "Creation order" },
];

export const ORDERS = [
  { value: "desc", label: "Descending" },
  { value: "asc", label: "Ascending" },
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
    { value: "", label: "All entities" },
    ...entities.map((entity) => ({ value: entity.entity_id, label: `${isolate(entity.name)} (${entity.groups})` })),
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
