// Pure helpers of the plan tool (no React): list items and the operations the buttons send.
// Titles, ids and the wording of what a change means downstream are the shell's
// (`shell/planText.js`), shared with the suggestions tool. The server decides what is allowed
// (every operation is validated there); these only shape requests and texts.

export const TOOL_ID = "plan";
export const OVERVIEW_ID = "__overview";
export const TREE_VIEW = "tree";
export const LABELS_VIEW = "labels";

export const KIND_LABELS = { page: "דף", category: "קטגוריה", label: "תווית" };
/** The rows of the tree list: the overview row, then every node in display order. */
export function treeItems(overview) {
  return [{ id: OVERVIEW_ID, kind: "overview" }, ...(overview?.nodes ?? [])];
}

/** The move that swaps a node with its neighbour (`step` -1 = up, +1 = down), or null. */
export function reorderOp(node, step) {
  if (!node) return null;
  const position = node.index + step;
  if (position < 0 || position >= node.siblings) return null;
  return { op: "move", item_id: node.id, parent_id: node.parent_id, position };
}

/** "new" | [changed field names] | undefined: what changed since the model's proposal. */
export function changeOf(overview, id) {
  return overview?.changes?.items?.[id];
}

export function warningsFor(overview, id) {
  return (overview?.warnings ?? []).filter((warning) => warning.ids.includes(id));
}

export function aliasesToText(aliases) {
  return (aliases ?? []).join("\n");
}

/** The share 0..1 as a whole percent ("94%"). */
export function percent(share) {
  return share === null || share === undefined ? "—" : `${Math.round(share * 100)}%`;
}
