// Pure helpers of the message views (run: npm test).
import assert from "node:assert/strict";
import { test } from "node:test";

import { chronoStream, displayedThreads, windowEdges } from "../src/messages/chronoStream.js";
import {
  groupByThread,
  indicesFromKeys,
  keysFromIndices,
  quoteClosures,
  selectRange,
  selectionRules,
  toggleRow,
} from "../src/messages/selection.js";
import { PALETTE, ThreadColors, letterTag } from "../src/messages/threadColors.js";

const msg = (id, hhmm, extra = {}) => ({ id, datetime: `2025-01-01T${hhmm}:00`, ...extra });
const A = { thread_id: "t-a", messages: [msg("a1", "10:00"), msg("a2", "10:10"), msg("a3", "10:20")] };
const B = { thread_id: "t-b", messages: [msg("b1", "10:05"), msg("b2", "10:10"), msg("b3", "10:30")] };
const keys = (rows) => rows.map((r) => r.key);
const alone = (rows) => (i) => [rows[i].key];

test("chronoStream interleaves by time, keeps thread order on ties and drops duplicates", () => {
  const rows = chronoStream([A, B, A, null]);
  assert.deepEqual(keys(rows), ["a1", "b1", "a2", "b2", "a3", "b3"]);
  assert.deepEqual(rows[3], { key: "b2", threadId: "t-b", position: 1, message: B.messages[1] });
  assert.deepEqual(keys(chronoStream([B, A])).slice(2, 4), ["b2", "a2"]);
});

test("equal times inside one thread keep the server order", () => {
  const T = { thread_id: "t", messages: [msg("x2", "10:00"), msg("x1", "10:00")] };
  assert.deepEqual(keys(chronoStream([T])), ["x2", "x1"]);
});

test("messages without a WhatsApp id get a positional key", () => {
  const T = { thread_id: "t", messages: [{ datetime: "2025-01-01T10:00:00" }] };
  assert.deepEqual(keys(chronoStream([T])), ["t#0"]);
});

test("displayedThreads mirrors the thread view: prev, family or thread, next", () => {
  const prev = { thread_id: "p" };
  const next = { thread_id: "n" };
  assert.deepEqual(displayedThreads(A, { prev, next, family: [A] }), [prev, A, next]);
  assert.deepEqual(displayedThreads(A, { prev: null, next, family: [A, B] }), [A, B, next]);
  assert.deepEqual(displayedThreads(null, {}), []);
});

test("windowEdges marks each entry into and exit out of the window once", () => {
  const rows = chronoStream([A, B]);
  const edges = windowEdges(rows, new Set(["b1", "a2", "b2"]));
  assert.deepEqual([...edges], [[1, true], [4, false]]);
  assert.equal(windowEdges(rows, null).size, 0);
});

test("colours are stable per thread; a split-off thread gets a new one", () => {
  const colors = new ThreadColors();
  colors.assignAll(["t-a", "t-b"]);
  const before = colors.get("t-a");
  colors.get("t-a-split-1");
  assert.equal(colors.get("t-a"), before);
  assert.deepEqual(
    ["t-a", "t-b", "t-a-split-1"].map((id) => colors.get(id).tag),
    ["A", "B", "C"]
  );
  assert.notEqual(colors.get("t-a-split-1").color, colors.get("t-a").color);
  assert.equal(colors.peek("unseen"), null);
  const many = new ThreadColors();
  const ten = Array.from({ length: PALETTE.length }, (_, i) => many.get(`t${i}`).color);
  assert.equal(new Set(ten).size, PALETTE.length);
  assert.deepEqual([0, 25, 26, 27, 701, 702].map(letterTag), ["A", "Z", "AA", "AB", "ZZ", "AAA"]);
});

test("selection spans threads: toggle, range and grouping", () => {
  const rows = chronoStream([A, B]);
  let selected = toggleRow(new Set(), rows, 1, alone(rows)); // b1
  selected = selectRange(selected, rows, 1, 3); // b1 a2 b2
  assert.deepEqual([...selected].sort(), ["a2", "b1", "b2"]);
  selected = toggleRow(selected, rows, 2, alone(rows)); // deselect a2
  const groups = groupByThread(rows, selected);
  assert.deepEqual([...groups.keys()], ["t-b"]);
  assert.deepEqual(groups.get("t-b"), { keys: ["b1", "b2"], positions: [0, 1] });
});

test("toggleRow adds the whole quote chain", () => {
  const Q = {
    thread_id: "q",
    messages: [msg("q1", "10:00", { content: "hello there", sender: "s" }),
      msg("q2", "10:01", { quote: { sender: "s", text: "hello there" } })],
  };
  const rows = chronoStream([Q, B]);
  const selected = toggleRow(new Set(), rows, 0, quoteClosures(rows));
  assert.deepEqual([...selected].sort(), ["q1", "q2"]);
  assert.deepEqual([...toggleRow(selected, rows, 0, quoteClosures(rows))], ["q2"]);
});

test("indices and keys convert between the two views", () => {
  const rows = chronoStream([A, B]);
  const selected = keysFromIndices(rows, "t-a", new Set([0, 2]));
  assert.deepEqual([...selected].sort(), ["a1", "a3"]);
  assert.deepEqual([...indicesFromKeys(rows, "t-a", new Set(["a3", "b1"]))], [2]);
});

test("selectionRules enable only what the server operations can do", () => {
  const rows = chronoStream([A, B]);
  const sizes = new Map([["t-a", 3], ["t-b", 3]]);
  const rules = (ids) => selectionRules(groupByThread(rows, new Set(ids)), sizes);

  const empty = rules([]);
  assert.equal(empty.count, 0);
  assert.ok(empty.moveTo("t-a") && empty.newThread && empty.split && empty.moveDialog);

  const across = rules(["a1", "b2"]);
  assert.deepEqual([across.count, across.threads], [2, ["t-a", "t-b"]]);
  assert.equal(across.moveTo("t-a"), null);
  assert.equal(across.newThread, null);
  assert.match(across.split, /2 שיחות/);
  assert.ok(across.moveDialog);

  const gap = rules(["a1", "a3"]);
  assert.equal(gap.split, null);
  assert.ok(gap.moveDialog);
  assert.ok(gap.moveTo("t-a"));
  assert.equal(gap.moveTo("t-b"), null);

  const run = rules(["a1", "a2"]);
  assert.equal(run.moveDialog, null);

  const whole = rules(["a1", "a2", "a3"]);
  assert.ok(whole.newThread);
  assert.ok(whole.split);
});
