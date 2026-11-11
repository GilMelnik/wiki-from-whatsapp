import { forwardRef } from "react";
import { countText, formatCount, Ltr, Select } from "@shell";
import { entityOptions, ORDERS, SORTS, STANCE_FILTERS } from "./groups.js";

// Toolbar of the groups view: search, filters, order, counters and the file read.

function Flag({ label, checked, onChange }) {
  return (
    <label className="flex items-center gap-1">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      {label}
    </label>
  );
}

export function Counters({ summary }) {
  if (!summary) return null;
  return (
    <div className="ms-auto flex flex-wrap items-center gap-2 text-xs text-slate-600">
      <span>
        {countText(summary.group_count, "group", formatCount)} · {formatCount(summary.multi_count)} with several claims ·{" "}
        {countText(summary.claim_count, "claim", formatCount)}
      </span>
      {summary.ungrouped_claims > 0 && (
        <span className="badge badge-amber">Claims outside groups: {formatCount(summary.ungrouped_claims)}</span>
      )}
      <span className="badge badge-rose">With contradictions: {formatCount(summary.contradiction_groups)}</span>
      <span title={summary.edited_exists ? undefined : "The first change will create the edited copy"}>
        Reading <Ltr>{summary.file}</Ltr>
      </span>
    </div>
  );
}

export const GroupsToolbar = forwardRef(function GroupsToolbar({ modeTabs, query, onQuery, view, onView, summary }, searchRef) {
  return (
    <>
      {modeTabs}
      <input
        ref={searchRef}
        type="search"
        dir="auto"
        className="field w-56"
        placeholder="Search text, entities… (f)"
        value={query}
        onChange={(event) => onQuery(event.target.value)}
      />
      <Flag label="Several claims" checked={view.multi} onChange={(multi) => onView({ multi })} />
      <Flag label="With contradictions" checked={view.contradictions} onChange={(contradictions) => onView({ contradictions })} />
      <Flag label="With merge suggestions" checked={view.suggested} onChange={(suggested) => onView({ suggested })} />
      <Select label="Entity" value={view.entity} options={entityOptions(summary?.entities)} onChange={(entity) => onView({ entity })} />
      <Select label="Stance" value={view.stance} options={STANCE_FILTERS} onChange={(stance) => onView({ stance })} />
      <Select label="Sort" value={view.sort} options={SORTS} onChange={(sort) => onView({ sort })} />
      <Select label="Order" value={view.order} options={ORDERS} onChange={(order) => onView({ order })} />
      <Counters summary={summary} />
    </>
  );
});
