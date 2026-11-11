import { Bidi, kindOf, Ltr, nodeById, Panel, titleOf } from "@shell";
import { DETAILS_TAB, GROUPS_TAB, isAssignmentRow } from "./assign.js";
import { Key } from "./bits.jsx";
import { OVERVIEW_ID, reorderOp } from "./plan.js";

// Side column: what can be done with the selected item. Every button opens a dialog that
// shows the consequences first, except reordering, which only changes the navigation order.

function Button({ onClick, disabled, children, danger, title }) {
  return (
    <button type="button" title={title} className={`btn btn-sm btn-block justify-between ${danger ? "text-rose-700" : ""}`} disabled={disabled} onClick={onClick}>
      {children}
    </button>
  );
}

function Reorder({ node, saving, on }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <Button disabled={saving || !reorderOp(node, -1)} onClick={() => on.reorder(node.id, -1)}>
        ↑ למעלה <Key>Alt+↑</Key>
      </Button>
      <Button disabled={saving || !reorderOp(node, 1)} onClick={() => on.reorder(node.id, 1)}>
        ↓ למטה <Key>Alt+↓</Key>
      </Button>
    </div>
  );
}

function PageActions({ id, saving, on }) {
  return (
    <>
      <Button disabled={saving} onClick={() => on.open("split", id)}>פיצול דף חדש מתוכו… <Key>s</Key></Button>
      <Button disabled={saving} onClick={() => on.open("merge", id)}>מיזוג עם דפים אחרים… <Key>m</Key></Button>
      <Button disabled={saving} onClick={() => on.open("move", id)}>העברה לקטגוריה אחרת… <Key>t</Key></Button>
      <Button disabled={saving} onClick={() => on.open("changeId", id)}>החלפת המזהה…</Button>
      <Button danger disabled={saving} onClick={() => on.open("delete", id)}>מחיקת הדף… <Key>Del</Key></Button>
    </>
  );
}

function CategoryActions({ node, saving, on }) {
  return (
    <>
      <Button disabled={saving} onClick={() => on.open("addPage", null, { parentId: node.id })}>
        דף חדש בקטגוריה… <Key>n</Key>
      </Button>
      <Button disabled={saving} onClick={() => on.open("addCategory", null, { parentId: node.id })}>תת-קטגוריה חדשה…</Button>
      <Button disabled={saving} onClick={() => on.open("move", node.id)}>העברה… <Key>t</Key></Button>
      <Button disabled={saving} onClick={() => on.open("changeId", node.id)}>החלפת המזהה…</Button>
      <Button
        danger
        disabled={saving || node.children.length > 0}
        title={node.children.length > 0 ? "אפשר למחוק רק קטגוריה ריקה: העבירו או מחקו קודם את מה שבתוכה" : undefined}
        onClick={() => on.open("delete", node.id)}
      >
        מחיקת הקטגוריה… <Key>Del</Key>
      </Button>
      {node.children.length > 0 && <p className="text-xs text-slate-500">מוחקים רק קטגוריה ריקה.</p>}
    </>
  );
}

function LabelActions({ id, overview, saving, on }) {
  return (
    <>
      <Button disabled={saving} onClick={() => on.open("merge", id)}>מיזוג עם תוויות אחרות… <Key>m</Key></Button>
      <Button disabled={saving} onClick={() => on.open("changeId", id)}>החלפת המזהה…</Button>
      <Button danger disabled={saving} onClick={() => on.open("delete", id)}>מחיקת התווית… <Key>Del</Key></Button>
      <Button disabled={saving} onClick={() => on.open("addLabel", null, { kind: overview.labels[id].kind })}>
        תווית חדשה מאותו סוג… <Key>n</Key>
      </Button>
    </>
  );
}

function GroupsTab({ tab, on }) {
  const groups = tab === GROUPS_TAB;
  return (
    <Button onClick={() => on.showTab(groups ? DETAILS_TAB : GROUPS_TAB)}>
      {groups ? "חזרה לפרטים" : "קבוצות הטענות ששויכו…"} <Key>g</Key>
    </Button>
  );
}

function MoveHelp() {
  return (
    <div className="space-y-1 text-xs text-slate-600">
      <p>בוחרים קבוצות בתיבות (או <Key>a</Key> לכל המוצגות) ואז:</p>
      <p><Key>p</Key> העברה לדף · <Key>2</Key> דף נוסף · <Key>x</Key> הסרה מכאן · <Key>o</Key> מכל הדפים</p>
      <p><Key>b</Key> תוויות · <Key>r</Key> חזרה לשיוך אוטומטי · <Key>f</Key> חיפוש · <Key>u</Key> ביטול</p>
      <p>כל העברה נשמרת מיד ומסמנת את הקבוצה כמשויכת ביד: שלב השיוך לא יחליט עליה מחדש.</p>
    </div>
  );
}

export function ActionsPanel({ overview, selectedId, labelsView, saving, tab, on }) {
  const kind = kindOf(overview, selectedId);
  const node = nodeById(overview, selectedId);
  const special = !labelsView && isAssignmentRow(selectedId);
  return (
    <Panel
      title="פעולות"
      subtitle={
        selectedId && selectedId !== OVERVIEW_ID && kind ? (
          <>
            <Bidi>{titleOf(overview, selectedId)}</Bidi> <Ltr>{selectedId}</Ltr>
          </>
        ) : (
          "כל שינוי נבדק לפני השמירה"
        )
      }
      footer={
        <>
          {labelsView ? (
            <Button disabled={saving} onClick={() => on.open("addLabel", null, {})}>תווית חדשה…</Button>
          ) : (
            <Button disabled={saving} onClick={() => on.open("addCategory", null, { parentId: null })}>קטגוריה חדשה ברמה העליונה…</Button>
          )}
          <Button disabled={saving || !overview.undo} onClick={on.undo} title={overview.undo?.label}>
            ביטול השינוי האחרון <Key>u</Key>
          </Button>
        </>
      }
    >
      <div className="space-y-2 p-4 text-sm">
        {(kind === "page" || kind === "label") && <GroupsTab tab={tab} on={on} />}
        {kind === "page" && <PageActions id={selectedId} saving={saving} on={on} />}
        {kind === "category" && <CategoryActions node={node} saving={saving} on={on} />}
        {kind === "label" && <LabelActions id={selectedId} overview={overview} saving={saving} on={on} />}
        {node && <Reorder node={node} saving={saving} on={on} />}
        {(special || ((kind === "page" || kind === "label") && tab === GROUPS_TAB)) && <MoveHelp />}
        {!kind && !special && (
          <p className="text-slate-500">
            בחרו דף, קטגוריה או תווית ברשימה. שינויי טקסט נשמרים מיד; שינויי מבנה ותחום נפתחים בחלון
            שמראה קודם מה ייעלם, מה יופנה ואילו טענות יסווגו מחדש.
          </p>
        )}
        {overview.undo && (
          <p className="pt-2 text-xs text-slate-500">
            השינוי האחרון: <Bidi>{overview.undo.label}</Bidi>
          </p>
        )}
      </div>
    </Panel>
  );
}
