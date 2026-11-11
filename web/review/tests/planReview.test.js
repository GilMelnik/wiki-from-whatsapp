import assert from "node:assert/strict";
import { test } from "node:test";
import {
  consequenceLines,
  kindOf,
  labelItems,
  moveTargets,
  normalizeId,
  pathTitles,
  subtreeIds,
  textToAliases,
  titleOf,
} from "../src/shell/planText.js";
import { OVERVIEW_ID, percent, reorderOp, treeItems } from "../src/tools/plan/plan.js";

// A synthetic overview in the shape GET /api/plan/plan answers.
const row = (id, kind, depth, parent, index, siblings, children = []) => ({
  id, kind, title_he: `כותרת ${id}`, description: "", depth, parent_id: parent, index, siblings, children, page_count: 0,
});
const OVERVIEW = {
  nodes: [
    row("before", "category", 1, null, 0, 2, ["choosing"]),
    row("choosing", "category", 2, "before", 0, 1, ["agencies", "costs"]),
    row("agencies", "page", 3, "choosing", 0, 2),
    row("costs", "page", 3, "choosing", 1, 2),
    row("during", "category", 1, null, 1, 2, ["medical"]),
    row("medical", "category", 2, "during", 0, 1, []),
  ],
  pages: { agencies: { title_he: "סוכנויות" }, costs: { title_he: "עלויות" } },
  labels: { usa: { title_he: "ארה״ב", kind: "country" }, twins: { title_he: "תאומים", kind: "family" }, georgia: { title_he: "גאורגיה", kind: "country" } },
  label_kinds: [{ kind: "country", label_ids: ["usa", "georgia"] }, { kind: "family", label_ids: ["twins"] }],
};
const NO_EDIT = { created: [], removed_pages: [], removed_labels: [], redirects: {}, reassign: [] };

test("list items: the overview row first, labels grouped by kind", () => {
  assert.deepEqual(treeItems(OVERVIEW).map((item) => item.id), [OVERVIEW_ID, "before", "choosing", "agencies", "costs", "during", "medical"]);
  const labels = labelItems(OVERVIEW);
  assert.deepEqual(labels.map((item) => [item.id, item.labelKind, item.first]), [
    ["usa", "country", true],
    ["georgia", "country", false],
    ["twins", "family", true],
  ]);
  assert.deepEqual(treeItems(undefined).map((item) => item.id), [OVERVIEW_ID]);
});

test("kinds, titles and paths come from the plan", () => {
  assert.equal(kindOf(OVERVIEW, "costs"), "page");
  assert.equal(kindOf(OVERVIEW, "before"), "category");
  assert.equal(kindOf(OVERVIEW, "usa"), "label");
  assert.equal(kindOf(OVERVIEW, "ghost"), null);
  assert.equal(titleOf(OVERVIEW, "costs"), "עלויות");
  assert.equal(titleOf(OVERVIEW, "medical"), "כותרת medical");
  assert.equal(titleOf(OVERVIEW, "ghost"), "ghost");
  assert.equal(titleOf(OVERVIEW, null), "הרמה העליונה");
  assert.deepEqual(pathTitles(OVERVIEW, "costs"), ["כותרת before", "כותרת choosing"]);
});

test("a node can move into any category outside its own subtree", () => {
  assert.deepEqual(subtreeIds(OVERVIEW, "before"), ["before", "choosing", "agencies", "costs"]);
  assert.deepEqual(moveTargets(OVERVIEW, "before").map((node) => node.id), ["during", "medical"]);
  assert.deepEqual(moveTargets(OVERVIEW, "costs").map((node) => node.id), ["before", "choosing", "during", "medical"]);
});

test("reordering swaps with a neighbour and stops at the ends", () => {
  const costs = OVERVIEW.nodes[3];
  assert.deepEqual(reorderOp(costs, -1), { op: "move", item_id: "costs", parent_id: "choosing", position: 0 });
  assert.equal(reorderOp(costs, 1), null);
  assert.deepEqual(reorderOp(OVERVIEW.nodes[2], 1), { op: "move", item_id: "agencies", parent_id: "choosing", position: 1 });
  assert.equal(reorderOp(null, 1), null);
});

test("typed ids are nudged towards slugs; aliases are one per line", () => {
  assert.equal(normalizeId("Surrogate Gifts"), "surrogate-gifts");
  assert.deepEqual(textToAliases(" א \n\nב\nא\n"), ["א", "ב"]);
  assert.equal(percent(0.9366), "94%");
  assert.equal(percent(null), "—");
});

test("consequences name what disappears, what is redirected and what is classified again", () => {
  const merge = consequenceLines({ ...NO_EDIT, removed_pages: ["costs"], redirects: { costs: "agencies", old: "agencies" } }, OVERVIEW);
  assert.equal(merge.length, 2);
  assert.match(merge[0].text, /„עלויות” \(costs\) ייעלם.*„סוכנויות”/);
  assert.equal(merge[0].tone, "warning");
  assert.match(merge[1].text, /של old יופנו אל „סוכנויות”/);

  const deleted = consequenceLines({ ...NO_EDIT, removed_pages: ["costs"] }, OVERVIEW);
  assert.equal(deleted[0].tone, "danger");
  assert.match(deleted[0].text, /יימחק.*שיוך חדש/);

  const split = consequenceLines({ ...NO_EDIT, created: ["labs"], reassign: ["costs"] }, OVERVIEW);
  assert.match(split[0].text, /„עלויות” יסווגו מחדש/);
  assert.match(split[1].text, /מזהה חדש: labs/);

  const renamedId = consequenceLines({ ...NO_EDIT, created: ["fees"], redirects: { costs: "fees" } }, OVERVIEW);
  assert.equal(renamedId.length, 1);
  assert.match(renamedId[0].text, /של costs יופנו אל המזהה החדש fees/);

  assert.deepEqual(consequenceLines(NO_EDIT, OVERVIEW), [{ tone: "info", text: "אין השלכה על שיוך הטענות." }]);
  assert.deepEqual(consequenceLines(null, OVERVIEW), []);
});
