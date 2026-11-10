// Pure helpers of the plan tool (no React): list items, titles, the operations the buttons
// send, and the Hebrew wording of what a change means downstream. The server decides what is
// allowed (every operation is validated there); these only shape requests and texts.

export const TOOL_ID = "plan";
export const OVERVIEW_ID = "__overview";
export const TREE_VIEW = "tree";
export const LABELS_VIEW = "labels";
export const PREVIEW_DEBOUNCE_MS = 250;
export const ID_CHECK_DEBOUNCE_MS = 200;

export const KIND_LABELS = { page: "דף", category: "קטגוריה", label: "תווית" };
export const PERMANENT_ID_NOTE =
  "המזהה הוא כתובת הדף בוויקי והמפתח שכל טענה משויכת אליו, ולכן הוא קבוע: אפשר רק להחליף אותו במזהה חדש שמפנה מהישן.";

/** The rows of the tree list: the overview row, then every node in display order. */
export function treeItems(overview) {
  return [{ id: OVERVIEW_ID, kind: "overview" }, ...(overview?.nodes ?? [])];
}

/** The rows of the labels list, grouped by kind (`first` marks a kind's first row). */
export function labelItems(overview) {
  return (overview?.label_kinds ?? []).flatMap(({ kind, label_ids: ids }) =>
    ids.map((id, index) => ({ id, kind: "label", labelKind: kind, first: index === 0, ...overview.labels[id] }))
  );
}

export function nodeById(overview, id) {
  return overview?.nodes?.find((node) => node.id === id) ?? null;
}

/** "page" | "category" | "label" | null for an id of the current plan. */
export function kindOf(overview, id) {
  if (!overview || !id) return null;
  if (overview.labels?.[id]) return "label";
  return nodeById(overview, id)?.kind ?? null;
}

export function titleOf(overview, id) {
  if (id === null || id === undefined) return "הרמה העליונה";
  return overview?.pages?.[id]?.title_he ?? overview?.labels?.[id]?.title_he ?? nodeById(overview, id)?.title_he ?? id;
}

/** The titles of a node's ancestors, top level first. */
export function pathTitles(overview, id) {
  const titles = [];
  for (let node = nodeById(overview, id); node?.parent_id; node = nodeById(overview, node.parent_id)) {
    titles.unshift(titleOf(overview, node.parent_id));
  }
  return titles;
}

/** Ids of a node and everything under it. */
export function subtreeIds(overview, id) {
  const node = nodeById(overview, id);
  if (!node) return [];
  return [id, ...node.children.flatMap((child) => subtreeIds(overview, child))];
}

/** The categories an item could be moved into (never itself or its own subtree). */
export function moveTargets(overview, id) {
  const inside = new Set(subtreeIds(overview, id));
  return (overview?.nodes ?? []).filter((node) => node.kind === "category" && !inside.has(node.id));
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

/** One alias per line; blank lines dropped, duplicates removed. */
export function textToAliases(text) {
  return [...new Set(text.split("\n").map((line) => line.trim()).filter(Boolean))];
}

/** What the owner types as an id, nudged towards a slug (lower case, dashes for spaces). */
export function normalizeId(text) {
  return text.toLowerCase().replace(/\s+/g, "-");
}

function names(overview, ids) {
  return ids.map((id) => `„${titleOf(overview, id)}”`).join(", ");
}

/**
 * What a change means downstream, as Hebrew lines `{tone, text}` (tone: info | warning |
 * danger), from the server's `edit` (created, removed_pages, removed_labels, redirects,
 * reassign). `overview` is the plan before the change, for titles.
 */
export function consequenceLines(edit, overview) {
  if (!edit) return [];
  const lines = [];
  const redirected = (id) => edit.redirects[id];
  const removed = (ids, what) => {
    const followed = ids.filter(redirected);
    const homeless = ids.filter((id) => !redirected(id));
    for (const id of followed) {
      lines.push({
        tone: "warning",
        text: `${what} „${titleOf(overview, id)}” (${id}) ייעלם. הטענות והקישורים שלו יעברו אל „${titleOf(overview, redirected(id))}”.`,
      });
    }
    if (homeless.length > 0) {
      lines.push({
        tone: "danger",
        text: `${homeless.length === 1 ? `${what} ${names(overview, homeless)} יימחק` : `יימחקו: ${names(overview, homeless)}`}. המזהה יוצא משימוש לתמיד, והטענות ששויכו אליו יצטרכו שיוך חדש.`,
      });
    }
  };
  removed(edit.removed_pages, "הדף");
  removed(edit.removed_labels, "התווית");
  const renamed = Object.entries(edit.redirects).filter(
    ([old]) => !edit.removed_pages.includes(old) && !edit.removed_labels.includes(old)
  );
  for (const [old, target] of renamed) {
    lines.push({
      tone: "info",
      text: edit.created.includes(target)
        ? `שיוכים וקישורים ישנים של ${old} יופנו אל המזהה החדש ${target}; ${old} יוצא משימוש לתמיד.`
        : `קישורים ושיוכים ישנים של ${old} יופנו אל „${titleOf(overview, target)}”.`,
    });
  }
  if (edit.reassign.length > 0) {
    lines.push({
      tone: "warning",
      text: `הטענות של ${names(overview, edit.reassign)} יסווגו מחדש בשלב השיוך, לפי התחומים החדשים.`,
    });
  }
  const fresh = edit.created.filter((id) => !Object.values(edit.redirects).includes(id));
  if (fresh.length > 0) {
    lines.push({ tone: "info", text: `ייווצר מזהה חדש: ${fresh.join(", ")}. ${PERMANENT_ID_NOTE}` });
  }
  if (lines.length === 0) lines.push({ tone: "info", text: "אין השלכה על שיוך הטענות." });
  return lines;
}

/** The share 0..1 as a whole percent ("94%"). */
export function percent(share) {
  return share === null || share === undefined ? "—" : `${Math.round(share * 100)}%`;
}
