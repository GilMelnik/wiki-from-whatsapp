// Pure helpers for the assignment views of the plan tool (no React): the extra rows of the
// list column, the list query, the page picker's choices, the label changes of a bulk edit,
// the proposal forms and the label findings. The server decides what is allowed; these only
// shape requests and texts. Functions that write numbers take the shell's `formatCount` as
// `format` (this file is tested with only the shell's pure helpers: scripts/shell-alias.mjs).

import { groupCount, prefixed, titleOf } from "@shell";
import { percent } from "./plan.js";

export const UNPLACED_ID = "__unplaced";
export const WAITING_ID = "__waiting";
export const PROPOSALS_ID = "__proposals";
export const ASSIGNMENTS_KEY = "assignments";
export const DETAILS_TAB = "details";
export const GROUPS_TAB = "groups";

export const SORT_OPTIONS = [
  { value: "uncertain", label: "הכי פחות בטוחות קודם" },
  { value: "date", label: "החדשות קודם" },
  { value: "support", label: "הכי הרבה תומכים" },
];
export const ROLE_OPTIONS = [
  { value: "all", label: "כל הקבוצות" },
  { value: "primary", label: "שהדף ראשי עבורן" },
  { value: "mention", label: "שרק מזכירות את הדף" },
];
export const DEFAULT_GROUP_VIEW = { sort: "uncertain", role: "all", q: "" };
export const PROPOSAL_KINDS = { page: "דף חדש", label: "תווית חדשה", scope: "הרחבת תחום" };
export const STATUS_LABELS = { pending: "ממתינה", accepted: "התקבלה", rejected: "נדחתה" };

/** The rows the tree list shows above the tree: groups on no page, waiting, proposals. */
export function assignmentRows(overview) {
  const summary = overview?.assignments;
  const rows = [{ id: UNPLACED_ID, kind: "unplaced" }];
  if (summary?.counts?.waiting > 0) rows.push({ id: WAITING_ID, kind: "waiting" });
  rows.push({ id: PROPOSALS_ID, kind: "proposals" });
  return rows;
}

export function isAssignmentRow(id) {
  return id === UNPLACED_ID || id === WAITING_ID || id === PROPOSALS_ID;
}

/** The query of GET /assignments/groups (unset values left out by the api client). */
export function groupsQuery(scope, itemId, view, offset) {
  return { scope, id: itemId ?? undefined, role: scope === "page" ? view.role : undefined, sort: view.sort, q: view.q.trim() || undefined, offset };
}

/** Page ids in tree order. */
export function pageIdsInOrder(overview) {
  return (overview?.nodes ?? []).filter((node) => node.kind === "page").map((node) => node.id);
}

/**
 * The page picker's choices for the picked rows: `considered` = the pages the classifier
 * gave some probability to (p ≥ the kept floor) for any of them, most probable first, with
 * the mean probability over the picked rows and how many of them considered the page; then
 * `others` = every other page in tree order. `query` filters both by title, id or path.
 */
export function pageChoices(overview, rows, query = "", pathOf = () => "") {
  const totals = {};
  for (const row of rows) {
    for (const [pageId, probability] of row.p ?? []) {
      if (!overview.pages[pageId]) continue;
      totals[pageId] ??= { sum: 0, count: 0 };
      totals[pageId].sum += probability;
      totals[pageId].count += 1;
    }
  }
  const needle = query.trim().toLowerCase();
  const fits = (id) =>
    !needle || [titleOf(overview, id), id, pathOf(id)].some((text) => text.toLowerCase().includes(needle));
  const considered = Object.entries(totals)
    .map(([id, { sum, count }]) => ({ id, mean: sum / Math.max(rows.length, 1), count }))
    .sort((a, b) => b.mean - a.mean)
    .filter(({ id }) => fits(id));
  const seen = new Set(Object.keys(totals));
  const others = pageIdsInOrder(overview).filter((id) => !seen.has(id) && fits(id));
  return { considered, others };
}

/** "all" | "some" | "none": how many of the rows carry the label. */
export function labelShare(rows, labelId) {
  const count = rows.filter((row) => (row.labels ?? []).includes(labelId)).length;
  if (count === 0) return "none";
  return count === rows.length ? "all" : "some";
}

/** The label a click in the labels dialog sets: what is fully on goes off, anything else on. */
export function nextLabelTarget(initial, target) {
  const shown = target ?? initial;
  return shown === "all" ? "none" : "all";
}

/** The changes for POST /assignments/edit from `{labelId: "all" | "none"}` targets. */
export function labelChanges(initial, targets) {
  return Object.entries(targets)
    .filter(([labelId, target]) => target !== initial[labelId])
    .map(([labelId, target]) => ({ action: target === "all" ? "add_label" : "remove_label", label_id: labelId }));
}

/** The categories a new page can go in, as plan rows (tree order). */
export function categoryIds(overview) {
  return (overview?.nodes ?? []).filter((node) => node.kind === "category").map((node) => node.id);
}

/** Initial form values of the dialog that accepts `proposal` (the owner corrects them). */
export function proposalForm(proposal, overview) {
  const categories = categoryIds(overview);
  return {
    id: proposal.target_id ?? "",
    title: proposal.title_he ?? "",
    description: proposal.description ?? "",
    scope: proposal.scope ?? "",
    parentId: categories.includes(proposal.parent_id) ? proposal.parent_id : (categories[0] ?? null),
    kind: proposal.label_kind ?? "",
  };
}

/** A label that attaches to very few groups or to a very large share, in Hebrew. */
export function findingText(finding, overview, format = String) {
  const title = `„${titleOf(overview, finding.label_id)}”`;
  if (finding.finding === "large") {
    return `התווית ${title} מודבקת ל-${percent(finding.share)} מהקבוצות (${format(finding.count)}): כמסנן היא כמעט לא מצמצמת.`;
  }
  return `התווית ${title} מודבקת רק ${prefixed("ל", finding.count, format)}: אולי היא צרה מדי, או שהתיאור שלה לא ברור למסווג.`;
}
