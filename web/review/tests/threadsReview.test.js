// The conversations tool's pure helpers.
import assert from "node:assert/strict";
import { test } from "node:test";
import { selectedByThread, selectionRules, streamRows } from "../src/shell/pure.js";
import {
  addRange,
  extraParams,
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
