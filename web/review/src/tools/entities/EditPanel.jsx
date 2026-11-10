import { useEffect, useState } from "react";
import { Bidi, Ltr, Panel, PanelState } from "@shell";
import { Contacts } from "./Contacts.jsx";

// Side column: what can be done with the selected entity. Decisions in the footer (accept,
// merge, reject, delete); the name, contacts and claim grouping above. Every change is saved
// at once to entities_edited.json (claim groups to claim_aggregations.json).

function Key({ children }) {
  return (
    <kbd dir="ltr" className="ms-1 rounded border border-current px-1 font-mono text-[10px] opacity-70">
      {children}
    </kbd>
  );
}

function NameForm({ entity, saving, nameRef, onRename }) {
  const [name, setName] = useState(entity.canonical_name);
  useEffect(() => setName(entity.canonical_name), [entity.entity_id, entity.canonical_name]);
  const dirty = name.trim() !== entity.canonical_name && name.trim() !== "";
  return (
    <form
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (dirty && !saving) onRename(name.trim());
      }}
    >
      <h3 className="section-title">
        השם הקנוני {entity.canonical_manual && <span className="badge badge-sky ms-1">נקבע ידנית</span>}
      </h3>
      <input ref={nameRef} dir="auto" className="field w-full" value={name} onChange={(event) => setName(event.target.value)} />
      <button type="submit" className="btn btn-sm" disabled={saving || !dirty}>
        שמירת השם
      </button>
    </form>
  );
}

function Footer({ entity, saving, pickedCount, on }) {
  const accepted = entity.status === "accepted";
  return (
    <>
      {accepted ? (
        <button type="button" className="btn btn-block" disabled={saving} onClick={() => on.setStatus("suggested")}>
          החזרה להצעה (לבדיקה נוספת)
        </button>
      ) : (
        <button type="button" className="btn btn-primary btn-block" disabled={saving} onClick={() => on.setStatus("accepted")}>
          אישור הישות
          <Key>a</Key>
        </button>
      )}
      <div className="grid grid-cols-2 gap-2">
        <button type="button" className="btn btn-sm" disabled={saving} onClick={on.merge}>
          מיזוג לישות אחרת…
          <Key>m</Key>
        </button>
        <button type="button" className="btn btn-sm" disabled={saving} onClick={on.reject}>
          דחיית האיחוד…
          <Key>x</Key>
        </button>
        <button type="button" className="btn btn-sm" disabled={saving || pickedCount < 2} onClick={on.aggregate}>
          איחוד {pickedCount} טענות…
          <Key>g</Key>
        </button>
        <button type="button" className="btn btn-sm text-rose-700" disabled={saving} onClick={on.remove}>
          מחיקת הישות…
          <Key>Del</Key>
        </button>
      </div>
    </>
  );
}

export function EditPanel({ detail, saving, nameRef, pickedCount, on }) {
  const entity = detail.data?.entity;
  return (
    <Panel
      title="עריכה"
      subtitle={
        <>
          נשמר מיד בקובץ <Ltr>entities_edited.json</Ltr>
        </>
      }
      footer={entity && <Footer entity={entity} saving={saving} pickedCount={pickedCount} on={on} />}
    >
      <PanelState empty={!entity} emptyText="אין ישות נבחרת">
        {entity && (
          <div className="space-y-6 p-4 text-sm">
            <NameForm entity={entity} saving={saving} nameRef={nameRef} onRename={on.rename} />
            <Contacts entity={entity} saving={saving} onSave={on.saveContacts} onResolve={on.resolveContact} />
            <section>
              <h3 className="section-title">כל השמות</h3>
              <p className="flex flex-wrap gap-1">
                {entity.aliases.map((alias) => (
                  <Bidi key={alias} className="badge badge-slate">
                    {alias}
                  </Bidi>
                ))}
              </p>
            </section>
            <section className="space-y-1 text-xs text-slate-500">
              <p>סמנו טענות (תיבת הבחירה) כדי להעתיק, להעביר, לסמן כלא קשורות או לאחד אותן.</p>
              <p>„העברת השם” מעבירה כינוי שלם; „מיזוג” מעביר את כל השמות לישות אחרת.</p>
              <p>„דחיית האיחוד” מפרקת את הישות לשם אחד בכל ישות, רק עם הטענות שמסומנות בשם.</p>
            </section>
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
