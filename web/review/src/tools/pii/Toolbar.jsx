import { formatCount, Ltr } from "@shell";
import { FILTERS, ORDERS, SORTS, STATUS, typeCounts } from "./review.js";

// Toolbar: which claims are listed and in what order, the review counters and the file read.

function Select({ label, value, options, onChange }) {
  return (
    <label className="flex items-center gap-1">
      {label}
      <select className="field" value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function Counters({ summary }) {
  if (!summary) return null;
  const { review } = summary;
  const pendingTypes = typeCounts(summary.pending_by_type);
  return (
    <div className="ms-auto flex flex-wrap items-center gap-2 text-xs text-slate-600">
      {Object.entries(STATUS).map(([status, { label, badge }]) => (
        <span key={status} className={`badge ${badge}`}>
          {label}: {formatCount(review[status])}
        </span>
      ))}
      {pendingTypes && <span>ממתינים לפי סוג: {pendingTypes}</span>}
      <span title={summary.edited_exists ? undefined : "ההחלטה הראשונה תיצור את העותק הערוך"}>
        קורא מ-<Ltr>{summary.file}</Ltr>
      </span>
    </div>
  );
}

export function Toolbar({ view, onFilter, onSort, onOrder, summary }) {
  return (
    <>
      <Select label="סינון" value={view.filter} options={FILTERS} onChange={onFilter} />
      <Select label="מיון" value={view.sort} options={SORTS} onChange={onSort} />
      <Select label="כיוון" value={view.order} options={ORDERS} onChange={onOrder} />
      <Counters summary={summary} />
    </>
  );
}
