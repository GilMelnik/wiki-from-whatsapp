import assert from "node:assert/strict";
import { test } from "node:test";
import { EventChannel, OPEN, RECONNECTING } from "../src/shell/eventChannel.js";

class FakeSource {
  constructor(url) {
    this.url = url;
    this.closed = false;
  }
  close() {
    this.closed = true;
  }
  emit(event) {
    this.onmessage({ data: JSON.stringify(event) });
  }
}

function makeChannel() {
  const sources = [];
  const timers = [];
  const channel = new EventChannel({
    url: "/api/events",
    retryDelaysMs: [10, 20],
    createSource: (url) => {
      const source = new FakeSource(url);
      sources.push(source);
      return source;
    },
    setTimer: (callback, ms) => timers.push({ callback, ms }),
  });
  return { channel, sources, timers };
}

test("one source is shared by every subscriber and events are filtered by type", () => {
  const { channel, sources } = makeChannel();
  const changed = [];
  const all = [];
  channel.subscribe("artifact_changed", (event) => changed.push(event.key));
  channel.subscribe("*", (event) => all.push(event.type));
  assert.equal(sources.length, 1);
  sources[0].emit({ type: "hello" });
  sources[0].emit({ type: "artifact_changed", key: "claims" });
  sources[0].emit({ type: "job_started" });
  assert.deepEqual(changed, ["claims"]);
  assert.deepEqual(all, ["hello", "artifact_changed", "job_started"]);
});

test("unsubscribe stops delivery and a failing handler does not stop the others", () => {
  const { channel, sources } = makeChannel();
  const seen = [];
  const unsubscribe = channel.subscribe("artifact_changed", () => seen.push("a"));
  channel.subscribe("artifact_changed", () => {
    throw new Error("boom");
  });
  channel.subscribe("artifact_changed", () => seen.push("c"));
  const originalError = console.error;
  console.error = () => {};
  try {
    sources[0].emit({ type: "artifact_changed", key: "x" });
    unsubscribe();
    sources[0].emit({ type: "artifact_changed", key: "y" });
  } finally {
    console.error = originalError;
  }
  assert.deepEqual(seen, ["a", "c", "c"]);
});

test("an error replaces the source with backoff and the next hello asks for a resync", () => {
  const { channel, sources, timers } = makeChannel();
  const hellos = [];
  channel.subscribe("hello", (event) => hellos.push(event.resync));
  sources[0].onopen();
  assert.equal(channel.getStatus(), OPEN);
  sources[0].emit({ type: "hello" });

  sources[0].onerror();
  assert.equal(sources[0].closed, true);
  assert.equal(channel.getStatus(), RECONNECTING);
  sources[0].onerror(); // a late error of the replaced source schedules nothing more
  assert.deepEqual(timers.map((timer) => timer.ms), [10]);
  timers[0].callback();
  sources[1].onerror();
  timers[1].callback();
  sources[2].onerror();
  assert.deepEqual(timers.map((timer) => timer.ms), [10, 20, 20]); // the last delay repeats

  timers[2].callback();
  sources[3].onopen();
  sources[3].emit({ type: "hello" });
  assert.equal(channel.getStatus(), OPEN);
  assert.deepEqual(hellos, [false, true]);
});
