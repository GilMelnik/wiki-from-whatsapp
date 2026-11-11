import { Bidi, Kbd, Ltr, Panel, PanelState } from "@shell";
import { MARK, restoreLabel, typeLabel } from "./review.js";

// Side column: the decision on the selected claim. Pending: choose which removed values to
// restore (none chosen = all), then accept the removal or restore. Reviewed: undo.

function RedactionChoices({ claim, chosen, onToggle, disabled }) {
  return (
    <ul className="space-y-2">
      {claim.redactions.map((redaction, index) => (
        <li key={index}>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={chosen.includes(index)}
              disabled={disabled}
              onChange={() => onToggle(index)}
            />
            <Kbd>{index + 1}</Kbd>
            <span className="w-14 shrink-0 text-xs text-slate-500">{typeLabel(redaction.type)}</span>
            <Ltr className="break-all rounded bg-rose-50 px-1 text-rose-800">{redaction.value}</Ltr>
          </label>
        </li>
      ))}
    </ul>
  );
}

function ReviewedValues({ claim }) {
  if (claim.redactions.length === 0) {
    return <p className="text-slate-400">The original file no longer says what was removed from the claim.</p>;
  }
  return (
    <ul className="space-y-2">
      {claim.redactions.map((redaction, index) => (
        <li key={index} className="flex items-center gap-2">
          <span className={`badge ${redaction.in_text ? "badge-emerald" : "badge-slate"}`}>
            {redaction.in_text ? "Restored" : "Removed"}
          </span>
          <span className="w-14 shrink-0 text-xs text-slate-500">{typeLabel(redaction.type)}</span>
          <Ltr className="break-all">{redaction.value}</Ltr>
        </li>
      ))}
    </ul>
  );
}

function PendingFooter({ claim, chosen, saving, onAccept, onRestore, onUndo, canUndoPrevious }) {
  return (
    <>
      <button type="button" className="btn btn-primary btn-block" disabled={saving} onClick={onAccept}>
        Accept the removal – keep <Bidi>{MARK}</Bidi>
        <Kbd>a</Kbd>
      </button>
      <button
        type="button"
        className="btn btn-block"
        disabled={saving || !claim.marks_match}
        onClick={onRestore}
      >
        {restoreLabel(chosen.length, claim.redactions.length)}
        <Kbd>r</Kbd>
      </button>
      {canUndoPrevious && (
        <button type="button" className="btn btn-sm btn-block" disabled={saving} onClick={onUndo}>
          Undo the previous decision
          <Kbd>u</Kbd>
        </button>
      )}
    </>
  );
}

export function DecisionPanel({ detail, chosen, saving, canUndoPrevious, onToggle, onAccept, onRestore, onUndo }) {
  const claim = detail.data?.claim;
  const pending = claim?.status === "pending";
  const footer = !claim ? null : pending ? (
    <PendingFooter
      claim={claim}
      chosen={chosen}
      saving={saving}
      onAccept={onAccept}
      onRestore={onRestore}
      onUndo={onUndo}
      canUndoPrevious={canUndoPrevious}
    />
  ) : (
    <button type="button" className="btn btn-block" disabled={saving || !claim.can_undo} onClick={onUndo}>
      Undo the decision – the claim goes back to pending
      <Kbd>u</Kbd>
    </button>
  );
  return (
    <Panel
      title="Decision"
      subtitle={
        <>
          Saved at once to <Ltr>claims_edited.json</Ltr>
        </>
      }
      footer={footer}
    >
      <PanelState empty={!claim} emptyText="No claim selected">
        {claim && (
          <div className="space-y-6 p-4 text-sm">
            {pending ? (
              <section className="space-y-3">
                <p>Is the automatic removal right, or should the detail stay in the claim?</p>
                <RedactionChoices claim={claim} chosen={chosen} onToggle={onToggle} disabled={saving || !claim.marks_match} />
                {claim.redactions.length > 1 && (
                  <p className="text-xs text-slate-500">Mark details (or press their number) to restore only those.</p>
                )}
              </section>
            ) : (
              <section>
                <h3 className="section-title">Details removed in step 3</h3>
                <ReviewedValues claim={claim} />
                {!claim.can_undo && (
                  <p className="mt-3 text-xs text-slate-500">
                    Cannot undo: in the original file, the claim no longer has removed details.
                  </p>
                )}
              </section>
            )}
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
