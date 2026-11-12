// Labels and pure helpers of the entity tool (no React, so node --test can import them).

export const PAGE_SIZE = 100;
// How many more claims "Show more" under a name adds (the detail shows the first three).
export const MORE_CLAIMS = 12;
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
  { value: "review", label: "Needs review first" },
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

export const DEFAULT_VIEW = { status: "all", flag: "all", q: "", sort: "review", order: "desc" };

// The sections of the review order (the server's `section` of a list item).
export const SECTION_LABELS = {
  review: "Needs review",
  several: "Several names",
  one: "One name",
  accepted: "Accepted",
  rejected: "Rejected",
};

export const SIGNAL_LABELS = {
  co_occur: "Mentioned together",
  confident_contact: "Same contacts",
  string: "Similar spelling",
  prefix: "Prefix",
  seed: "Seed list",
  transliteration: "Transliteration",
};

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

/** The list's section headings: only in the review order (other orders mix the sections). */
export function sectionsFor(view) {
  return view.sort === "review" ? (item) => SECTION_LABELS[item.section] ?? null : undefined;
}

/** The tooltip that names a claim and its conversation (ids are not shown under every claim). */
export function claimTip(claim) {
  return claim.thread_id ? `Claim ${claim.claim_id} · conversation ${claim.thread_id}` : `Claim ${claim.claim_id}`;
}

/** How many claims of a name to show after one more "Show more" (never past `count`). */
export function moreShown(shown, count, step = MORE_CLAIMS) {
  return Math.min(count, shown + step);
}

/**
 * Every contact of the entity in one list for the decision, each with its state (contact,
 * uncertain, rejected) and the claim it was found in (`sources` from the server; a contact
 * typed by hand has none).
 */
export function contactRows(entity) {
  const sourceOf = new Map((entity.contact_sources ?? []).map((source) => [`${source.kind}\u0001${source.value}`, source]));
  const rows = [];
  for (const [field, state] of [["contacts", "contact"], ["contacts_uncertain", "uncertain"], ["contacts_rejected", "rejected"]]) {
    for (const { key } of CONTACT_KINDS) {
      for (const value of entity[field]?.[key] ?? []) {
        rows.push({ kind: key, value, state, source: sourceOf.get(`${key}\u0001${value}`) ?? null });
      }
    }
  }
  return rows;
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
