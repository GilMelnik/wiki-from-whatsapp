import { formatCount, Ltr, Select } from "@shell";
import { FILTERS, ORDERS, SORTS, STATUS, typeCounts } from "./review.js";

// Toolbar: which claims are listed and in what order, the review counters and the file read.

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
      {pendingTypes && <span>Pending by type: {pendingTypes}</span>}
      <span title={summary.edited_exists ? undefined : "The first decision creates the edited copy"}>
        Reads from <Ltr>{summary.file}</Ltr>
      </span>
    </div>
  );
}

export function Toolbar({ view, onFilter, onSort, onOrder, summary }) {
  return (
    <>
      <Select label="Filter" value={view.filter} options={FILTERS} onChange={onFilter} />
      <Select label="Sort" value={view.sort} options={SORTS} onChange={onSort} />
      <Select label="Order" value={view.order} options={ORDERS} onChange={onOrder} />
      <Counters summary={summary} />
    </>
  );
}
