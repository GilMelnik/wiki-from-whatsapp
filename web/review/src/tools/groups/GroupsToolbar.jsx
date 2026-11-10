import { forwardRef } from "react";
import { formatCount, Ltr, Select } from "@shell";
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
        {formatCount(summary.group_count)} קבוצות · {formatCount(summary.multi_count)} עם כמה טענות ·{" "}
        {formatCount(summary.claim_count)} טענות
      </span>
      {summary.ungrouped_claims > 0 && (
        <span className="badge badge-amber">טענות מחוץ לקבוצות: {formatCount(summary.ungrouped_claims)}</span>
      )}
      <span className="badge badge-rose">עם סתירות: {formatCount(summary.contradiction_groups)}</span>
      <span title={summary.edited_exists ? undefined : "השינוי הראשון ייצור את העותק הערוך"}>
        קורא מ-<Ltr>{summary.file}</Ltr>
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
        placeholder="חיפוש בטקסט, ישות… (f)"
        value={query}
        onChange={(event) => onQuery(event.target.value)}
      />
      <Flag label="כמה טענות" checked={view.multi} onChange={(multi) => onView({ multi })} />
      <Flag label="עם סתירות" checked={view.contradictions} onChange={(contradictions) => onView({ contradictions })} />
      <Flag label="עם הצעות מיזוג" checked={view.suggested} onChange={(suggested) => onView({ suggested })} />
      <Select label="ישות" value={view.entity} options={entityOptions(summary?.entities)} onChange={(entity) => onView({ entity })} />
      <Select label="עמדה" value={view.stance} options={STANCE_FILTERS} onChange={(stance) => onView({ stance })} />
      <Select label="מיון" value={view.sort} options={SORTS} onChange={(sort) => onView({ sort })} />
      <Select label="כיוון" value={view.order} options={ORDERS} onChange={(order) => onView({ order })} />
      <Counters summary={summary} />
    </>
  );
});
