// The conversations tool's pure helpers.
import assert from "node:assert/strict";
import { test } from "node:test";
import { selectedByThread, selectionRules, streamRows } from "../src/shell/pure.js";
import {
  addRange,
  automaticText,
  emptyListText,
  extraParams,
  knowledgeText,
  listParams,
  mergeCandidates,
  moveTargets,
  progressText,
  restOfThread,
  windowRows,
} from "../src/tools/threads/threads.js";

const msg = (id, seq, content = "text") => ({ id, seq, datetime: `2025-01-01T10:${String(seq).padStart(2, "0")}:00`, content });
const block = (thread_id, role, ids, extra = {}) => ({
  thread_id, role, size: ids.length, partial: false, start_time: "2025-01-01T10:00:00", last_time: "2025-01-01T10:30:00",
  messages: ids.map(([id, seq]) => msg(id, seq)), ...extra,
});

test("loaded chat stretches merge when they overlap or touch", () => {
  let ranges = addRange([], [10, 19]);
  ranges = addRange(ranges, [30, 39]);
  ranges = addRange(ranges, [20, 25]);
  assert.deepEqual(ranges, [[10, 25], [30, 39]]);
  assert.deepEqual(addRange(ranges, [0, 40]), [[0, 40]]);
  assert.deepEqual(extraParams({ include: ["t-x"], context: ranges }), { include: ["t-x"], context: ["10-25", "30-39"] });
});

test("the window list: window conversations only, with what lies inside and their cut edges", () => {
  const view = {
    chat_size: 10,
    in_window_ids: ["a2", "b1"],
    blocks: [
      block("t-a", "window", [["a1", 1], ["a2", 2]]),
      block("t-b", "window", [["b1", 3], ["b2", 6]]),
      block("t-c", "context", [["c1", 7]], { partial: true, size: 4 }),
    ],
  };
  const rows = windowRows(view);
  assert.deepEqual(rows.map((row) => [row.thread_id, row.inside, row.start, row.end]), [
    ["t-a", 1, true, false],
    ["t-b", 1, false, false],
  ]);
  assert.deepEqual(windowRows(undefined), []);
});

test("move targets are every conversation in view, with why one cannot take the selection", () => {
  const blocks = [block("t-a", "previous", [["a1", 1]]), block("t-b", "current", [["b1", 2], ["b2", 3]])];
  const rows = streamRows(blocks);
  const rules = selectionRules(selectedByThread(rows, new Set(["b2"])), new Map([["t-a", 1], ["t-b", 2]]));
  assert.deepEqual(moveTargets([...blocks, blocks[0]], rules).map((t) => [t.threadId, Boolean(t.reason)]), [
    ["t-a", false],
    ["t-b", true],
  ]);
});

test("split here: from the message to the end of a conversation shown whole", () => {
  const blocks = [block("t-b", "current", [["b1", 2], ["b2", 3], ["b3", 4]]), block("t-c", "context", [["c1", 5]], { partial: true })];
  assert.deepEqual(restOfThread(blocks, "b2"), { keys: ["b2", "b3"], threadId: "t-b" });
  assert.match(restOfThread(blocks, "b1").reason, /first message/);
  assert.match(restOfThread(blocks, "c1").reason, /Only part/);
  assert.match(restOfThread(blocks, "zz").reason, /not shown/);
});

test("merge offers the conversation right before first", () => {
  const blocks = [block("t-n", "next", []), block("t-p", "around", []), block("t-x", "current", [])];
  assert.deepEqual(mergeCandidates(blocks, "t-x").map((b) => b.thread_id), ["t-p", "t-n"]);
});

test("progress counts the done windows", () => {
  assert.equal(progressText([{ done: true }, { done: false }, { done: true }]), "2 of 3 windows done");
});

test("step 2's knowledge label reads as yes / no, with its probability as a detail", () => {
  const label = { value: false, auto: true, p_knowledge: 0.623, passed_heuristic: true, manual: true };
  assert.equal(knowledgeText(label), "No knowledge");
  assert.equal(knowledgeText({ ...label, value: true }), "Carries knowledge");
  assert.equal(knowledgeText(null), null);
  assert.equal(automaticText(label), "Step 2: yes (probability 0.62)");
  assert.equal(automaticText({ ...label, auto: false, p_knowledge: null }), "Step 2: no (too few messages or people to ask)");
});

test("the list asks for the knowledge filter, also from an older stored search", () => {
  assert.deepEqual(listParams({ q: "x", sort: "size" }), { q: "x", sort: "size", knowledge: "all" });
  assert.deepEqual(listParams({ q: "", sort: "time", knowledge: "manual" }), { q: "", sort: "time", knowledge: "manual" });
  assert.equal(emptyListText({ q: "cats", knowledge: "all" }), "No message or sender contains “cats”.");
  assert.equal(emptyListText({ q: "cats", knowledge: "yes" }), "No message or sender contains “cats” among “Carries knowledge”.");
  assert.equal(emptyListText({ q: "", knowledge: "manual" }), "No conversation has a label set by hand.");
  assert.equal(emptyListText({ q: "", knowledge: "no" }), "No conversation is marked “No knowledge”.");
});
