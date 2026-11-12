import assert from "node:assert/strict";
import { test } from "node:test";
import {
  assignmentRows,
  isAssignmentRow,
  nextProposalRow,
  proposalOfRow,
  proposalOperation,
  proposalRowId,
  PROPOSALS_ID,
  UNPLACED_ID,
  WAITING_ID,
} from "../src/tools/plan/assign.js";
import { overviewTodos } from "../src/tools/plan/todos.js";

// Synthetic overviews in the shape GET /api/plan/plan answers (only what these helpers read).
const node = (id, kind, parent = null) => ({ id, kind, title_he: `כותרת ${id}`, parent_id: parent, children: [] });
const BASE = {
  nodes: [node("before", "category"), node("agencies", "page", "before"), node("costs", "page", "before")],
  pages: { agencies: { title_he: "סוכנויות", scope: "a" }, costs: { title_he: "עלויות", scope: "b" } },
  labels: { usa: { title_he: "ארה״ב", kind: "country" } },
  warnings: [],
};
const summary = (counts, pending = 0, findings = []) => ({
  exists: true,
  problem: null,
  counts: { unplaced: 0, waiting: 0, ...counts },
  proposals: { pending, accepted: 0, rejected: 0 },
  label_findings: findings,
});
const proposal = (id, status = "pending", fields = {}) => ({ id, status, kind: "page", title_he: id, groups: [], target: {}, ...fields });

test("the overview's to-dos: warnings to act on first, each opening what it names", () => {
  const overview = {
    ...BASE,
    assignments: summary({ unplaced: 4, waiting: 2 }, 3, [{ label_id: "usa", count: 1, share: 0.01, finding: "few" }]),
    warnings: [
      { code: "split_page", ids: ["agencies"], tone: "warning", text: "Split it." },
      { code: "large_page", ids: ["costs"], tone: "info", text: "A look." },
      { code: "page_budget", ids: [], text: "Full." },
    ],
  };
  const todos = overviewTodos(overview);
  assert.deepEqual(
    todos.map((todo) => [todo.tone, todo.target]),
    [
      ["warning", PROPOSALS_ID],
      ["warning", UNPLACED_ID],
      ["warning", "agencies"],
      ["warning", null], // a warning that names nothing opens nothing
      ["info", WAITING_ID],
      ["info", "usa"],
      ["info", "costs"],
    ]
  );
  assert.equal(todos[0].text, "3 proposals of the assignment step to change the plan wait for a decision.");
  assert.equal(todos[1].text, "4 groups on no page: place them by hand, or leave them off the wiki.");
  assert.match(todos[5].text, /only 1 group:/);
});

test("no to-dos when nothing waits; none from step 6b before it has run", () => {
  assert.deepEqual(overviewTodos({ ...BASE, assignments: summary({}) }), []);
  assert.deepEqual(overviewTodos({ ...BASE, assignments: { exists: false } }), []);
  const broken = { ...BASE, assignments: { exists: true, problem: "unreadable" }, warnings: [{ code: "x", ids: ["costs"], text: "W" }] };
  assert.deepEqual(overviewTodos(broken).map((todo) => todo.key), ["x:costs"]);
  assert.deepEqual(overviewTodos(undefined), []);
});

test("proposals still to decide get a row of their own under the proposals row", () => {
  const items = [proposal("p1"), proposal("p2", "rejected"), proposal("p3")];
  const rows = assignmentRows({ ...BASE, assignments: summary({}) }, items);
  assert.deepEqual(rows.map((row) => row.id), [UNPLACED_ID, PROPOSALS_ID, proposalRowId("p1"), proposalRowId("p3")]);
  assert.ok(isAssignmentRow(proposalRowId("p1")) && !isAssignmentRow("p1"));
  assert.equal(proposalOfRow(items, proposalRowId("p3")).id, "p3");
  assert.equal(proposalOfRow(items, proposalRowId("gone")), null);
  assert.equal(proposalOfRow(items, "costs"), null);
});

test("after a decision the next proposal to decide is shown, else the list", () => {
  const items = [proposal("p1"), proposal("p2", "accepted"), proposal("p3"), proposal("p4")];
  assert.equal(nextProposalRow(items, "p1"), proposalRowId("p3"));
  assert.equal(nextProposalRow(items, "p4"), proposalRowId("p3")); // the last one: the one before
  assert.equal(nextProposalRow([proposal("p1")], "p1"), PROPOSALS_ID);
});

test("a proposal's dry run runs the operation accepting it unchanged would send", () => {
  const page = proposal("p1", "pending", { target_id: "funds", parent_id: "before", title_he: "קרנות", description: "ת", scope: "ס" });
  assert.deepEqual(proposalOperation(page, BASE), {
    op: "add_page",
    page_id: "funds",
    parent_id: "before",
    title_he: "קרנות",
    description: "ת",
    scope: "ס",
    aliases: { he: [], en: [] },
    position: null,
  });
  const label = proposal("p2", "pending", { kind: "label", target_id: "israel", label_kind: "country", title_he: "ישראל", description: "ד" });
  assert.deepEqual(proposalOperation(label, BASE), { op: "add_label", label_id: "israel", title_he: "ישראל", description: "ד", kind: "country" });
  const scope = proposal("p3", "pending", { kind: "scope", scope: "רחב", target: { page_id: "costs" } });
  assert.deepEqual(proposalOperation(scope, BASE), { op: "edit", item_id: "costs", changes: { scope: "רחב" } });
  assert.equal(proposalOperation({ ...scope, target: { page_id: null } }, BASE), null); // its page left the plan
});
