import { formatCount, Ltr, Panel, PanelState } from "@shell";
import { pickedIn } from "./groups.js";
import { Key } from "./Key.jsx";

// Side column of the groups view: what can be done with the selected group. Pick member
// claims in the main column to split them off or move one; every change is saved at once.

export function GroupActions({ detail, picked, saving, on }) {
  const group = detail.data?.group;
  const chosen = group ? pickedIn(picked, group.group_id) : [];
  const canSplit = group && chosen.length > 0 && chosen.length < group.size;
  const footer = group && (
    <>
      <button type="button" className="btn btn-primary btn-block" disabled={saving} onClick={on.merge}>
        מיזוג עם קבוצה אחרת
        <Key>m</Key>
      </button>
      <button type="button" className="btn btn-block" disabled={saving || !canSplit} onClick={on.split}>
        {canSplit ? `פיצול ${formatCount(chosen.length)} טענות לקבוצה חדשה` : "פיצול (בחרו טענות)"}
        <Key>x</Key>
      </button>
      <button type="button" className="btn btn-block" disabled={saving || chosen.length !== 1} onClick={on.move}>
        העברת הטענה שנבחרה לקבוצה אחרת
        <Key>v</Key>
      </button>
      <button type="button" className="btn btn-block" disabled={saving} onClick={on.addLink}>
        הוספת קישור סתירה
        <Key>c</Key>
      </button>
      {group.representative_manual && (
        <button type="button" className="btn btn-sm btn-block" disabled={saving} onClick={() => on.setRepresentative(null)}>
          בחירה אוטומטית של הטקסט המייצג
        </button>
      )}
    </>
  );
  return (
    <Panel
      title="פעולות"
      subtitle={
        <>
          נשמר מיד בקובץ <Ltr>claims_aggregated_edited.json</Ltr>
        </>
      }
      footer={footer}
    >
      <PanelState empty={!group} emptyText="אין קבוצה נבחרת">
        {group && (
          <div className="space-y-3 p-4 text-sm text-slate-600">
            <p>
              נבחרו {formatCount(chosen.length)} מתוך {formatCount(group.size)} טענות.
            </p>
            <p className="text-xs text-slate-500">
              מיזוג שומר את הקבוצה הוותיקה יותר. פיצול יוצר קבוצה חדשה, והשתיים לא יוצעו למיזוג שוב. המספרים
              מחושבים מחדש מיד, כמו בריצה של שלב 5.
            </p>
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
