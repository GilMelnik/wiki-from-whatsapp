import assert from "node:assert/strict";
import { test } from "node:test";
import { pageStart } from "../src/shell/pagedSelection.js";
import {
  dateRange,
  marksToRestore,
  restoreLabel,
  toggleMark,
  typeCounts,
} from "../src/tools/pii/review.js";

test("the list page follows the selected claim's queue position", () => {
  assert.equal(pageStart(0, 100), 0);
  assert.equal(pageStart(99, 100), 0);
  assert.equal(pageStart(100, 100), 100);
  assert.equal(pageStart(257, 100), 200);
});

test("choosing marks to restore toggles them and keeps them sorted", () => {
  assert.deepEqual(toggleMark([], 2), [2]);
  assert.deepEqual(toggleMark([2], 0), [0, 2]);
  assert.deepEqual(toggleMark([0, 2], 2), [0]);
});

test("no chosen mark means every mark is restored", () => {
  assert.deepEqual(marksToRestore([], 3), [0, 1, 2]);
  assert.deepEqual(marksToRestore([1], 3), [1]);
  assert.equal(restoreLabel(0, 1), "שחזור הפרט");
  assert.equal(restoreLabel(0, 3), "שחזור כל הפרטים");
  assert.equal(restoreLabel(3, 3), "שחזור כל הפרטים");
  assert.equal(restoreLabel(1, 3), "שחזור 1 מתוך 3 הפרטים");
});

test("dates and type counts read well", () => {
  assert.equal(dateRange("2024-01-05", "2024-01-05"), "2024-01-05");
  assert.equal(dateRange("2024-01-05", "2024-02-01"), "2024-01-05 – 2024-02-01");
  assert.equal(dateRange(null, null), "—");
  assert.equal(typeCounts({ email: 1, phone: 2 }), "דוא״ל 1 · טלפון 2");
  assert.equal(typeCounts({}), "");
});
