// Pure helpers of the suggestions tool (no React): the list's order and filters, which of a
// set of suggestions an action can move, what an apply click will cost, the request body of a
// corrected plan operation, and the Hebrew wording of outcomes. The server (the store and its
// applier) decides every move; these only shape requests and texts.

export const TOOL_ID = "suggestions";
export const OVERVIEW_ID = "__overview";
export const SUGGESTIONS_KEY = "suggestions";
export const ALL = "all";
export const DEFAULT_VIEW = { kind: ALL, status: ALL };
export const STRUCTURAL = ["split", "merge", "add_page", "add_label"];
export const OPEN = ["pending", "accepted"];

export const STATUS_BADGES = {
  pending: "badge-amber",
  accepted: "badge-sky",
  rejected: "badge-slate",
  applied: "badge-emerald",
};
export const KIND_BADGES = {
  mistake: "badge-rose",
  contradiction: "badge-rose",
  split: "badge-sky",
  merge: "badge-sky",
  add_page: "badge-emerald",
  add_label: "badge-emerald",
};

// Which statuses each action starts from (the store's own moves; the server re-checks).
export const ACTION_FROM = {
  accept: ["pending"],
  reject: ["pending", "accepted"],
  reopen: ["accepted", "rejected"],
  apply: ["accepted"],
};
export const ACTION_LABELS = {
  accept: "קבלה",
  reject: "דחייה",
  reopen: "החזרה להמתנה",
  apply: "ביצוע",
};

/** Open suggestions first (pending, accepted), then decided ones; each in the reviewer's order. */
export function orderItems(items) {
  const open = items.filter((item) => OPEN.includes(item.status));
  return [...open, ...items.filter((item) => !OPEN.includes(item.status))];
}

export function filterItems(items, view) {
  return items.filter(
    (item) => (view.kind === ALL || item.kind === view.kind) && (view.status === ALL || item.status === view.status)
  );
}

/** The options of a filter: "all", then every value present with its Hebrew name and count. */
export function filterOptions(items, field, nameField, allLabel) {
  const counts = new Map();
  for (const item of items) {
    const entry = counts.get(item[field]) ?? { name: item[nameField], count: 0 };
    entry.count += 1;
    counts.set(item[field], entry);
  }
  return [
    { value: ALL, label: `${allLabel} (${items.length})` },
    ...[...counts].map(([value, { name, count }]) => ({ value, label: `${name} (${count})` })),
  ];
}

/** The suggestions among `items` that `action` can move now (not while queued or applying). */
export function actionTargets(items, action) {
  return items.filter((item) => ACTION_FROM[action].includes(item.status) && !item.busy);
}

/** `{count, paid}` of an apply: how many will be carried out, how many of them are model calls. */
export function applyCost(items) {
  return { count: items.length, paid: items.filter((item) => item.paid).length };
}

/** The sentence a paid apply asks to confirm ("3 לביצוע, 2 מהן בתשלום"). */
export function applyQuestion({ count, paid }) {
  const what = count === 1 ? "הצעה אחת לביצוע" : `${count} הצעות לביצוע`;
  const cost = paid === count ? (count === 1 ? "והיא בתשלום" : "כולן בתשלום") : `${paid} מהן בתשלום`;
  return `${what}, ${cost}: כל תיקון טקסט הוא קריאה אחת למודל, שנמשכת דקה עד שתיים. הן ירוצו ברקע, אחת אחרי השנייה, ואפשר להמשיך לעבוד בינתיים.`;
}

/** The effective proposal: the owner's correction if there is one. */
export function proposalOf(suggestion) {
  return suggestion.edited_proposal ?? suggestion.proposal;
}

/**
 * A plan operation from a plan dialog, in the suggestion's proposal shape: the fields the
 * dialogs always send but the review never proposes (`position`, an unchanged `parent_id` or
 * `original_scope`, empty aliases) are left out, so an untouched form equals the proposal.
 */
export function proposalFromOperation(op) {
  const { position, ...rest } = op;
  const body = Object.fromEntries(Object.entries(rest).filter(([, value]) => value !== null && value !== undefined));
  if (body.aliases && !body.aliases.he?.length && !body.aliases.en?.length) delete body.aliases;
  return body;
}

/** The quoted paragraph split around the sentence: [before, sentence, after]. */
export function quoteParts(quote) {
  return [quote.text.slice(0, quote.start), quote.text.slice(quote.start, quote.end), quote.text.slice(quote.end)];
}

/** The newest outcome of the apply queue for one suggestion, or null. */
export function outcomeFor(applies, id) {
  return applies?.outcomes?.find((outcome) => outcome.id === id) ?? null;
}

/** Where a suggestion is in the apply queue: "running" | "queued" | null. */
export function queuePlace(applies, id) {
  if (applies?.running?.id === id) return "running";
  return applies?.queue?.some((entry) => entry.id === id) ? "queued" : null;
}

/** Outcomes that arrived since `seen` (a Set of `${id}@${ended_at}` keys): for toasts. */
export function newOutcomes(applies, seen) {
  return (applies?.outcomes ?? []).filter((outcome) => !seen.has(outcomeKey(outcome)));
}

export function outcomeKey(outcome) {
  return `${outcome.id}@${outcome.ended_at}`;
}

/** "$3.7" */
export function formatUsd(amount) {
  return `$${Number(amount).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}
