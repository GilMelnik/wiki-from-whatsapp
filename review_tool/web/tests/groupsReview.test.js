import assert from "node:assert/strict";
import { test } from "node:test";
import {
  barPercent,
  dateRange,
  DEFAULT_VIEW,
  diffWords,
  entityOptions,
  followSuggestion,
  pickedIn,
  releaseNote,
  togglePick,
  viewParams,
  whySuggested,
} from "../src/tools/groups/groups.js";

const items = [{ key: "a|b" }, { key: "c|d" }, { key: "e|f" }];

test("after a decision the next suggestion takes the place of the decided one", () => {
  assert.equal(followSuggestion(items, "c|d", 1), "c|d"); // still listed: stays
  const afterMerge = [{ key: "a|b" }, { key: "e|f" }];
  assert.equal(followSuggestion(afterMerge, "c|d", 1), "e|f");
  assert.equal(followSuggestion([{ key: "a|b" }], "c|d", 1), "a|b"); // list got shorter
  assert.equal(followSuggestion(items, null, 0), "a|b");
  assert.equal(followSuggestion([], "a|b", 0), null);
});

test("the list query leaves out unset filters", () => {
  assert.deepEqual(viewParams(DEFAULT_VIEW), { sort: "size", order: "desc" });
  assert.deepEqual(viewParams({ ...DEFAULT_VIEW, multi: true, q: "x" }), { multi: true, q: "x", sort: "size", order: "desc" });
});

test("picked claims belong to one group", () => {
  let picked = { groupId: null, claimIds: [] };
  picked = togglePick(picked, "g-1", "c1");
  picked = togglePick(picked, "g-1", "c2");
  assert.deepEqual(pickedIn(picked, "g-1"), ["c1", "c2"]);
  assert.deepEqual(pickedIn(picked, "g-2"), []);
  assert.deepEqual(togglePick(picked, "g-1", "c1").claimIds, ["c2"]);
  assert.deepEqual(togglePick(picked, "g-2", "c9"), { groupId: "g-2", claimIds: ["c9"] });
});

test("dates, bars and entity options are formatted for the page", () => {
  assert.equal(dateRange("2024-01-01", "2024-01-01"), "2024-01-01");
  assert.equal(dateRange("2024-01-01", "2024-03-01"), "2024-01-01 – 2024-03-01");
  assert.equal(dateRange(null, null), "—");
  assert.equal(barPercent(0, 10), 0);
  assert.equal(barPercent(1, 100), 4);
  assert.equal(barPercent(10, 10), 100);
  assert.deepEqual(entityOptions([{ entity_id: "e1", name: "Alpha", groups: 2 }]), [
    { value: "", label: "All entities" },
    { value: "e1", label: "\u2068Alpha\u2069 (2)" },
  ]);
});

const joined = (segments) => segments.map((segment) => segment.text).join("");
const changed = (segments) => segments.filter((segment) => !segment.same).map((segment) => segment.text.trim());

test("the words that differ between two texts are marked, punctuation and case aside", () => {
  const diff = diffWords("The clinic in Alpha  is good.", "the clinic in Beta is very good");
  assert.equal(joined(diff.a), "The clinic in Alpha is good.");
  assert.equal(joined(diff.b), "the clinic in Beta is very good");
  assert.deepEqual(changed(diff.a), ["Alpha"]);
  assert.deepEqual(changed(diff.b), ["Beta", "very"]);
  const same = diffWords("one two", "one two");
  assert.deepEqual(same.a, [{ text: "one two", same: true }]);
  assert.deepEqual(diffWords("", "new words").b, [{ text: "new words", same: false }]);
  const long = Array.from({ length: 300 }, (_, index) => `w${index}`).join(" ");
  assert.deepEqual(diffWords(long, `${long} x`).b, [{ text: `${long} x`, same: true }]); // too long: no highlight
});

test("why a pair is suggested says it in words, with shared names and stances", () => {
  const pair = { a: { stance: "positive" }, b: { stance: "positive" }, shared_entity_names: ["Alpha"] };
  const lines = whySuggested(pair);
  assert.equal(lines.length, 3);
  assert.equal(lines[1], "Both name “\u2068Alpha\u2069”.");
  assert.match(lines[2], /same stance: positive/);
  const apart = whySuggested({ a: { stance: "positive" }, b: { stance: "factual" }, shared_entity_names: [] });
  assert.match(apart[1], /no entity in common/);
  assert.match(apart[2], /differ: positive and factual/);
  assert.ok(lines.every((line) => !/\d\.\d/.test(line))); // no similarity numbers
});

test("a merge says whether it releases a withheld group, keeps it withheld or withholds the result", () => {
  const check = (withheld, after, supporters) => ({ withheld, withheld_after: after, supporter_count_after: supporters });
  assert.equal(releaseNote(null, 2), null);
  assert.equal(releaseNote(check([], false, 3), 2), null);
  assert.equal(releaseNote(check(["g-1"], null, null), 2), null); // without vectors: unknown
  assert.deepEqual(releaseNote(check(["g-1"], false, 2), 2), {
    tone: "success",
    text: "This merge releases it to the wiki: the merged group has 2 supporters.",
  });
  assert.equal(releaseNote(check(["g-1", "g-2"], true, 1), 2).tone, "info");
  assert.match(releaseNote(check(["g-1", "g-2"], true, 1), 2).text, /still have 1 supporter \(it needs 2\)/);
  assert.equal(releaseNote(check([], true, 1), 2).tone, "warning");
});
