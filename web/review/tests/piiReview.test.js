import assert from "node:assert/strict";
import { test } from "node:test";
import { pageStart } from "../src/shell/pagedSelection.js";
import {
  dateRange,
  marksToRestore,
  messagesHolding,
  otherMessages,
  removedText,
  restoreLabel,
  sectionOf,
  toggleMark,
} from "../src/tools/pii/review.js";

test("the list page follows the selected claim's queue position", () => {
  assert.equal(pageStart(0, 100), 0);
  assert.equal(pageStart(99, 100), 0);
  assert.equal(pageStart(100, 100), 100);
  assert.equal(pageStart(257, 100), 200);
});

test("the list is cut into to decide, removal kept and restored", () => {
  assert.equal(sectionOf({ status: "pending" }), "To decide");
  assert.equal(sectionOf({ status: "accepted" }), "Removal kept");
  // Restored in full or in part: one section.
  assert.equal(sectionOf({ status: "restored" }), "Restored");
  assert.equal(sectionOf({ status: "partial" }), "Restored");
});

test("choosing marks to restore toggles them and keeps them sorted", () => {
  assert.deepEqual(toggleMark([], 2), [2]);
  assert.deepEqual(toggleMark([2], 0), [0, 2]);
  assert.deepEqual(toggleMark([0, 2], 2), [0]);
});

test("no chosen mark means every mark is restored", () => {
  assert.deepEqual(marksToRestore([], 3), [0, 1, 2]);
  assert.deepEqual(marksToRestore([1], 3), [1]);
  assert.equal(restoreLabel(0, 1), "Restore the detail");
  assert.equal(restoreLabel(0, 3), "Restore all details");
  assert.equal(restoreLabel(3, 3), "Restore all details");
  assert.equal(restoreLabel(1, 3), "Restore 1 of 3 details");
});

test("what was removed and when it was said read well", () => {
  assert.equal(removedText({ email: 1, phone: 2 }), "1 e-mail address, 2 phone numbers");
  assert.equal(removedText({}), "");
  assert.equal(dateRange("2024-01-05", "2024-01-05"), "2024-01-05");
  assert.equal(dateRange("2024-01-05", "2024-02-01"), "2024-01-05 – 2024-02-01");
  assert.equal(dateRange(null, null), "—");
});

test("each removed detail is shown with the messages that hold it", () => {
  const messages = [
    { id: "m1", holds: [0] },
    { id: "m2", holds: [0, 1] },
    { id: "m3", holds: [] },
  ];
  assert.deepEqual(messagesHolding(messages, 0).map((m) => m.id), ["m1", "m2"]);
  assert.deepEqual(messagesHolding(messages, 1).map((m) => m.id), ["m2"]);
  assert.deepEqual(otherMessages(messages).map((m) => m.id), ["m3"]);
  assert.deepEqual(messagesHolding(undefined, 0), []);
});
