import assert from "node:assert/strict";
import { test } from "node:test";
import { canKeep, countsText, groupPeopleText, keepSelection, keepToast, listsWarning, monthDate, peopleText, whyText, WHY_LABELS } from "../src/tools/withheld/withheld.js";

test("people are counted, never named", () => {
  assert.equal(peopleText(1), "backed by 1 person");
  assert.equal(peopleText(3), "backed by 3 people");
  assert.equal(groupPeopleText({ people: 3, people_unmarked: 3 }, 2), "3 people back these claims");
  assert.equal(
    groupPeopleText({ people: 1, people_unmarked: 2 }, 2),
    "1 person backs these claims (2 before the claims you kept withheld): fewer than 2, so nothing is released"
  );
});

test("every reason the server gives has a label and a sentence", () => {
  for (const why of ["kept", "waiting", "unusable", "short", "unmatched", "alone"]) {
    assert.ok(WHY_LABELS[why], why);
    assert.notEqual(whyText(why), why);
  }
  assert.match(whyText("waiting"), /python -m step_7_write_pages\.same_complaint/);
  assert.match(whyText("short", 3), /fewer than 3 people/);
});

test("only a claim the rule withholds can be kept withheld", () => {
  assert.equal(canKeep({ status: "released" }), true);
  assert.equal(canKeep({ status: "kept" }), true);
  assert.equal(canKeep({ status: "short" }), true);
  assert.equal(canKeep({ status: "published" }), false);
});

test("the toolbar counts and the warning about lists the check has not answered", () => {
  assert.equal(
    countsText({ withheld: 391, released: 12, still_withheld: 379, kept: 1 }),
    "391 withheld by the rule · 12 released by the check · 379 still withheld · 1 kept withheld by you"
  );
  assert.equal(countsText(undefined), "");
  assert.equal(listsWarning({ answered: 4, waiting: 0, unusable: 0 }), null);
  assert.equal(
    listsWarning({ answered: 0, waiting: 2, unusable: 1 }),
    "2 entities wait for the check (run python -m step_7_write_pages.same_complaint); the answer about 1 entity was unusable: nothing of them is released."
  );
});

test("months, toasts and the selection after a change", () => {
  assert.equal(monthDate("2024-03"), "2024-03-15");
  assert.equal(monthDate(null), null);
  assert.match(keepToast(true), /will not release/);
  assert.match(keepToast(false), /may release/);
  const items = [{ id: "a" }, { id: "b" }];
  const key = (item) => item.id;
  assert.equal(keepSelection(items, key, "b"), "b");
  assert.equal(keepSelection(items, key, "z"), "a");
  assert.equal(keepSelection([], key, "a"), null);
});
