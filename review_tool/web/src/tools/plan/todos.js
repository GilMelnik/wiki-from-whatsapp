// What the plan overview asks the owner to do (pure, no React): every warning about the plan,
// the proposals of the assignment step waiting for a decision, the groups on no page or
// waiting for the next assignment run, and the labels worth checking. Each to-do is
// `{key, tone, text, target}`: `target` is the list row it opens (a page, label, category or
// one of the assignment lists), `tone` "warning" (worth acting on) or "info" (worth a look).
// Counts go through `format` (the shell's `formatCount` in the app).

import { countText, groupCount } from "@shell";
import { findingText, PROPOSALS_ID, UNPLACED_ID, WAITING_ID } from "./assign.js";

export function overviewTodos(overview, format = String) {
  const todos = [];
  const summary = overview?.assignments;
  if (summary?.exists && !summary.problem) {
    const { counts } = summary;
    if (summary.proposals.pending > 0) {
      const text = `${countText(summary.proposals.pending, "proposal", format)} of the assignment step to change the plan wait for a decision.`;
      todos.push({ key: "proposals", tone: "warning", text, target: PROPOSALS_ID });
    }
    if (counts.unplaced > 0) {
      const text = `${groupCount(counts.unplaced, format)} on no page: place them by hand, or leave them off the wiki.`;
      todos.push({ key: "unplaced", tone: "warning", text, target: UNPLACED_ID });
    }
    if (counts.waiting > 0) {
      const text = `${groupCount(counts.waiting, format)} wait for the next assignment run (new groups, or groups whose pages were deleted).`;
      todos.push({ key: "waiting", tone: "info", text, target: WAITING_ID });
    }
    for (const finding of summary.label_findings ?? []) {
      todos.push({ key: `label:${finding.label_id}`, tone: "info", text: findingText(finding, overview, format), target: finding.label_id });
    }
  }
  for (const warning of overview?.warnings ?? []) {
    todos.push({ key: `${warning.code}:${warning.ids.join(",")}`, tone: warning.tone ?? "warning", text: warning.text, target: warning.ids[0] ?? null });
  }
  // What to act on first, then what is worth a look; each keeps its order.
  return [...todos.filter((todo) => todo.tone === "warning"), ...todos.filter((todo) => todo.tone !== "warning")];
}
