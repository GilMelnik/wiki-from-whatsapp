import assert from "node:assert/strict";
import { test } from "node:test";
import {
  allPicked,
  colour,
  contactsChanged,
  contactsToForm,
  formToContacts,
  NOTHING_PICKED,
  pickedFor,
  segments,
  statusOf,
  togglePicked,
} from "../src/tools/entities/entities.js";

test("a claim text is cut around its highlights", () => {
  const text = "Alpha works with Beta";
  const self = { start: 0, end: 5, kind: "self" };
  const other = { start: 17, end: 21, kind: "other" };
  assert.deepEqual(segments(text, [self, other]), [
    { text: "Alpha", highlight: self },
    { text: " works with " },
    { text: "Beta", highlight: other },
  ]);
  assert.deepEqual(segments(text, []), [{ text }]);
  // Overlapping or out-of-range spans are skipped instead of garbling the text.
  const overlapping = { start: 3, end: 8, kind: "other" };
  const beyond = { start: 18, end: 40, kind: "other" };
  assert.equal(segments(text, [self, overlapping, beyond]).map((p) => p.text).join(""), text);
});

test("picked claims belong to one entity and keep their texts", () => {
  let picked = togglePicked(NOTHING_PICKED, "e1", "Alpha", { claim_id: "c1", claim_text: "one" });
  picked = togglePicked(picked, "e1", "Alfa", { claim_id: "c2", claim_text: "two" });
  picked = togglePicked(picked, "e1", "Alfa", { claim_id: "c1", claim_text: "one" });
  assert.deepEqual(pickedFor(picked, "e1"), { Alpha: ["c1"], Alfa: ["c2", "c1"] });
  assert.deepEqual(allPicked(picked, "e1"), ["c1", "c2"]);
  assert.deepEqual(picked.texts, { c1: "one", c2: "two" });
  picked = togglePicked(picked, "e1", "Alpha", { claim_id: "c1", claim_text: "one" });
  assert.deepEqual(pickedFor(picked, "e1"), { Alfa: ["c2", "c1"] });
  assert.deepEqual(pickedFor(picked, "e2"), {});
  // Picking in another entity starts over.
  const other = togglePicked(picked, "e2", "Beta", { claim_id: "c9", claim_text: "nine" });
  assert.deepEqual(other.byName, { Beta: ["c9"] });
  assert.deepEqual(other.texts, { c9: "nine" });
});

test("contacts go to and from one-value-per-line fields", () => {
  const contacts = { email: ["a@x.example", "b@x.example"], phone: [], website: ["x.example"] };
  const form = contactsToForm(contacts);
  assert.deepEqual(form, { email: "a@x.example\nb@x.example", phone: "", website: "x.example" });
  assert.equal(contactsChanged(contacts, form), false);
  assert.equal(contactsChanged(contacts, { ...form, email: " a@x.example \n\nb@x.example" }), false);
  assert.equal(contactsChanged(contacts, { ...form, phone: "03-1234567" }), true);
  assert.deepEqual(formToContacts({ ...form, phone: " 03-1234567 \n" }).phone, ["03-1234567"]);
});

test("statuses and colours have fallbacks", () => {
  assert.equal(statusOf("accepted").label, "אושרה");
  assert.equal(statusOf("unknown"), statusOf("suggested"));
  assert.equal(colour(0), colour(8));
  assert.equal(colour(-1), colour(7));
  assert.equal(colour(null), colour(0));
});
