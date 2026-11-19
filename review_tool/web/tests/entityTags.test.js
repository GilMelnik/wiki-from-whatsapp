import assert from "node:assert/strict";
import { test } from "node:test";
import { countRows, splitEntities, waitingCount } from "../src/tools/plan/entityTags.js";

const VIEW = {
  problem: null,
  source: "assignments",
  thresholds: { min_claims: 6, min_pages: 2 },
  counts: { candidates: 9, few_pages: 3, covered: 2, uncertain: 1, tagged: 3 },
  entities: [
    { entity_id: "e0001", decision: null },
    { entity_id: "e0002", decision: "tag" },
    { entity_id: "e0003", decision: "covered" },
  ],
};

test("the numbers follow the rule's order and name the thresholds", () => {
  const rows = countRows(VIEW);
  assert.deepEqual(rows.map((row) => row.key), ["candidates", "few_pages", "covered", "uncertain", "tagged"]);
  assert.deepEqual(rows.map((row) => row.count), [9, 3, 2, 1, 3]);
  assert.match(rows[0].label, /at least 6 claims/);
  assert.match(rows[1].label, /fewer than 2 pages/);
});

test("the entities split into those to decide and those decided", () => {
  const { toDecide, decided } = splitEntities(VIEW);
  assert.deepEqual(toDecide.map((entity) => entity.entity_id), ["e0001"]);
  assert.deepEqual(decided.map((entity) => entity.entity_id), ["e0002", "e0003"]);
  assert.deepEqual(splitEntities(undefined), { toDecide: [], decided: [] });
});

test("the list row counts the waiting entities only when the view is there", () => {
  assert.equal(waitingCount(VIEW), 1);
  assert.equal(waitingCount(undefined), null);
  assert.equal(waitingCount({ problem: "no plan" }), null);
});
