import { Ltr, Panel, PanelState } from "@shell";
import { restoreLabel, typeLabel } from "./review.js";

// Side column: the decision on the selected claim. Pending: choose which removed values to
// restore (none chosen = all), then accept the removal or restore. Reviewed: undo.

function Key({ children }) {
  return (
    <kbd dir="ltr" className="ms-1 rounded border border-current px-1 font-mono text-[10px] opacity-70">
      {children}
    </kbd>
  );
}

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
            <Key>{index + 1}</Key>
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
    return <p className="text-slate-400">הקובץ המקורי כבר לא מציין מה הוסר מהטענה.</p>;
  }
  return (
    <ul className="space-y-2">
      {claim.redactions.map((redaction, index) => (
        <li key={index} className="flex items-center gap-2">
          <span className={`badge ${redaction.in_text ? "badge-emerald" : "badge-slate"}`}>
            {redaction.in_text ? "שוחזר" : "הוסר"}
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
        אישור ההסרה – להשאיר [הוסר]
        <Key>a</Key>
      </button>
      <button
        type="button"
        className="btn btn-block"
        disabled={saving || !claim.marks_match}
        onClick={onRestore}
      >
        {restoreLabel(chosen.length, claim.redactions.length)}
        <Key>r</Key>
      </button>
      {canUndoPrevious && (
        <button type="button" className="btn btn-sm btn-block" disabled={saving} onClick={onUndo}>
          ביטול ההחלטה הקודמת
          <Key>u</Key>
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
      ביטול ההחלטה – הטענה תחזור לממתינות
      <Key>u</Key>
    </button>
  );
  return (
    <Panel
      title="החלטה"
      subtitle={
        <>
          נשמרת מיד בקובץ <Ltr>claims_edited.json</Ltr>
        </>
      }
      footer={footer}
    >
      <PanelState empty={!claim} emptyText="אין טענה נבחרת">
        {claim && (
          <div className="space-y-6 p-4 text-sm">
            {pending ? (
              <section className="space-y-3">
                <p>האם ההסרה האוטומטית נכונה, או שהפרט צריך להישאר בטענה?</p>
                <RedactionChoices claim={claim} chosen={chosen} onToggle={onToggle} disabled={saving || !claim.marks_match} />
                {claim.redactions.length > 1 && (
                  <p className="text-xs text-slate-500">סמנו פרטים (או הקישו את המספר) כדי לשחזר רק אותם.</p>
                )}
              </section>
            ) : (
              <section>
                <h3 className="section-title">הפרטים שהוסרו בשלב 3</h3>
                <ReviewedValues claim={claim} />
                {!claim.can_undo && (
                  <p className="mt-3 text-xs text-slate-500">
                    אי אפשר לבטל: בקובץ המקורי כבר אין לטענה פרטים שהוסרו.
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
