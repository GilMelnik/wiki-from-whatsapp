// Labels and pure helpers of the entity tool (no React, so node --test can import them).

export const PAGE_SIZE = 100;
export const SEARCH_DEBOUNCE_MS = 250;
export const TARGET_SEARCH_LIMIT = 40;

export const STATUSES = {
  suggested: { label: "Suggested", badge: "badge-slate" },
  ambiguous: { label: "Ambiguous", badge: "badge-amber" },
  accepted: { label: "Accepted", badge: "badge-emerald" },
  rejected: { label: "Rejected", badge: "badge-rose" },
};

export const STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  ...Object.entries(STATUSES).map(([value, { label }]) => ({ value, label })),
];

export const FLAG_FILTERS = [
  { value: "all", label: "All" },
  { value: "attached", label: "Names attached automatically" },
  { value: "conflict", label: "May be confused with another entity" },
  { value: "uncertain", label: "Uncertain contacts" },
  { value: "multi", label: "More than one name" },
];

export const SORTS = [
  { value: "count", label: "Mentions" },
  { value: "size", label: "Number of names" },
  { value: "score", label: "Cohesion" },
  { value: "id", label: "ID (new ones last)" },
  { value: "name", label: "Name" },
];

export const ORDERS = [
  { value: "desc", label: "Descending" },
  { value: "asc", label: "Ascending" },
];

export const DEFAULT_VIEW = { status: "all", flag: "all", q: "", sort: "count", order: "desc" };

export const SIGNAL_LABELS = {
  co_occur: "Mentioned together",
  confident_contact: "Same contacts",
  string: "Similar spelling",
  prefix: "Prefix",
  seed: "Seed list",
  transliteration: "Transliteration",
};

// The kinds of `config.json` `entities.kind.kinds`. A person's lone negative claim is kept
// off the wiki (task 29), so the owner can correct a wrong kind here.
export const KINDS = [
  { value: "person", label: "Person" },
  { value: "organisation", label: "Organisation" },
  { value: "place", label: "Place" },
  { value: "other", label: "Other" },
];

/** Where the entity's kind comes from, in words (the line under the kind control). */
export function kindNote(entity) {
  if (entity.kind_manual) return "Set by you; step 4 keeps it.";
  if (!entity.kind) return "Not classified yet: counts as a person until step 4 classifies it.";
  const sure = entity.kind_confidence == null ? "" : `, ${Math.round(entity.kind_confidence * 100)}% sure`;
  return `Set by the classifier${sure}; step 4 asks again when the names change.`;
}

export const CONTACT_KINDS = [
  { key: "email", label: "Email" },
  { key: "phone", label: "Phone" },
  { key: "website", label: "Website" },
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
