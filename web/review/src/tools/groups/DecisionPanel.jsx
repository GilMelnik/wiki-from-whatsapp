import { Ltr, Panel, PanelState } from "@shell";
import { Key } from "./Key.jsx";

// Side column of the suggestions view: one key per decision. Merge and "not the same" are
// saved at once (the next suggestion comes up); skip only moves on; "u" forgets the last
// "not the same".

export function DecisionPanel({ pair, saving, canUndo, onMerge, onNotSame, onSkip, onUndo }) {
  const footer = pair && (
    <>
      <button type="button" className="btn btn-primary btn-block" disabled={saving} onClick={onMerge}>
        Merge – the same
        <Key>m</Key>
      </button>
      <button type="button" className="btn btn-block" disabled={saving} onClick={onNotSame}>
        Not the same – do not suggest again
        <Key>n</Key>
      </button>
      <button type="button" className="btn btn-block" disabled={saving} onClick={onSkip}>
        Skip – decide later
        <Key>s</Key>
      </button>
      {canUndo && (
        <button type="button" className="btn btn-sm btn-block" disabled={saving} onClick={onUndo}>
          Undo the last “not the same”
          <Key>u</Key>
        </button>
      )}
    </>
  );
  return (
    <Panel
      title="Decision"
      subtitle={
        <>
          Saved at once to <Ltr>claims_aggregated_edited.json</Ltr>
        </>
      }
      footer={footer}
    >
      <PanelState empty={!pair} emptyText="No suggestion selected">
        {pair && (
          <div className="space-y-3 p-4 text-sm text-slate-600">
            <p>A merge adds group B to group A; the numbers are recomputed as in a step 5 run.</p>
            <p className="text-xs text-slate-500">
              “Not the same” is saved for every pair of claims between the groups, so it holds even after merges,
              splits and re-runs of step 5.
            </p>
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
