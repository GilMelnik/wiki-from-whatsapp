import { formatCount, groupCount, Kbd, Ltr, Panel, useJobs, useStepRun } from "@shell";
import { ASSIGNMENTS_KEY, DETAILS_TAB, GROUPS_TAB } from "./assign.js";
import { Tabs } from "./bits.jsx";
import { GroupTable } from "./GroupTable.jsx";

// The assignment views that are not about one page or label: the lists of groups on no page
// and of groups waiting for the next assignment run, what to show before step 6b has run, and
// the tabs that switch a page or a label between its details and its groups.

const LIST_TEXTS = {
  unplaced: {
    title: "Groups on no page",
    text: "Groups the classifier did not place on any page with confidence (or that you took off every page by hand). For some of them the assignment step proposed a page, a label or a wider scope (in the proposals list); the rest can be placed here by hand, or left on no page.",
  },
  waiting: {
    title: "Waiting for the assignment run",
    text: "Groups whose pages were all deleted from the plan, and new groups not assigned yet. The next assignment run will ask about them again; a group that already has an assignment can also be moved by hand.",
  },
};

/** The step-6b run button (the frame confirms a paid run first). */
export function RunAssignButton({ label = "Run the assignment step…" }) {
  const run = useStepRun();
  const { stepsByArtifact, runningByArtifact } = useJobs();
  const step = stepsByArtifact[ASSIGNMENTS_KEY];
  const running = runningByArtifact[ASSIGNMENTS_KEY];
  return (
    <button type="button" className="btn btn-sm" disabled={!step || Boolean(running)} onClick={() => run(step.key)}>
      {running ? "The assignment step is running…" : label}
    </button>
  );
}

export function NotRunYet() {
  return (
    <div className="note note-info space-y-2">
      <p>
        Step 6b (assigning the claim groups to pages and labels) has not run yet. Run it once the plan is reviewed (
        <Ltr>python -m step_6_plan.assign</Ltr>); then this is where you see and fix where every group went, and how big
        every page is.
      </p>
      <RunAssignButton />
    </div>
  );
}

export function GroupsPanel({ scope, overview, ...groups }) {
  const { title, text } = LIST_TEXTS[scope];
  const summary = overview.assignments;
  return (
    <Panel title={title} subtitle={summary?.exists ? groupCount(summary.counts[scope], formatCount) : null}>
      <div className="space-y-4 p-4 text-sm">
        <p className="text-slate-600">{text}</p>
        {summary?.exists ? <GroupTable key={scope} overview={overview} scope={scope} {...groups} /> : <NotRunYet />}
      </div>
    </Panel>
  );
}

/** "Details" | "Claim groups (N)" above a page or a label. */
export function ItemTabs({ count, tab, onChange }) {
  const tabs = [
    { id: DETAILS_TAB, label: "Details" },
    { id: GROUPS_TAB, label: count === undefined ? "Claim groups" : `Claim groups (${formatCount(count)})` },
  ];
  return (
    <div className="flex items-center gap-2">
      <Tabs tabs={tabs} value={tab} onChange={onChange} />
      <span className="text-xs text-slate-500">
        Switch <Kbd>g</Kbd>
      </span>
    </div>
  );
}

/** The groups of a page or a label, or why there are none yet. */
export function ItemGroups({ scope, itemId, groups }) {
  if (!groups.overview.assignments?.exists) return <NotRunYet />;
  return <GroupTable key={`${scope}:${itemId}`} scope={scope} itemId={itemId} {...groups} />;
}
