import { Bidi, Kbd, Ltr, Panel, PanelState } from "@shell";
import { MARK, restoreLabel, typeLabel } from "./review.js";

// The side column: the decision on the selected claim, with only the actions that fit it. To
// decide: which removed values to put back (none marked = all), then keep the removal or
// restore. Decided: undo. "Undo the previous decision" stays offered after moving on.

function ActionButton({ label, keyName, primary = false, disabled, onClick }) {
  return (
    <button type="button" className={`btn btn-block justify-between ${primary ? "btn-primary" : ""}`} disabled={disabled} onClick={onClick}>
      <span>{label}</span>
      <Kbd>{keyName}</Kbd>
    </button>
  );
}

function RedactionChoices({ claim, chosen, onToggle, disabled }) {
  return (
    <ul className="space-y-2">
      {claim.redactions.map((redaction, index) => (
        <li key={index}>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={chosen.includes(index)} disabled={disabled} onChange={() => onToggle(index)} />
            <Kbd>{index + 1}</Kbd>
            <span className="w-12 shrink-0 text-xs text-slate-500">{typeLabel(redaction.type)}</span>
            <Ltr className="break-all">{redaction.value}</Ltr>
          </label>
        </li>
      ))}
    </ul>
  );
}

function ToDecide({ claim, chosen, saving, onToggle, onAccept, onRestore }) {
  const several = claim.redactions.length > 1;
  return (
    <div className="space-y-3">
      <p className="text-slate-700">Should the detail stay out of the claim, or go back in?</p>
      {several && claim.marks_match && (
        <>
          <RedactionChoices claim={claim} chosen={chosen} onToggle={onToggle} disabled={saving} />
          <p className="text-xs text-slate-500">Mark details (or press their number) to restore only those.</p>
        </>
      )}
      <ActionButton label={<>Keep the removal: <Bidi>{MARK}</Bidi></>} keyName="a" primary disabled={saving} onClick={onAccept} />
      {claim.marks_match && <ActionButton label={restoreLabel(chosen.length, claim.redactions.length)} keyName="r" disabled={saving} onClick={onRestore} />}
    </div>
  );
}

export function DecisionPanel({ detail, chosen, saving, canUndoPrevious, onToggle, onAccept, onRestore, onUndo }) {
  const claim = detail.data?.claim;
  const pending = claim?.status === "pending";
  return (
    <Panel
      title="Decide"
      footer={
        pending &&
        canUndoPrevious && <ActionButton label="Undo the previous decision" keyName="u" disabled={saving} onClick={onUndo} />
      }
    >
      <PanelState empty={!claim} emptyText="No claim selected">
        {claim && (
          <div className="space-y-4 p-4 text-sm">
            {pending ? (
              <ToDecide claim={claim} chosen={chosen} saving={saving} onToggle={onToggle} onAccept={onAccept} onRestore={onRestore} />
            ) : claim.can_undo ? (
              <ActionButton label="Undo the decision" keyName="u" disabled={saving} onClick={onUndo} />
            ) : (
              <p className="text-xs text-slate-500">Decided. It cannot be undone: the original file no longer has the removed details.</p>
            )}
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
