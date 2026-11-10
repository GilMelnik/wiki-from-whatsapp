import { Ltr, Panel, PanelState } from "@shell";
import { Key } from "./Key.jsx";

// Side column of the suggestions view: one key per decision. Merge and "not the same" are
// saved at once (the next suggestion comes up); skip only moves on; "u" forgets the last
// "not the same".

export function DecisionPanel({ pair, saving, canUndo, onMerge, onNotSame, onSkip, onUndo }) {
  const footer = pair && (
    <>
      <button type="button" className="btn btn-primary btn-block" disabled={saving} onClick={onMerge}>
        מיזוג – אותו דבר
        <Key>m</Key>
      </button>
      <button type="button" className="btn btn-block" disabled={saving} onClick={onNotSame}>
        לא אותו דבר – לא להציע שוב
        <Key>n</Key>
      </button>
      <button type="button" className="btn btn-block" disabled={saving} onClick={onSkip}>
        דילוג – אחליט אחר כך
        <Key>s</Key>
      </button>
      {canUndo && (
        <button type="button" className="btn btn-sm btn-block" disabled={saving} onClick={onUndo}>
          ביטול ה&quot;לא אותו דבר&quot; האחרון
          <Key>u</Key>
        </button>
      )}
    </>
  );
  return (
    <Panel
      title="החלטה"
      subtitle={
        <>
          נשמרת מיד בקובץ <Ltr>claims_aggregated_edited.json</Ltr>
        </>
      }
      footer={footer}
    >
      <PanelState empty={!pair} emptyText="אין הצעה נבחרת">
        {pair && (
          <div className="space-y-3 p-4 text-sm text-slate-600">
            <p>מיזוג מצרף את קבוצה ב לקבוצה א; המספרים מחושבים מחדש כמו בריצה של שלב 5.</p>
            <p className="text-xs text-slate-500">
              &quot;לא אותו דבר&quot; נשמר לכל זוג טענות בין הקבוצות, ולכן נשאר גם אחרי מיזוגים, פיצולים וריצות חוזרות
              של שלב 5.
            </p>
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
