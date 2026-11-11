import assert from "node:assert/strict";
import { test } from "node:test";
import {
  actionTargets,
  ALL,
  applyCost,
  applyQuestion,
  filterItems,
  filterOptions,
  formatUsd,
  newOutcomes,
  orderItems,
  outcomeFor,
  outcomeKey,
  proposalFromOperation,
  quoteParts,
  queuePlace,
} from "../src/tools/suggestions/suggestions.js";

// Synthetic suggestions in the shape GET /api/suggestions/suggestions answers.
const item = (id, kind, status, extra = {}) => ({
  id, kind, status, kind_name: `סוג ${kind}`, status_name: `מצב ${status}`, paid: kind === "mistake" || kind === "contradiction", busy: false, ...extra,
});
const ITEMS = [
  item("s1", "mistake", "applied"),
  item("s2", "split", "pending"),
  item("s3", "mistake", "rejected"),
  item("s4", "contradiction", "accepted"),
  item("s5", "add_label", "accepted", { busy: true }),
];

test("open suggestions come first, each group in the reviewer's order", () => {
  assert.deepEqual(orderItems(ITEMS).map((i) => i.id), ["s2", "s4", "s5", "s1", "s3"]);
});

test("filters by kind and status, with counted options", () => {
  assert.deepEqual(filterItems(ITEMS, { kind: "mistake", status: ALL }).map((i) => i.id), ["s1", "s3"]);
  assert.deepEqual(filterItems(ITEMS, { kind: ALL, status: "accepted" }).map((i) => i.id), ["s4", "s5"]);
  const options = filterOptions(ITEMS, "kind", "kind_name", "הכול");
  assert.deepEqual(options[0], { value: ALL, label: "הכול (5)" });
  assert.deepEqual(options[1], { value: "mistake", label: "סוג mistake (2)" });
});

test("an action moves only the suggestions in a fitting status that are not being applied", () => {
  assert.deepEqual(actionTargets(ITEMS, "accept").map((i) => i.id), ["s2"]);
  assert.deepEqual(actionTargets(ITEMS, "reject").map((i) => i.id), ["s2", "s4"]);
  assert.deepEqual(actionTargets(ITEMS, "reopen").map((i) => i.id), ["s3", "s4"]);
  assert.deepEqual(actionTargets(ITEMS, "apply").map((i) => i.id), ["s4"]);
});

test("an apply says how many are paid", () => {
  const cost = applyCost([ITEMS[3], item("s6", "merge", "accepted"), item("s7", "mistake", "accepted")]);
  assert.deepEqual(cost, { count: 3, paid: 2 });
  assert.match(applyQuestion(cost), /^3 הצעות לביצוע, 2 מהן בתשלום/);
  assert.match(applyQuestion({ count: 1, paid: 1 }), /^הצעה אחת לביצוע, והיא בתשלום/);
});

test("a corrected plan operation drops what the dialogs add but the review never proposes", () => {
  const op = { op: "split", page_id: "a", new_page_id: "b", title_he: "t", description: "d", scope: "s", aliases: { he: [], en: [] }, parent_id: null, original_scope: null };
  assert.deepEqual(proposalFromOperation(op), { op: "split", page_id: "a", new_page_id: "b", title_he: "t", description: "d", scope: "s" });
  const page = { op: "add_page", page_id: "p", parent_id: "c", title_he: "t", description: "d", scope: "s", aliases: { he: ["x"], en: [] }, position: null };
  assert.deepEqual(proposalFromOperation(page).aliases, { he: ["x"], en: [] });
  assert.equal("position" in proposalFromOperation(page), false);
});

test("the quoted sentence is cut out of its paragraph", () => {
  assert.deepEqual(quoteParts({ text: "אחת. שתיים. שלוש.", start: 5, end: 11 }), ["אחת. ", "שתיים.", " שלוש."]);
});

test("the apply queue: place, newest outcome, outcomes not seen yet", () => {
  const applies = {
    running: { id: "s4" },
    queue: [{ id: "s5" }],
    outcomes: [{ id: "s1", status: "applied", ended_at: "t2" }, { id: "s1", status: "failed", ended_at: "t1" }],
  };
  assert.equal(queuePlace(applies, "s4"), "running");
  assert.equal(queuePlace(applies, "s5"), "queued");
  assert.equal(queuePlace(applies, "s2"), null);
  assert.equal(outcomeFor(applies, "s1").status, "applied");
  const seen = new Set([outcomeKey(applies.outcomes[1])]);
  assert.deepEqual(newOutcomes(applies, seen).map((o) => o.ended_at), ["t2"]);
  assert.equal(formatUsd(3.7), "$3.7");
});
