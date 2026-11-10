import assert from "node:assert/strict";
import { test } from "node:test";
import { clampWidth, widthFromPointer } from "../src/shell/panelWidths.js";

const rect = { left: 100, right: 1100 };

test("widths are clamped to the column limits", () => {
  assert.equal(clampWidth(50, { min: 200, max: 500 }), 200);
  assert.equal(clampWidth(320.6, { min: 200, max: 500 }), 321);
  assert.equal(clampWidth(900, { min: 200, max: 500 }), 500);
});

test("in RTL the list hangs on the right edge and the side panel on the left", () => {
  assert.equal(widthFromPointer({ edge: "start", rtl: true, rect, clientX: 800 }), 300);
  assert.equal(widthFromPointer({ edge: "end", rtl: true, rect, clientX: 400 }), 300);
  assert.equal(widthFromPointer({ edge: "start", rtl: false, rect, clientX: 400 }), 300);
  assert.equal(widthFromPointer({ edge: "end", rtl: false, rect, clientX: 800 }), 300);
});
