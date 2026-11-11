import { useState } from "react";
import { Bidi, countText, formatCount, formatDateTime, groupCount, Ltr, Panel, PanelState, useResource } from "@shell";
import { api } from "./api.js";
import { UNPLACED_ID } from "./assign.js";
import { AssignmentSummary } from "./AssignmentViews.jsx";
import { Warnings } from "./bits.jsx";
import { percent } from "./plan.js";

// Main column when the plan itself is selected: why the model built it this way, the page
// budget and the depth of each branch, every warning, where step 6b put the claim groups
// (and which labels look too narrow or too broad), the coverage check of the planning run
// (with its sampled claims that fit no page; it describes the model's proposal, so it stays
// separate from the real list of groups on no page) and what changed since the proposal.

function Budget({ overview }) {
  const { budget, branches } = overview;
  const full = budget.pages >= budget.max_pages;
  return (
    <section>
      <h3 className="section-title">Size</h3>
      <p className="mb-2">
        <span className={`text-2xl font-semibold ${full ? "text-amber-700" : ""}`}>
          {formatCount(budget.pages)} of {formatCount(budget.max_pages)}
        </span>{" "}
        pages · {countText(budget.labels, "label", formatCount)} · pages at depth {budget.min_depth}–{budget.max_depth}
      </p>
      <table className="w-full text-start">
        <thead className="text-xs text-slate-500">
          <tr>
            <th className="py-1 text-start font-medium">Branch</th>
            <th className="py-1 text-start font-medium">Pages</th>
            <th className="py-1 text-start font-medium">Depth</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {branches.map((branch) => (
            <tr key={branch.id}>
              <td className="py-1"><Bidi>{branch.title_he}</Bidi></td>
              <td className="py-1">{formatCount(branch.pages)}</td>
              <td className="py-1">{branch.depth || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function GroupList({ groups }) {
  return (
    <ul className="space-y-1">
      {groups.filter((group) => !group.missing).map((group) => (
        <li key={group.group_id} className="rounded border border-slate-200 bg-white p-2">
          <Bidi as="p">{group.claim_text}</Bidi>
          <Ltr className="text-xs text-slate-400">{group.group_id}</Ltr>
        </li>
      ))}
    </ul>
  );
}

function Unplaced({ reads }) {
  const unplaced = useResource(() => api.get("/coverage/unplaced"), [], { watch: reads });
  return (
    <PanelState loading={unplaced.loading} error={unplaced.error} hasData={Boolean(unplaced.data)} onRetry={unplaced.reload}>
      {unplaced.data && (
        <div className="space-y-3">
          <h4 className="text-xs font-medium text-slate-600">Fit no page</h4>
          <GroupList groups={unplaced.data.none} />
          <h4 className="text-xs font-medium text-slate-600">Unclear which page</h4>
          <GroupList groups={unplaced.data.unsure} />
        </div>
      )}
    </PanelState>
  );
}

function Coverage({ coverage, unplaced, reads, onSelect }) {
  const [open, setOpen] = useState(false);
  if (!coverage) return <p className="text-slate-400">No coverage report (it is made at planning time).</p>;
  return (
    <div className="space-y-2">
      {unplaced !== null && (
        <p className="rounded bg-slate-50 p-2 text-xs text-slate-600">
          This check describes the planning's sample. The real list after the assignment step:{" "}
          <button type="button" className="badge badge-amber" onClick={() => onSelect(UNPLACED_ID)}>
            {groupCount(unplaced, formatCount)} on no page
          </button>
        </p>
      )}
      <p>
        The cheap classifier placed {formatCount(coverage.sample_size)} of {groupCount(coverage.total_groups, formatCount)} by
        the titles and scopes alone: <b>{percent(coverage.confident_share)}</b> with confidence,{" "}
        {percent(coverage.unsure_share)} without confidence ({formatCount(coverage.unsure_count)}),{" "}
        {percent(coverage.none_share)} on no page ({formatCount(coverage.none_count)}).
      </p>
      <p className="text-xs text-slate-500">
        Checked {formatDateTime(coverage.generated_at)} on the plan as the model proposed it; changes since are not measured.
      </p>
      {coverage.findings?.length > 0 && (
        <ul className="list-disc space-y-1 ps-5 text-xs text-slate-600" dir="ltr">
          {coverage.findings.map((finding) => (
            <li key={finding}>{finding}</li>
          ))}
        </ul>
      )}
      <button type="button" className="btn btn-sm" onClick={() => setOpen(!open)}>
        {open ? "Hide" : "Show"} the claims not placed with confidence
      </button>
      {open && <Unplaced reads={reads} />}
    </div>
  );
}

function Changes({ changes, onSelect }) {
  const count = Object.keys(changes.items).length;
  if (count === 0 && changes.removed.length === 0) return <p className="text-slate-500">The plan is still as the model proposed it.</p>;
  return (
    <div className="space-y-2">
      <p>{countText(count, "item", formatCount)} new or edited (marked in the list).</p>
      {changes.removed.length > 0 && (
        <ul className="space-y-1">
          {changes.removed.map((item) => (
            <li key={item.id}>
              <Bidi>{item.title_he}</Bidi> <Ltr className="text-xs text-slate-400">{item.id}</Ltr>{" "}
              {item.redirect ? (
                <>
                  → redirected to{" "}
                  <button type="button" className="badge badge-sky" onClick={() => onSelect(item.redirect)}>
                    <Ltr>{item.redirect}</Ltr>
                  </button>
                </>
              ) : (
                <span className="badge badge-rose">Deleted</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Overview({ overview, reads, on }) {
  const { metadata } = overview;
  return (
    <Panel
      title="Plan overview"
      subtitle={
        <>
          {metadata.model && <Ltr>{metadata.model}</Ltr>} · planned {formatDateTime(metadata.generated_at)}
          {metadata.edited_at && <> · edited {formatDateTime(metadata.edited_at)}</>} · <Ltr>{overview.file}</Ltr>
        </>
      }
    >
      <div className="space-y-6 p-4 text-sm">
        {metadata.top_level_rationale && (
          <section>
            <h3 className="section-title">Why this structure (from the model)</h3>
            <Bidi as="p" className="leading-relaxed">{metadata.top_level_rationale}</Bidi>
          </section>
        )}
        <Budget overview={overview} />
        <section>
          <h3 className="section-title">Groups assigned to pages (step 6b)</h3>
          <AssignmentSummary overview={overview} onSelect={on.select} />
        </section>
        <section>
          <h3 className="section-title">Warnings ({formatCount(overview.warnings.length)})</h3>
          {overview.warnings.length === 0 ? <p className="text-slate-500">No warnings.</p> : <Warnings overview={overview} warnings={overview.warnings} onSelect={on.select} />}
        </section>
        <section>
          <h3 className="section-title">Coverage check of the planning</h3>
          <Coverage coverage={overview.coverage} unplaced={overview.assignments?.counts?.unplaced ?? null} reads={reads} onSelect={on.select} />
        </section>
        <section>
          <h3 className="section-title">Changes since the proposal</h3>
          <Changes changes={overview.changes} onSelect={on.select} />
        </section>
      </div>
    </Panel>
  );
}
