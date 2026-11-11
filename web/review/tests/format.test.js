import assert from "node:assert/strict";
import { test } from "node:test";
import { formatBytes, formatRelative } from "../src/shell/format.js";
import { countText } from "../src/shell/planText.js";

test("relative times never point to the future", () => {
  const now = Date.parse("2026-11-10T12:00:00Z");
  assert.equal(formatRelative("2026-11-10T12:00:05Z", now), formatRelative("2026-11-10T12:00:00Z", now));
  assert.notEqual(formatRelative("2026-11-10T11:55:00Z", now), formatRelative("2026-11-10T12:00:00Z", now));
  assert.equal(formatRelative(null, now), "—");
});

test("sizes use binary units", () => {
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(null), "—");
});

test("counted words take the plural unless the count is one", () => {
  assert.equal(countText(1, "claim"), "1 claim");
  assert.equal(countText(2500, "claim"), "2,500 claims");
  assert.equal(countText(0, "person", String, "people"), "0 people");
});
