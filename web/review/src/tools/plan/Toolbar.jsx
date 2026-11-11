import { countText, formatCount } from "@shell";
import { Key, Tabs } from "./bits.jsx";
import { LABELS_VIEW, OVERVIEW_ID, TREE_VIEW } from "./plan.js";

// Toolbar: tree / labels, the page budget, warnings and the undo of the last change.

const VIEWS = [
  { id: TREE_VIEW, label: "Page tree" },
  { id: LABELS_VIEW, label: "Labels" },
];

export function Toolbar({ overview, view, onView, onSelect, onUndo, saving }) {
  const budget = overview?.budget;
  return (
    <>
      <Tabs tabs={VIEWS} value={view} onChange={onView} />
      <span className="text-xs text-slate-400">
        Switch <Key>l</Key>
      </span>
      {budget && (
        <span className={`badge ${budget.pages >= budget.max_pages ? "badge-amber" : "badge-slate"}`}>
          {formatCount(budget.pages)} of {formatCount(budget.max_pages)} pages · {countText(budget.labels, "label", formatCount)}
        </span>
      )}
      {overview?.warnings?.length > 0 && (
        <button type="button" className="badge badge-amber hover:bg-amber-200" onClick={() => onSelect(OVERVIEW_ID)}>
          {countText(overview.warnings.length, "warning", formatCount)}
        </button>
      )}
      <span className="flex-1" />
      {overview?.undo && (
        <span className="max-w-xs truncate text-xs text-slate-500" title={overview.undo.label}>
          Last: {overview.undo.label}
        </span>
      )}
      <button type="button" className="btn btn-sm" disabled={saving || !overview?.undo} onClick={onUndo}>
        Undo <Key>u</Key>
      </button>
    </>
  );
}
