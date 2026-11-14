// Labels and pure helpers of the claim-groups tool (no React, so node --test can import them).

import { countText, isolate, quote, STANCES, stanceOf } from "@shell";

export const PAGE_SIZE = 100;
export const SUGGESTION_PAGE_SIZE = 50;
export const SEARCH_DEBOUNCE_MS = 250;
export const PICKER_LIMIT = 30;

export const MODES = [
  { value: "suggestions", label: "Merge suggestions", key: "p" },
  { value: "groups", label: "All groups", key: "g" },
  { value: "withheld", label: "Withheld from the wiki", key: "w" },
];
// The view each list mode starts from; the withheld list always holds only withheld groups.
export const WITHHELD_MODE = "withheld";

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
  withheld: false,
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

// Where a contradiction link between two groups comes from (server: review_tool/tools/groups/disputes.py).
export const ORIGIN_LABELS = {
  conversation: "A member answered another in a conversation and disputed it",
  wording: "Similar wording with opposite stances about the same entity",
  manual: "Linked by you",
};

// Two claim texts longer than this (in word pairs compared) are shown without highlights.
const MAX_DIFF_CELLS = 40000;

const words = (text) => (text ?? "").split(/\s+/).filter(Boolean);
const normal = (word) => word.replace(/[^\p{L}\p{N}]/gu, "").toLowerCase();

function segments(tokens, same) {
  const out = [];
  tokens.forEach((token, index) => {
    const text = index < tokens.length - 1 ? `${token} ` : token;
    const last = out[out.length - 1];
    if (last && last.same === same[index]) last.text += text;
    else out.push({ text, same: same[index] });
  });
  return out;
}

/**
 * The words two texts share and the words that differ, for highlighting:
 * `{a: [{text, same}], b: [{text, same}]}` (a longest common run of words, compared without
 * punctuation or case). Joined, each side's segments give its words with single spaces.
 */
export function diffWords(a, b) {
  const x = words(a);
  const y = words(b);
  if (x.length * y.length > MAX_DIFF_CELLS) {
    return { a: [{ text: x.join(" "), same: true }], b: [{ text: y.join(" "), same: true }] };
  }
  const nx = x.map(normal);
  const ny = y.map(normal);
  const table = Array.from({ length: x.length + 1 }, () => new Uint16Array(y.length + 1));
  for (let i = x.length - 1; i >= 0; i -= 1) {
    for (let j = y.length - 1; j >= 0; j -= 1) {
      table[i][j] = nx[i] === ny[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const sameA = x.map(() => false);
  const sameB = y.map(() => false);
  let i = 0;
  let j = 0;
  while (i < x.length && j < y.length) {
    if (nx[i] === ny[j]) {
      sameA[i] = true;
      sameB[j] = true;
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) i += 1;
    else j += 1;
  }
  return { a: segments(x, sameA), b: segments(y, sameB) };
}

/** Why the pair was suggested, in plain words (no similarity numbers). */
export function whySuggested(pair) {
  const lines = [
    "Every claim of one group is worded close to every claim of the other, but not close enough for step 5 to merge them by itself.",
  ];
  if (pair.shared_entity_names?.length) {
    lines.push(`Both name ${pair.shared_entity_names.map(quote).join(", ")}.`);
  } else {
    lines.push("They name no entity in common, so step 5 asks for closer wording before it merges.");
  }
  const stanceA = stanceOf(pair.a.stance).label;
  const stanceB = stanceOf(pair.b.stance).label;
  lines.push(stanceA === stanceB ? `Both have the same stance: ${stanceA.toLowerCase()}.` : `Their stances differ: ${stanceA.toLowerCase()} and ${stanceB.toLowerCase()}.`);
  return lines;
}

/**
 * What a merge would do to the rule that keeps lone negative reports off the wiki, from
 * GET /merge-check: `{tone, text}` or null when no group involved is or would be withheld.
 */
export function releaseNote(check, minSupporters) {
  if (!check || check.withheld_after === null) return null;
  const now = check.withheld.length;
  if (now === 0 && !check.withheld_after) return null;
  if (now === 0) {
    return {
      tone: "warning",
      text: `The merged group would be withheld from the wiki: a negative statement that names an entity, reported by fewer than ${countText(minSupporters, "member")}.`,
    };
  }
  if (check.withheld_after) {
    return { tone: "info", text: `It stays withheld from the wiki: the merged group would still have ${countText(check.supporter_count_after, "supporter")} (it needs ${minSupporters}).` };
  }
  return { tone: "success", text: `This merge releases it to the wiki: the merged group has ${countText(check.supporter_count_after, "supporter")}.` };
}
