import { useEffect, useState } from "react";
import { Ltr } from "@shell";
import { CONTACT_KINDS, contactsChanged, contactsToForm, formToContacts } from "./entities.js";

// Contacts of the entity: the confident ones (editable; saving makes them manual, so step 4
// keeps them), the uncertain ones (seen only in claims naming several entities: accept, with
// a correction if needed, or reject), and the rejected ones (step 4 never brings them back;
// a rejection can be undone).

function UncertainRow({ kind, value, saving, onResolve }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const corrected = draft.trim() !== value ? draft.trim() : null;
  return (
    <li className="flex items-center gap-1">
      <input dir="ltr" className="field min-w-0 flex-1 font-mono text-xs" value={draft} onChange={(event) => setDraft(event.target.value)} />
      <button
        type="button"
        className="btn btn-sm"
        disabled={saving || !draft.trim()}
        onClick={() => onResolve({ kind, value, action: "accept", new_value: corrected })}
      >
        שיוך
      </button>
      <button type="button" className="btn btn-sm text-rose-700" disabled={saving} onClick={() => onResolve({ kind, value, action: "reject" })}>
        דחייה
      </button>
    </li>
  );
}

function ByKind({ contacts, render }) {
  return CONTACT_KINDS.map(({ key, label }) => {
    const values = contacts?.[key] ?? [];
    if (values.length === 0) return null;
    return (
      <div key={key}>
        <p className="text-xs text-slate-500">{label}</p>
        <ul className="space-y-1">{values.map((value) => render(key, value))}</ul>
      </div>
    );
  });
}

const hasAny = (contacts) => CONTACT_KINDS.some(({ key }) => (contacts?.[key] ?? []).length > 0);

export function Contacts({ entity, saving, onSave, onResolve }) {
  const [form, setForm] = useState(() => contactsToForm(entity.contacts));
  useEffect(() => setForm(contactsToForm(entity.contacts)), [entity.entity_id, entity.contacts]);
  const dirty = contactsChanged(entity.contacts, form);

  return (
    <>
      <section className="space-y-2">
        <h3 className="section-title">
          פרטי קשר {entity.contacts_manual && <span className="badge badge-sky ms-1">נערכו ידנית</span>}
        </h3>
        {CONTACT_KINDS.map(({ key, label }) => (
          <label key={key} className="block">
            <span className="text-xs text-slate-500">{label} (ערך בכל שורה)</span>
            <textarea
              dir="ltr"
              rows={2}
              className="field w-full font-mono text-xs"
              value={form[key]}
              onChange={(event) => setForm((previous) => ({ ...previous, [key]: event.target.value }))}
            />
          </label>
        ))}
        <button type="button" className="btn btn-sm" disabled={saving || !dirty} onClick={() => onSave(formToContacts(form))}>
          שמירת פרטי הקשר
        </button>
      </section>
      {hasAny(entity.contacts_uncertain) && (
        <section className="space-y-2 rounded border border-amber-200 bg-amber-50 p-3">
          <h3 className="section-title text-amber-800">פרטי קשר לא ודאיים</h3>
          <p className="text-xs text-amber-900">
            הופיעו רק בטענות שמזכירות כמה ישויות, ולכן לא ברור למי הם שייכים.
          </p>
          <ByKind
            contacts={entity.contacts_uncertain}
            render={(kind, value) => <UncertainRow key={value} kind={kind} value={value} saving={saving} onResolve={onResolve} />}
          />
        </section>
      )}
      {hasAny(entity.contacts_rejected) && (
        <section className="space-y-2">
          <h3 className="section-title">פרטי קשר שנדחו</h3>
          <ByKind
            contacts={entity.contacts_rejected}
            render={(kind, value) => (
              <li key={value} className="flex items-center gap-2">
                <Ltr className="flex-1 break-all text-slate-500 line-through">{value}</Ltr>
                <button type="button" className="btn btn-sm" disabled={saving} onClick={() => onResolve({ kind, value, action: "restore" })}>
                  ביטול הדחייה
                </button>
              </li>
            )}
          />
        </section>
      )}
    </>
  );
}
