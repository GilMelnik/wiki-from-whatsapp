import assert from "node:assert/strict";
import { test } from "node:test";
import {
  barPercent,
  dateRange,
  DEFAULT_VIEW,
  entityOptions,
  followSuggestion,
  formatScore,
  pickedIn,
  togglePick,
  viewParams,
} from "../src/tools/groups/groups.js";

const items = [{ key: "a|b" }, { key: "c|d" }, { key: "e|f" }];

test("after a decision the next suggestion takes the place of the decided one", () => {
  assert.equal(followSuggestion(items, "c|d", 1), "c|d"); // still listed: stays
  const afterMerge = [{ key: "a|b" }, { key: "e|f" }];
  assert.equal(followSuggestion(afterMerge, "c|d", 1), "e|f");
  assert.equal(followSuggestion([{ key: "a|b" }], "c|d", 1), "a|b"); // list got shorter
  assert.equal(followSuggestion(items, null, 0), "a|b");
  assert.equal(followSuggestion([], "a|b", 0), null);
});

test("the list query leaves out unset filters", () => {
  assert.deepEqual(viewParams(DEFAULT_VIEW), { sort: "size", order: "desc" });
  assert.deepEqual(viewParams({ ...DEFAULT_VIEW, multi: true, q: "x" }), { multi: true, q: "x", sort: "size", order: "desc" });
});

test("picked claims belong to one group", () => {
  let picked = { groupId: null, claimIds: [] };
  picked = togglePick(picked, "g-1", "c1");
  picked = togglePick(picked, "g-1", "c2");
  assert.deepEqual(pickedIn(picked, "g-1"), ["c1", "c2"]);
  assert.deepEqual(pickedIn(picked, "g-2"), []);
  assert.deepEqual(togglePick(picked, "g-1", "c1").claimIds, ["c2"]);
  assert.deepEqual(togglePick(picked, "g-2", "c9"), { groupId: "g-2", claimIds: ["c9"] });
});

test("scores, dates, bars and entity options are formatted for the page", () => {
  assert.equal(formatScore(0.93214), "0.932");
  assert.equal(formatScore(-0.0181, { signed: true }), "−0.018");
  assert.equal(formatScore(0.002, { signed: true }), "+0.002");
  assert.equal(dateRange("2024-01-01", "2024-01-01"), "2024-01-01");
  assert.equal(dateRange("2024-01-01", "2024-03-01"), "2024-01-01 – 2024-03-01");
  assert.equal(dateRange(null, null), "—");
  assert.equal(barPercent(0, 10), 0);
  assert.equal(barPercent(1, 100), 4);
  assert.equal(barPercent(10, 10), 100);
  assert.deepEqual(entityOptions([{ entity_id: "e1", name: "Alpha", groups: 2 }]), [
    { value: "", label: "כל הישויות" },
    { value: "e1", label: "Alpha (2)" },
  ]);
});
