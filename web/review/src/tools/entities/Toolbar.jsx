import { forwardRef } from "react";
import { formatCount, Ltr, Select } from "@shell";
import { FLAG_FILTERS, ORDERS, SORTS, STATUS_FILTERS } from "./entities.js";

// Toolbar: search, which entities are listed and in what order, the review counters, the
// file read, and the deleted names.

function Counters({ summary, onDeletedNames }) {
  if (!summary) return null;
  const statuses = summary.by_status;
  return (
    <div className="ms-auto flex flex-wrap items-center gap-2 text-xs text-slate-600">
      <span>
        {formatCount(summary.entity_count)} ישויות · {formatCount(summary.multi_member_count)} עם כמה שמות
      </span>
      <span className="badge badge-emerald">אושרו: {formatCount(statuses.accepted ?? 0)}</span>
      <span className="badge badge-amber">דו-משמעיות: {formatCount(statuses.ambiguous ?? 0)}</span>
      <span className="badge badge-amber">שמות שצורפו אוטומטית: {formatCount(summary.attached_members)}</span>
      <button type="button" className="btn btn-sm" onClick={onDeletedNames}>
        שמות שנמחקו ({formatCount(summary.deleted_names)})
      </button>
      <span title={summary.edited_exists ? undefined : "השינוי הראשון ייצור את העותק הערוך"}>
        קורא מ-<Ltr>{summary.file}</Ltr>
      </span>
    </div>
  );
}

export const Toolbar = forwardRef(function Toolbar({ query, onQuery, view, onView, summary, onDeletedNames }, searchRef) {
  return (
    <>
      <input
        ref={searchRef}
        type="search"
        dir="auto"
        className="field w-56"
        placeholder="חיפוש שם, כינוי או מזהה… (f)"
        value={query}
        onChange={(event) => onQuery(event.target.value)}
      />
      <Select label="סטטוס" value={view.status} options={STATUS_FILTERS} onChange={(status) => onView({ status })} />
      <Select label="לבדיקה" value={view.flag} options={FLAG_FILTERS} onChange={(flag) => onView({ flag })} />
      <Select label="מיון" value={view.sort} options={SORTS} onChange={(sort) => onView({ sort })} />
      <Select label="כיוון" value={view.order} options={ORDERS} onChange={(order) => onView({ order })} />
      <Counters summary={summary} onDeletedNames={onDeletedNames} />
    </>
  );
});
