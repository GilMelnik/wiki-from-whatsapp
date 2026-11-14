// Pure helpers about a wiki plan (no React), shared by every tool that shows or edits a plan
// operation (the plan tool, the suggestions tool): titles and places of plan items, ids as
// the owner types them, counts in words, and the English wording of what an operation means downstream for the
// plan (`consequenceLines`, from the server's `edit`) and for the claim groups at the next
// assignment run (`assignmentLines`, from a preview's `assignments`). The server decides what
// is allowed; these only shape requests and texts. Functions that write numbers take the
// shell's `formatCount` as `format` (tests run them without the shell). A Hebrew title inside
// an English sentence is `quote`d: English quotes around a bidi isolate (U+2068 … U+2069), so
// the title keeps its own direction and does not reorder the words, numbers or commas around it.
import { formatCount } from "./format.js";

export const PREVIEW_DEBOUNCE_MS = 250;
export const ID_CHECK_DEBOUNCE_MS = 200;

export const PERMANENT_ID_NOTE =
  "The id is the page's address in the wiki and the key every claim is assigned to, so it is permanent: it can only be replaced by a new id that the old one redirects to.";

/** Content inside an English string, in a first-strong isolate (its own direction). */
export function isolate(text) {
  return `\u2068${text}\u2069`;
}

/** A title (or any content) inside an English sentence: “title”, isolated. */
export function quote(text) {
  return `“${isolate(text)}”`;
}

/** "1 claim" / "12 claims" (`plural` for a noun that does not just take an "s"). */
export function countText(count, singular, format = formatCount, plural = `${singular}s`) {
  return `${format(count)} ${count === 1 ? singular : plural}`;
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
  if (id === null || id === undefined) return "Top level";
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
  return ids.map((id) => quote(titleOf(overview, id))).join(", ");
}

/**
 * What a change means downstream, as English lines `{tone, text}` (tone: info | warning |
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
        text: `${what} ${quote(titleOf(overview, id))} (${id}) will disappear. Its claims and links will move to ${quote(titleOf(overview, redirected(id)))}.`,
      });
    }
    if (homeless.length > 0) {
      lines.push({
        tone: "danger",
        text:
          homeless.length === 1
            ? `${what} ${names(overview, homeless)} will be deleted. Its id is retired for good, and the claims assigned to it will need a new assignment.`
            : `These will be deleted: ${names(overview, homeless)}. Their ids are retired for good, and the claims assigned to them will need a new assignment.`,
      });
    }
  };
  removed(edit.removed_pages, "The page");
  removed(edit.removed_labels, "The label");
  const renamed = Object.entries(edit.redirects).filter(
    ([old]) => !edit.removed_pages.includes(old) && !edit.removed_labels.includes(old)
  );
  for (const [old, target] of renamed) {
    lines.push({
      tone: "info",
      text: edit.created.includes(target)
        ? `Old assignments of and links to ${old} will be redirected to the new id ${target}; ${old} is retired for good.`
        : `Old links to and assignments of ${old} will be redirected to ${quote(titleOf(overview, target))}.`,
    });
  }
  if (edit.reassign.length > 0) {
    lines.push({
      tone: "warning",
      text: `The claims of ${names(overview, edit.reassign)} will be classified again at the assignment step, by the new scopes.`,
    });
  }
  const fresh = edit.created.filter((id) => !Object.values(edit.redirects).includes(id));
  if (fresh.length > 0) {
    lines.push({ tone: "info", text: `${fresh.length === 1 ? "A new id will be created" : "New ids will be created"}: ${fresh.join(", ")}. ${PERMANENT_ID_NOTE}` });
  }
  if (lines.length === 0) lines.push({ tone: "info", text: "No effect on how the claims are assigned." });
  return lines;
}


/** "1 group" / "12 groups". */
export function groupCount(count, format = String) {
  return countText(count, "group", format);
}


/**
 * What a plan change does to the claim groups at the next assignment run, as English lines
 * `{tone, text}`, from the preview's `assignments` (null before step 6b has run).
 */
export function assignmentLines(effect, overview, format = String) {
  if (!effect) return [];
  const lines = [];
  const count = (n) => groupCount(n, format);
  if (effect.redirected > 0) {
    lines.push({ tone: "info", text: `The assignment of ${count(effect.redirected)} will move to the new target at the next assignment run, without asking again.` });
  }
  if (effect.reask > 0) {
    lines.push({ tone: "warning", text: `At the next assignment run the classifier will decide the page of ${count(effect.reask)} again (paid).` });
  }
  if (effect.screen_pages.length > 0) {
    lines.push({ tone: "info", text: `For the other groups the classifier will ask a cheap yes/no question: do they belong on ${names(overview, effect.screen_pages)}?` });
  }
  if (effect.asked_labels.length > 0) {
    lines.push({ tone: "info", text: `The classifier will ask of every group whether the label ${names(overview, effect.asked_labels)} fits it.` });
  }
  if (effect.pages_cleared > 0) {
    lines.push({ tone: "warning", text: `The deleted page will be taken off the assignment of ${count(effect.pages_cleared)}.` });
  }
  if (effect.labels_cleared > 0) {
    lines.push({ tone: "warning", text: `The deleted label will be taken off the assignment of ${count(effect.labels_cleared)}.` });
  }
  if (effect.manual_lost > 0) {
    const back = effect.manual_reask > 0 ? ` The manual assignment of ${count(effect.manual_reask)} will be cancelled, because they will have no page left, and the classifier will decide on them again.` : "";
    lines.push({ tone: "danger", text: `The deleted page will also be taken off ${count(effect.manual_lost)} you assigned by hand.${back}` });
  }
  if (lines.length > 0) {
    lines.push({ tone: "info", text: "The tool does not change the assignment now: the assignment step does it at its next run, which the banner at the top will offer." });
  }
  return lines;
}
