import assert from "node:assert/strict";
import { test } from "node:test";
import { cleanName, decisionToast, nextSelection, showOptions, statsText, tagIn, tagText } from "../src/tools/tagged/tagged.js";

const rows = (...ids) => ids.map((member) => ({ member }));

test("after a save the member stays selected while listed, else the next one listed before", () => {
  assert.equal(nextSelection(rows("a", "b", "c"), rows("a", "b", "c"), "b"), "b");
  assert.equal(nextSelection(rows("a", "b", "c"), rows("a", "c"), "b"), "c");
  assert.equal(nextSelection(rows("a", "b", "c"), rows("a"), "c"), "a");
  assert.equal(nextSelection(rows("a"), [], "a"), null);
});

test("the default filter names the server's threshold", () => {
  assert.equal(showOptions(3)[0].value, "waiting");
  assert.equal(showOptions(3)[0].label, "Undecided, tagged by 3 or more members");
});

test("a row reads as the tag the chat writes and how often it is tagged", () => {
  const row = { tags: ["111", "222"], tag_count: 1, tagger_count: 2, written: 0 };
  assert.equal(tagText(row), "@111");
  assert.equal(statsText(row), "1 tag by 2 members · wrote 0 messages");
  assert.equal(tagIn("thanks @222!", row.tags), "222");
  assert.equal(tagIn("no tag", row.tags), "111");
});

test("a name is saved with its spaces squashed, and the toast says what step 3 will show", () => {
  assert.equal(cleanName("  Clinic   Alpha "), "Clinic Alpha");
  assert.equal(cleanName(undefined), "");
  assert.equal(decisionToast({ status: "provider", public_name: "Clinic Alpha" }), "Saved: a provider, shown to step 3 as “@Clinic Alpha”");
  assert.equal(decisionToast({ status: "undecided" }), "Decision cleared: undecided again");
});
