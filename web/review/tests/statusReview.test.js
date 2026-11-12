import assert from "node:assert/strict";
import { test } from "node:test";
import { artifactState, estimateText, exportText, firstToShow, showsUpdate, updateState } from "../src/tools/status/status.js";

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

test("an update is shown while it is not completed, or when its state cannot be read", () => {
  assert.equal(showsUpdate({ state: "in_progress" }), true);
  assert.equal(showsUpdate({ state: "unreadable" }), true);
  assert.equal(showsUpdate({ state: "completed" }), false);
  assert.equal(showsUpdate({ state: "none" }), false);
  assert.equal(showsUpdate(undefined), false);
});

test("the update's row says what it waits for", () => {
  const base = { state: "in_progress", running: null, failed: null, stopped: null };
  assert.equal(updateState({ ...base, stopped: { kind: "review" } }).label, "Waiting for your review");
  assert.equal(updateState({ ...base, stopped: { kind: "paid" } }).label, "Waiting for your go-ahead to pay");
  assert.equal(updateState({ ...base, failed: { step: "s" } }).tone, "rose");
  assert.equal(updateState({ ...base, running: { step: "s", title: "Step 3" } }).label, "Running Step 3");
  assert.equal(updateState({ state: "unreadable" }).tone, "rose");
});

test("what the export added and the estimate read as sentences", () => {
  assert.equal(exportText({ new: 1, changed: 0, missing: 0 }), "1 new message, 0 changed");
  assert.equal(exportText({ new: 1200, changed: 3, missing: 2 }), "1,200 new messages, 3 changed, 2 missing from it (kept)");
  const estimate = { paid_calls: 12, usd: 1.234, what: "12 conversations to read", counted: "characters / 3" };
  assert.equal(estimateText(estimate), "12 conversations to read: 12 paid calls, about $1.23 (rough: characters / 3)");
});
