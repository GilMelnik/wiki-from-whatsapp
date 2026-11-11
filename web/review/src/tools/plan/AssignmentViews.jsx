import { formatCount, formatDateTime, groupCount, Ltr, Panel, useJobs, useStepRun } from "@shell";
import { ASSIGNMENTS_KEY, DETAILS_TAB, findingText, GROUPS_TAB, PROPOSALS_ID, UNPLACED_ID, WAITING_ID } from "./assign.js";
import { Key, Tabs } from "./bits.jsx";
import { GroupTable } from "./GroupTable.jsx";

// The assignment views that are not about one page or label: the lists of groups on no page
// and of groups waiting for the next assignment run, the overview's numbers (with the labels
// that attach to very few groups or to a very large share), and what to show before step 6b
// has run, and the tabs that switch a page or a label between its details and its groups.

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
    <div className="space-y-2 rounded border border-slate-200 bg-slate-50 p-3 text-slate-600">
      <p>
        The assignment step (6b) has not run yet: no claim groups are assigned to pages and labels yet.
        Run it once the plan is reviewed (<Ltr>python -m step_6_plan.assign</Ltr>); then this is where
        you see and fix where every group went.
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

function Count({ value, label, onClick, tone = "" }) {
  const body = (
    <>
      <span className={`block text-xl font-semibold ${tone}`}>{formatCount(value)}</span>
      <span className="text-xs text-slate-500">{label}</span>
    </>
  );
  return onClick ? (
    <button type="button" className="rounded border border-slate-200 bg-white p-2 text-start hover:bg-slate-50" onClick={onClick}>
      {body}
    </button>
  ) : (
    <div className="rounded border border-slate-200 bg-white p-2">{body}</div>
  );
}

export function AssignmentSummary({ overview, onSelect }) {
  const summary = overview.assignments;
  const newer = overview.newer_groups;
  if (!summary?.exists) return <NotRunYet />;
  if (summary.problem) return <p className="text-rose-700">{summary.problem}</p>;
  const { counts } = summary;
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        By <Ltr>{summary.classifier ?? "—"}</Ltr>, run {formatDateTime(summary.generated_at)}
        {summary.edited_exists && " · including the changes you made here"} · <Ltr>{summary.file}</Ltr>
      </p>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Count value={counts.groups} label="Groups assigned" />
        <Count value={counts.unplaced} label="On no page" tone={counts.unplaced ? "text-amber-700" : ""} onClick={() => onSelect(UNPLACED_ID)} />
        <Count value={counts.two_pages} label="On two pages" />
        <Count value={counts.manual} label="Assigned by hand" />
        {counts.waiting > 0 && <Count value={counts.waiting} label="Waiting for a run" tone="text-amber-700" onClick={() => onSelect(WAITING_ID)} />}
        <Count value={summary.proposals.pending} label="Pending proposals" tone={summary.proposals.pending ? "text-amber-700" : ""} onClick={() => onSelect(PROPOSALS_ID)} />
      </div>
      {counts.followed > 0 && (
        <p className="text-xs text-amber-800">
          The assignment of {groupCount(counts.followed, formatCount)} still names ids that were merged or
          deleted from the plan. The view already reads them by today's plan, and the next assignment run
          will update the file too.
        </p>
      )}
      {newer && newer.count > 0 && (
        <p>
          Since the planning, {groupCount(newer.count, formatCount)} were added that the model did not see;
          {formatCount(newer.assigned)} of them are already placed. The assignment step places the rest,
          without planning again.
        </p>
      )}
      {summary.label_findings.length > 0 && (
        <section>
          <h4 className="mb-1 text-xs font-medium text-slate-600">Labels worth checking</h4>
          <ul className="space-y-1">
            {summary.label_findings.map((finding) => (
              <li key={finding.label_id}>
                <button type="button" className="text-start hover:underline" onClick={() => onSelect(finding.label_id)}>
                  {findingText(finding, overview, formatCount)}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
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
      <span className="text-xs text-slate-400">
        Switch <Key>g</Key>
      </span>
    </div>
  );
}

/** The groups of a page or a label, or why there are none yet. */
export function ItemGroups({ scope, itemId, groups }) {
  if (!groups.overview.assignments?.exists) return <NotRunYet />;
  return <GroupTable key={`${scope}:${itemId}`} scope={scope} itemId={itemId} {...groups} />;
}
