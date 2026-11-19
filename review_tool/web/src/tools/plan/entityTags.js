// Pure helpers of the plan tool's "Entity tags" view (no React): the list row, the texts and
// the split of the entities into those to decide and those decided. The rule itself runs on
// the server (step_9_site.entity_tags); these only shape what the owner reads.

export const ENTITY_TAGS_ID = "__entity_tags";
export const ENTITY_TAGS_KEYS = ["plan", "claims", "entities", "pages", "assignments"];

export const DECISION_LABELS = { covered: "Covered by the plan", tag: "Gets a tag" };

export const SOURCE_NOTES = {
  pages: "Pages are the written pages that cite the entity.",
  assignments: "Step 7 has not written pages yet: the pages are estimated from the assignments (step 6b).",
  none: "There are no written pages and no assignments yet: every entity is on no page.",
};

/** The rows of the numbers, in the order the rule decides (labels in English, counts). */
export function countRows(view) {
  const { counts, thresholds } = view;
  return [
    { key: "candidates", label: `Named by at least ${thresholds.min_claims} claims`, count: counts.candidates },
    { key: "few_pages", label: `… on fewer than ${thresholds.min_pages} pages (no tag)`, count: counts.few_pages },
    { key: "covered", label: "… covered by a page or label of the plan", count: counts.covered },
    { key: "uncertain", label: "… waiting for your decision (no tag until then)", count: counts.uncertain },
    { key: "tagged", label: "… get a tag page", count: counts.tagged },
  ];
}

/** The entities with a partial match: those still to decide, then the decided ones. */
export function splitEntities(view) {
  const entities = view?.entities ?? [];
  return {
    toDecide: entities.filter((entity) => !entity.decision),
    decided: entities.filter((entity) => entity.decision),
  };
}

/** How many entities wait for a decision (null while the view is unknown or unavailable). */
export function waitingCount(view) {
  return view && !view.problem ? view.counts.uncertain : null;
}
