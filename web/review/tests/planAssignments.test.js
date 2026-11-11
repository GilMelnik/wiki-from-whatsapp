import assert from "node:assert/strict";
import { test } from "node:test";
import { pickState, setPicked, togglePicked } from "../src/shell/picking.js";
import { stanceOf } from "../src/shell/stance.js";
import {
  assignmentLines,
  assignmentRows,
  DEFAULT_GROUP_VIEW,
  findingText,
  groupCount,
  groupsQuery,
  isAssignmentRow,
  labelChanges,
  labelShare,
  nextLabelTarget,
  pageChoices,
  proposalForm,
  PROPOSALS_ID,
  UNPLACED_ID,
  WAITING_ID,
} from "../src/tools/plan/assign.js";

// A synthetic overview in the shape GET /api/plan/plan answers (only what these helpers read).
const node = (id, kind, parent = null) => ({ id, kind, title_he: `כותרת ${id}`, parent_id: parent, children: [] });
const OVERVIEW = {
  nodes: [node("before", "category"), node("agencies", "page", "before"), node("costs", "page", "before"), node("during", "category"), node("clinics", "page", "during")],
  pages: { agencies: { title_he: "סוכנויות" }, costs: { title_he: "עלויות" }, clinics: { title_he: "מרפאות" } },
  labels: { usa: { title_he: "ארה״ב", kind: "country" }, georgia: { title_he: "גאורגיה", kind: "country" } },
  assignments: { exists: true, counts: { waiting: 0 }, proposals: { pending: 2 } },
};
const row = (id, p, labels = []) => ({ group_id: id, record: true, p, labels });

test("multi-picking: toggle, pick or drop the visible rows, and the box state", () => {
  assert.deepEqual(togglePicked(["a"], "b"), ["a", "b"]);
  assert.deepEqual(togglePicked(["a", "b"], "a"), ["b"]);
  assert.deepEqual(setPicked(["x"], ["a", "b", "x"], true), ["x", "a", "b"]); // off-page picks stay
  assert.deepEqual(setPicked(["x", "a", "b"], ["a", "b"], false), ["x"]);
  assert.equal(pickState(["a", "b"], ["a", "b"]), "all");
  assert.equal(pickState(["a"], ["a", "b"]), "some");
  assert.equal(pickState(["z"], ["a", "b"]), "none");
  assert.equal(pickState([], []), "none");
});

test("stances read in Hebrew, unknown ones as themselves", () => {
  assert.deepEqual(stanceOf("negative"), { label: "שלילית", badge: "badge-rose" });
  assert.equal(stanceOf("odd").label, "odd");
  assert.equal(stanceOf(undefined).label, "—");
});

test("the tree list gets the assignment rows; waiting only when some groups wait", () => {
  assert.deepEqual(assignmentRows(OVERVIEW).map((item) => item.id), [UNPLACED_ID, PROPOSALS_ID]);
  const waiting = { ...OVERVIEW, assignments: { ...OVERVIEW.assignments, counts: { waiting: 3 } } };
  assert.deepEqual(assignmentRows(waiting).map((item) => item.id), [UNPLACED_ID, WAITING_ID, PROPOSALS_ID]);
  assert.deepEqual(assignmentRows({}).map((item) => item.id), [UNPLACED_ID, PROPOSALS_ID]); // 6b not run
  assert.ok(isAssignmentRow(PROPOSALS_ID) && !isAssignmentRow("costs"));
});

test("the list query sends the role only for a page and leaves out an empty filter", () => {
  assert.deepEqual(groupsQuery("page", "costs", { ...DEFAULT_GROUP_VIEW, role: "mention" }, 50), {
    scope: "page", id: "costs", role: "mention", sort: "uncertain", q: undefined, offset: 50,
  });
  assert.deepEqual(groupsQuery("unplaced", null, { sort: "date", role: "primary", q: "  x " }, 0), {
    scope: "unplaced", id: undefined, role: undefined, sort: "date", q: "x", offset: 0,
  });
});

