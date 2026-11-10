import assert from "node:assert/strict";
import { test } from "node:test";
import { createLatestGuard } from "../src/shell/latest.js";

test("only the newest request is current (the stale-detail race)", async () => {
  const guard = createLatestGuard();
  const shown = [];
  const load = async (id, delayMs) => {
    const isCurrent = guard.begin();
    await new Promise((done) => setTimeout(done, delayMs));
    if (isCurrent()) shown.push(id);
  };
  await Promise.all([load("slow-a", 30), load("fast-b", 1)]);
  assert.deepEqual(shown, ["fast-b"]);
});

test("invalidate makes every pending request stale", () => {
  const guard = createLatestGuard();
  const isCurrent = guard.begin();
  guard.invalidate();
  assert.equal(isCurrent(), false);
});
