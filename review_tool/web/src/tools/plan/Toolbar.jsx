import { Kbd } from "@shell";
import { Tabs } from "./bits.jsx";
import { LABELS_VIEW, TREE_VIEW } from "./plan.js";

// Toolbar: the page tree or the labels, and the undo of the last change (named).

const VIEWS = [
  { id: TREE_VIEW, label: "Page tree" },
  { id: LABELS_VIEW, label: "Labels" },
];

export function Toolbar({ overview, view, onView, onUndo, saving }) {
  return (
    <>
      <Tabs tabs={VIEWS} value={view} onChange={onView} />
      <span className="text-xs text-slate-500">
        Switch <Kbd>l</Kbd>
      </span>
      <span className="flex-1" />
      {overview?.undo && (
        <span className="max-w-xs truncate text-xs text-slate-500" title={overview.undo.label}>
          Last change: {overview.undo.label}
        </span>
      )}
      <button type="button" className="btn btn-sm" disabled={saving || !overview?.undo} onClick={onUndo}>
        Undo <Kbd>u</Kbd>
      </button>
    </>
  );
}
