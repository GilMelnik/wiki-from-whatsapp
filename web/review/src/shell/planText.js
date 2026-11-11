// Pure helpers about a wiki plan (no React), shared by every tool that shows or edits a plan
// operation (the plan tool, the suggestions tool): titles and places of plan items, ids as
// the owner types them, and the Hebrew wording of what an operation means downstream for the
// plan (`consequenceLines`, from the server's `edit`) and for the claim groups at the next
// assignment run (`assignmentLines`, from a preview's `assignments`). The server decides what
// is allowed; these only shape requests and texts. Functions that write numbers take the
// shell's `formatCount` as `format` (tests run them without the shell).

export const PREVIEW_DEBOUNCE_MS = 250;
export const ID_CHECK_DEBOUNCE_MS = 200;

export const PERMANENT_ID_NOTE =
  "המזהה הוא כתובת הדף בוויקי והמפתח שכל טענה משויכת אליו, ולכן הוא קבוע: אפשר רק להחליף אותו במזהה חדש שמפנה מהישן.";


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


/** "קבוצה אחת" / "12 קבוצות". */
export function groupCount(count, format = String) {
  return count === 1 ? "קבוצה אחת" : `${format(count)} קבוצות`;
}

/** `groupCount` after a one-letter prefix ("מ", "ל"): a hyphen before digits ("ל-12 קבוצות"). */
export function prefixed(prefix, count, format) {
  return `${prefix}${count === 1 ? "" : "-"}${groupCount(count, format)}`;
}


/**
 * What a plan change does to the claim groups at the next assignment run, as Hebrew lines
 * `{tone, text}`, from the preview's `assignments` (null before step 6b has run).
 */
export function assignmentLines(effect, overview, format = String) {
  if (!effect) return [];
  const lines = [];
  const count = (n) => groupCount(n, format);
  const names = (ids) => ids.map((id) => `„${titleOf(overview, id)}”`).join(", ");
  if (effect.redirected > 0) {
    lines.push({ tone: "info", text: `השיוך של ${count(effect.redirected)} יעבור אל היעד החדש בהרצת השיוך הבאה, בלי לשאול שוב.` });
  }
  if (effect.reask > 0) {
    lines.push({ tone: "warning", text: `בהרצת השיוך הבאה המסווג יחליט מחדש על הדף של ${count(effect.reask)} (בתשלום).` });
  }
  if (effect.screen_pages.length > 0) {
    lines.push({ tone: "info", text: `על שאר הקבוצות המסווג ישאל שאלת כן/לא זולה: האם הן שייכות ל${names(effect.screen_pages)}.` });
  }
  if (effect.asked_labels.length > 0) {
    lines.push({ tone: "info", text: `המסווג ישאל על כל הקבוצות אם התווית ${names(effect.asked_labels)} מתאימה להן.` });
  }
  if (effect.pages_cleared > 0) {
    lines.push({ tone: "warning", text: `הדף שנמחק יוסר מהשיוך של ${count(effect.pages_cleared)}.` });
  }
  if (effect.labels_cleared > 0) {
    lines.push({ tone: "warning", text: `התווית שנמחקה תוסר מהשיוך של ${count(effect.labels_cleared)}.` });
  }
  if (effect.manual_lost > 0) {
    const back = effect.manual_reask > 0 ? ` השיוך הידני של ${count(effect.manual_reask)} יתבטל, כי לא יישאר להן דף, והמסווג יחליט עליהן מחדש.` : "";
    lines.push({ tone: "danger", text: `הדף שנמחק יוסר גם ${prefixed("מ", effect.manual_lost, format)} ששייכתם ביד.${back}` });
  }
  if (lines.length > 0) {
    lines.push({ tone: "info", text: "הכלי לא משנה את השיוך עכשיו: שלב השיוך יעשה זאת בהרצה הבאה, וההודעה למעלה תציע אותה." });
  }
  return lines;
}