test("the page picker lists the pages the classifier considered first, by mean probability", () => {
  const rows = [row("g1", [["costs", 0.6], ["agencies", 0.3]]), row("g2", [["costs", 0.2], ["gone", 0.5]])];
  const { considered, others } = pageChoices(OVERVIEW, rows);
  assert.deepEqual(considered, [
    { id: "costs", mean: 0.4, count: 2 },
    { id: "agencies", mean: 0.15, count: 1 },
  ]); // "gone" is no page of today's plan
  assert.deepEqual(others, ["clinics"]);
  const searched = pageChoices(OVERVIEW, rows, "מרפ");
  assert.deepEqual([searched.considered, searched.others], [[], ["clinics"]]);
  const byPath = pageChoices(OVERVIEW, [], "before", (id) => (id === "clinics" ? "during" : "before"));
  assert.deepEqual(byPath.others, ["agencies", "costs"]);
});

test("labels of a bulk edit: what all / some / none carry, and only real changes are sent", () => {
  const rows = [row("g1", [], ["usa"]), row("g2", [], ["usa", "georgia"])];
  assert.equal(labelShare(rows, "usa"), "all");
  assert.equal(labelShare(rows, "georgia"), "some");
  assert.equal(labelShare(rows, "twins"), "none");
  assert.equal(nextLabelTarget("all", undefined), "none");
  assert.equal(nextLabelTarget("some", undefined), "all");
  assert.equal(nextLabelTarget("some", "all"), "none");
  const initial = { usa: "all", georgia: "some" };
  assert.deepEqual(labelChanges(initial, { usa: "all", georgia: "none" }), [{ action: "remove_label", label_id: "georgia" }]);
  assert.deepEqual(labelChanges(initial, { georgia: "all" }), [{ action: "add_label", label_id: "georgia" }]);
});

test("a proposal prefills its dialog; an unknown category falls back to the first one", () => {
  const page = { kind: "page", target_id: "fees", parent_id: "during", title_he: "דמי", description: "ת", scope: "ס" };
  assert.deepEqual(proposalForm(page, OVERVIEW), { id: "fees", title: "דמי", description: "ת", scope: "ס", parentId: "during", kind: "" });
  assert.equal(proposalForm({ ...page, parent_id: "invented" }, OVERVIEW).parentId, "before");
  assert.equal(proposalForm({ kind: "label", label_kind: "country" }, OVERVIEW).kind, "country");
});

test("what a plan change does to the groups, in words", () => {
  assert.deepEqual(assignmentLines(null, OVERVIEW), []);
  const none = { redirected: 0, reask: 0, screen_pages: [], asked_labels: [], pages_cleared: 0, labels_cleared: 0, manual_lost: 0, manual_reask: 0 };
  assert.deepEqual(assignmentLines(none, OVERVIEW), []);
  const lines = assignmentLines({ ...none, redirected: 1, reask: 12, screen_pages: ["costs"], manual_lost: 2, manual_reask: 1 }, OVERVIEW);
  assert.deepEqual(lines.map((line) => line.tone), ["info", "warning", "info", "danger", "info"]);
  assert.match(lines[0].text, /^השיוך של קבוצה אחת יעבור/);
  assert.match(lines[1].text, /הדף של 12 קבוצות/);
  assert.match(lines[2].text, /„עלויות”/);
  assert.match(lines[3].text, /^הדף שנמחק יוסר גם מ-2 קבוצות ששייכתם ביד\. השיוך הידני של קבוצה אחת יתבטל/);
  assert.equal(groupCount(3133, (n) => n.toLocaleString("en")), "3,133 קבוצות");
});

test("label findings say what to judge", () => {
  assert.match(findingText({ label_id: "usa", count: 913, share: 0.29, finding: "large" }, OVERVIEW), /„ארה״ב” מודבקת ל-29% מהקבוצות \(913\)/);
  assert.match(findingText({ label_id: "georgia", count: 4, share: 0.001, finding: "few" }, OVERVIEW), /רק ל-4 קבוצות/);
});
