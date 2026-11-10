// Labels and pure helpers of the entity tool (no React, so node --test can import them).

export const PAGE_SIZE = 100;
export const SEARCH_DEBOUNCE_MS = 250;
export const TARGET_SEARCH_LIMIT = 40;

export const STATUSES = {
  suggested: { label: "מוצעת", badge: "badge-slate" },
  ambiguous: { label: "דו-משמעית", badge: "badge-amber" },
  accepted: { label: "אושרה", badge: "badge-emerald" },
  rejected: { label: "נדחתה", badge: "badge-rose" },
};

export const STATUS_FILTERS = [
  { value: "all", label: "כל הסטטוסים" },
  ...Object.entries(STATUSES).map(([value, { label }]) => ({ value, label })),
];

export const FLAG_FILTERS = [
  { value: "all", label: "הכול" },
  { value: "attached", label: "שמות שצורפו אוטומטית" },
  { value: "conflict", label: "חשד לבלבול עם ישות אחרת" },
  { value: "uncertain", label: "פרטי קשר לא ודאיים" },
  { value: "multi", label: "יותר משם אחד" },
];

export const SORTS = [
  { value: "count", label: "אזכורים" },
  { value: "size", label: "מספר שמות" },
  { value: "score", label: "לכידות" },
  { value: "id", label: "מזהה (חדשות בסוף)" },
  { value: "name", label: "שם" },
];

export const ORDERS = [
  { value: "desc", label: "יורד" },
  { value: "asc", label: "עולה" },
];

export const DEFAULT_VIEW = { status: "all", flag: "all", q: "", sort: "count", order: "desc" };

export const SIGNAL_LABELS = {
  co_occur: "אזכור משותף",
  confident_contact: "פרטי קשר",
  string: "דמיון כתיב",
  prefix: "תחילית",
  seed: "רשימת זרע",
  transliteration: "תעתיק",
};

export const STANCE_LABELS = {
  positive: "חיובית",
  negative: "שלילית",
  neutral: "ניטרלית",
  factual: "עובדתית",
};

export const CONTACT_KINDS = [
  { key: "email", label: "דוא״ל" },
  { key: "phone", label: "טלפון" },
  { key: "website", label: "אתר" },
];

// Colours of members (self mentions) and of other entities in a claim text. Static class
// names, so Tailwind keeps them; blue (selection), rose (danger), amber (warning) stay free.
const PALETTE = [
  { card: "border-violet-200 bg-violet-50/60", mark: "bg-violet-100 text-violet-900", dot: "bg-violet-500" },
  { card: "border-teal-200 bg-teal-50/60", mark: "bg-teal-100 text-teal-900", dot: "bg-teal-500" },
  { card: "border-orange-200 bg-orange-50/60", mark: "bg-orange-100 text-orange-900", dot: "bg-orange-500" },
  { card: "border-pink-200 bg-pink-50/60", mark: "bg-pink-100 text-pink-900", dot: "bg-pink-500" },
  { card: "border-lime-200 bg-lime-50/60", mark: "bg-lime-100 text-lime-900", dot: "bg-lime-500" },
  { card: "border-cyan-200 bg-cyan-50/60", mark: "bg-cyan-100 text-cyan-900", dot: "bg-cyan-500" },
  { card: "border-fuchsia-200 bg-fuchsia-50/60", mark: "bg-fuchsia-100 text-fuchsia-900", dot: "bg-fuchsia-500" },
  { card: "border-yellow-200 bg-yellow-50/60", mark: "bg-yellow-100 text-yellow-900", dot: "bg-yellow-500" },
];

export function colour(index) {
  const size = PALETTE.length;
  return PALETTE[(((index ?? 0) % size) + size) % size];
}

export function statusOf(status) {
  return STATUSES[status] ?? STATUSES.suggested;
}

/** The text cut into plain pieces and highlighted pieces (`highlights` sorted, no overlap). */
export function segments(text, highlights) {
  const out = [];
  let cursor = 0;
  for (const highlight of highlights ?? []) {
    if (highlight.start < cursor || highlight.end > text.length) continue;
    if (highlight.start > cursor) out.push({ text: text.slice(cursor, highlight.start) });
    out.push({ text: text.slice(highlight.start, highlight.end), highlight });
    cursor = highlight.end;
  }
  if (cursor < text.length) out.push({ text: text.slice(cursor) });
  return out;
}

/** One value per line, trimmed, empty lines dropped. */
export function linesToList(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export function listToLines(values) {
  return (values ?? []).join("\n");
}

/** Every contact kind as lines, for the contacts form. */
export function contactsToForm(contacts) {
  return Object.fromEntries(CONTACT_KINDS.map(({ key }) => [key, listToLines(contacts?.[key])]));
}

export function formToContacts(form) {
  return Object.fromEntries(CONTACT_KINDS.map(({ key }) => [key, linesToList(form[key] ?? "")]));
}

export function contactsChanged(contacts, form) {
  return CONTACT_KINDS.some(({ key }) => listToLines(contacts?.[key]) !== listToLines(linesToList(form[key] ?? "")));
}

// Picked claims: `{ entityId, byName: { [memberName]: [claimId, ...] }, texts: { [claimId]: text } }`.
// They belong to one entity; any other entity's detail starts with nothing picked. The texts
// are kept for the claims-grouping dialog (picked claims may sit on pages no longer shown).

export const NOTHING_PICKED = { entityId: null, byName: {}, texts: {} };

export function pickedFor(picked, entityId) {
  return picked.entityId === entityId ? picked.byName : {};
}

export function togglePicked(picked, entityId, name, claim) {
  const same = picked.entityId === entityId;
  const byName = { ...pickedFor(picked, entityId) };
  const texts = { ...(same ? picked.texts : {}), [claim.claim_id]: claim.claim_text };
  const ids = byName[name] ?? [];
  const id = claim.claim_id;
  byName[name] = ids.includes(id) ? ids.filter((other) => other !== id) : [...ids, id];
  if (byName[name].length === 0) delete byName[name];
  return { entityId, byName, texts };
}

/** Every picked claim of the entity once (a claim can be picked under two names). */
export function allPicked(picked, entityId) {
  return [...new Set(Object.values(pickedFor(picked, entityId)).flat())];
}
