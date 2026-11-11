import assert from "node:assert/strict";
import { test } from "node:test";
import { artifactState, firstToShow } from "../src/tools/status/status.js";

const file = (key, extra = {}) => ({ key, exists: true, edited_exists: false, stale_because: [], ...extra });
const step = { key: "s" };

test("one state per artifact, most urgent first", () => {
  assert.equal(artifactState(file("a"), { step, running: { id: "j" } }).kind, "running");
  assert.equal(artifactState(file("a", { exists: false }), { step }).kind, "missing");
  assert.equal(artifactState(file("a", { exists: false }), {}).label, "No file");
  assert.equal(artifactState(file("a", { stale_because: ["b"] }), { step }).kind, "stale");
  assert.equal(artifactState(file("a", { stale_because: ["b"] }), { step, latest: { state: "failed" } }).kind, "failed");
  assert.equal(artifactState(file("a"), {}).kind, "input");
  assert.equal(artifactState(file("a"), { step }).kind, "current");
});

test("the artifact shown first is the earliest that needs attention", () => {
  const artifacts = [file("a", { exists: false }), file("b"), file("c", { stale_because: ["b"] })];
  const stateOf = (artifact) => artifactState(artifact, { step });
  assert.equal(firstToShow(artifacts, stateOf), "c");
  assert.equal(firstToShow(artifacts.slice(0, 2), stateOf), "a");
  assert.equal(firstToShow([], stateOf), null);
});
