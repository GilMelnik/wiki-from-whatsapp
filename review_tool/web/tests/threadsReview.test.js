// The conversations tool's pure helpers.
import assert from "node:assert/strict";
import { test } from "node:test";
import { selectedByThread, selectionRules, streamRows } from "../src/shell/pure.js";
import {
  addRange,
  automaticText,
  emptyListText,
  extraParams,
  knowledgeFilterOptions,
  knowledgeText,
  labelActions,
  labelDoneText,
  labelStateText,
  listParams,
  mergeCandidates,
  moveTargets,
  progressText,
  restOfThread,
  rowKnowledgeText,
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
  const label = { value: false, current: true, auto: true, p_knowledge: 0.623, passed_heuristic: true, manual: true };
  assert.equal(knowledgeText(label), "No knowledge");
  assert.equal(knowledgeText({ ...label, value: true }), "Carries knowledge");
  assert.equal(knowledgeText(null), null);
  assert.equal(automaticText(label), "Step 2: yes (probability 0.62)");
  assert.equal(automaticText({ ...label, auto: false, p_knowledge: null }), "Step 2: no (too few messages or people to ask)");
  assert.equal(automaticText({ ...label, auto: null, p_knowledge: null, passed_heuristic: null }), null);
  assert.equal(automaticText(null), null);
});

const STEP_2_YES = { value: true, manual: false, current: true, auto: true, p_knowledge: 0.6, passed_heuristic: true };
const MINE_NO = { value: false, manual: true, current: true, auto: null, p_knowledge: null, passed_heuristic: null };

test("the opened conversation's label says what it is and who decided it", () => {
  assert.equal(labelStateText(null), "Not labelled yet");
  assert.equal(labelStateText(STEP_2_YES), "Carries knowledge · step 2's answer");
  assert.equal(labelStateText(MINE_NO), "No knowledge · your label");
  assert.equal(labelStateText({ ...MINE_NO, current: false }), "Needs a new label: the conversation changed after this label was given");
});

test("both answers are offered, except the owner's own current label; o takes the owner's label away", () => {
  const keys = (label) => labelActions(label).map((action) => [action.key, action.text]);
  assert.deepEqual(keys(null), [["y", "It carries knowledge"], ["x", "It carries no knowledge"]]);
  assert.deepEqual(keys(STEP_2_YES), [["y", "It carries knowledge"], ["x", "It carries no knowledge"]]);
  assert.deepEqual(keys(MINE_NO), [["y", "It carries knowledge"], ["o", "Remove my label"]]);
  assert.deepEqual(keys({ ...MINE_NO, current: false }), [
    ["y", "It carries knowledge"], ["x", "It carries no knowledge"], ["o", "Remove my label"],
  ]);
  assert.deepEqual(keys({ ...STEP_2_YES, manual: true }).map(([key, text]) => key + text), [
    "xIt carries no knowledge", "oBack to step 2's answer",
  ]);
  assert.equal(labelDoneText(null, MINE_NO), "Your label is removed");
  assert.equal(labelDoneText(null, { ...STEP_2_YES, manual: true }), "Back to step 2's answer");
  assert.equal(labelDoneText(true, null), "Your label: carries knowledge");
  assert.equal(labelDoneText(false, STEP_2_YES), "Your label: carries no knowledge");
});

test("a row shows only what stands out: a label needed, no knowledge, a label set by hand", () => {
  assert.equal(rowKnowledgeText(null), "Needs a label");
  assert.equal(rowKnowledgeText({ ...STEP_2_YES, current: false }), "Needs a new label");
  assert.equal(rowKnowledgeText(STEP_2_YES), null);
  assert.equal(rowKnowledgeText({ ...STEP_2_YES, value: false }), "No knowledge");
  assert.equal(rowKnowledgeText(MINE_NO), "No knowledge, set by hand");
  assert.equal(rowKnowledgeText({ ...STEP_2_YES, manual: true }), "Carries knowledge, set by hand");
});

test("the list asks for the knowledge filter, also from an older stored search", () => {
  assert.deepEqual(listParams({ q: "x", sort: "size" }), { q: "x", sort: "size", knowledge: "all", keep: [] });
  assert.deepEqual(listParams({ q: "", sort: "time", knowledge: "needs_label" }, ["t-a"]), {
    q: "", sort: "time", knowledge: "needs_label", keep: ["t-a"],
  });
  assert.equal(emptyListText({ q: "", knowledge: "needs_label" }), "No conversation needs a label.");
  assert.equal(emptyListText({ q: "", knowledge: "changed" }), "No conversation was made or changed by your edits.");
  assert.equal(emptyListText({ q: "cats", knowledge: "all" }), "No message or sender contains “cats”.");
  assert.equal(emptyListText({ q: "cats", knowledge: "yes" }), "No message or sender contains “cats” among “Carries knowledge”.");
  assert.equal(emptyListText({ q: "", knowledge: "manual" }), "No conversation has a label set by hand.");
  assert.equal(emptyListText({ q: "", knowledge: "no" }), "No conversation is marked “No knowledge”.");
});

test("the filter offers the conversations needing a label and those changed by the owner, with counts", () => {
  const options = knowledgeFilterOptions({ ready: true, manual_count: 2, needs_label_count: 1234, changed_count: 0 });
  assert.deepEqual(options.map((option) => [option.value, option.label]), [
    ["all", "All"],
    ["needs_label", "Needs a label (1,234)"],
    ["changed", "Changed by my edits (0)"],
    ["yes", "Carries knowledge"],
    ["no", "No knowledge"],
    ["manual", "Set by hand (2)"],
  ]);
  assert.equal(knowledgeFilterOptions(undefined)[1].label, "Needs a label");
});
