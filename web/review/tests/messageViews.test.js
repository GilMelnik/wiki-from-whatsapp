// The shared message views' pure helpers: rows, pauses, edges, selection, colours.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  formatPause,
  hiddenBetween,
  letterTag,
  rangeNextTo,
  replyClosures,
  selectedByThread,
  selectionRules,
  selectRange,
  streamRows,
  THREAD_COLORS,
  ThreadColors,
  threadEdges,
  toggleRow,
  windowEdges,
} from "../src/shell/pure.js";
import { themeColors } from "../src/shell/palette.js";

// Synthetic blocks in the shape the threads tool's /view answers (seq = place in the chat).
const msg = (id, seq, hhmm, extra = {}) => ({ id, seq, datetime: `2025-01-01T${hhmm}:00`, ...extra });
const A = { thread_id: "t-a", size: 3, messages: [msg("a1", 0, "10:00"), msg("a2", 2, "10:10"), msg("a3", 5, "10:40")] };
const B = { thread_id: "t-b", size: 3, messages: [msg("b1", 1, "10:05"), msg("b2", 3, "10:12", { reply_to: { id: "b1" } })] };
const keys = (rows) => rows.map((row) => row.key);
const alone = (rows) => (i) => [rows[i].key];

test("rows interleave in chat order and each conversation is taken once", () => {
  const rows = streamRows([A, B, A, null]);
  assert.deepEqual(keys(rows), ["a1", "b1", "a2", "b2", "a3"]);
  assert.deepEqual(rows[1], { key: "b1", threadId: "t-b", message: B.messages[0] });
});

test("pauses read as minutes, hours or days", () => {
  assert.deepEqual([12, 60, 125, 2880].map(formatPause), ["12 min", "1 h", "2 h 5 min", "2 days"]);
});

test("hidden chat messages are the gaps in seq", () => {
  const rows = streamRows([A, B]);
  assert.deepEqual(rows.slice(1).map((row, i) => hiddenBetween(rows[i], row)), [0, 0, 0, 1]);
  assert.equal(hiddenBetween(undefined, rows[0]), 0);
});

test("a conversation is cut by an edge when the chat message next to it is not shown", () => {
  const edges = threadEdges(streamRows([A, B]), 8);
  assert.deepEqual(edges.get("t-a"), { first: 0, last: 5, start: false, end: true });
  assert.deepEqual(edges.get("t-b"), { first: 1, last: 3, start: false, end: true }); // seq 4 is not shown
  const shifted = threadEdges(streamRows([{ thread_id: "t-c", messages: [msg("c1", 4, "10:30")] }]), 5);
  assert.deepEqual(shifted.get("t-c"), { first: 4, last: 4, start: true, end: false });
});

test("the stretch to load next to a message stays inside the chat and the gap", () => {
  assert.deepEqual(rangeNextTo(20, "before", 10, 100), [10, 19]);
  assert.deepEqual(rangeNextTo(3, "before", 10, 100), [0, 2]);
  assert.equal(rangeNextTo(0, "before", 10, 100), null);
  assert.deepEqual(rangeNextTo(95, "after", 10, 100), [96, 99]);
  assert.deepEqual(rangeNextTo(20, "after", 10, 100, 25), [21, 24]);
  assert.deepEqual(rangeNextTo(30, "before", 10, 100, 25), [26, 29]);
});

test("the gold window's edges are marked once each way", () => {
  const rows = streamRows([A, B]);
  assert.deepEqual([...windowEdges(rows, new Set(["b1", "a2", "b2"]))], [[1, true], [4, false]]);
  assert.equal(windowEdges(rows, null).size, 0);
});

test("a click selects a message with its reply chain; shift adds a range", () => {
  const rows = streamRows([A, B]);
  const closure = replyClosures(rows);
  let selected = toggleRow(new Set(), rows, 3, closure); // b2 replies to b1
  assert.deepEqual([...selected].sort(), ["b1", "b2"]);
  selected = toggleRow(selected, rows, 1, alone(rows)); // b1 off again
  assert.deepEqual([...selected], ["b2"]);
  selected = selectRange(selected, rows, 0, 2);
  assert.deepEqual([...selectedByThread(rows, selected)], [["t-a", ["a1", "a2"]], ["t-b", ["b1", "b2"]]]);
});

test("selection rules: moves need a selection outside the target; a whole conversation is no new one", () => {
  const rows = streamRows([A, B]);
  const sizes = new Map([["t-a", 3], ["t-b", 2]]);
  const rules = (ids) => selectionRules(selectedByThread(rows, new Set(ids)), sizes);
  const none = rules([]);
  assert.ok(none.moveTo("t-a") && none.newThread);
  const across = rules(["a1", "b2"]);
  assert.deepEqual([across.count, across.threads, across.keys], [2, ["t-a", "t-b"], ["a1", "b2"]]);
  assert.equal(across.moveTo("t-a"), null);
  assert.equal(across.newThread, null);
  assert.match(rules(["a1"]).moveTo("t-a"), /already in this conversation/);
  assert.match(rules(["b1", "b2"]).newThread, /whole conversation/);
});

test("colours: stable per conversation, a letter each, readable in both themes", () => {
  const colors = new ThreadColors();
  colors.assignAll(["t-a", "t-b"]);
  const first = colors.get("t-a");
  assert.equal(colors.get("t-a"), first);
  assert.deepEqual(["t-a", "t-b", "t-c"].map((id) => colors.get(id).tag), ["A", "B", "C"]);
  assert.equal(colors.peek("unseen"), null);
  assert.deepEqual([0, 25, 26, 701, 702].map(letterTag), ["A", "Z", "AA", "ZZ", "AAA"]);
  const luminance = (rgb) => {
    const [r, g, b] = rgb.map((v) => (v / 255 <= 0.03928 ? v / 255 / 12.92 : ((v / 255 + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  for (const theme of ["light", "dark"]) {
    const palette = themeColors(theme);
    for (const { chip } of THREAD_COLORS) {
      const [, name, shade] = /bg-(\w+)-(\d+)/.exec(chip);
      const [light, dark] = [luminance(palette.white), luminance(palette[name][Number(shade)])].sort((x, y) => y - x);
      assert.ok((light + 0.05) / (dark + 0.05) >= 4.5, `${chip} in ${theme}`);
    }
  }
});
