import { useEffect, useState } from "react";

const CONTACT_KINDS = [
  { key: "email", label: "אימייל" },
  { key: "phone", label: "טלפון" },
  { key: "website", label: "אתר" },
];

function linesToList(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function listToLines(items) {
  return (items || []).join("\n");
}

export default function EntityEditPanel({
  entity,
  saving,
  onRename,
  onSaveContacts,
  onDelete,
}) {
  const [name, setName] = useState("");
  const [contacts, setContacts] = useState({ email: "", phone: "", website: "" });

  useEffect(() => {
    if (!entity) return;
    setName(entity.canonical_name || "");
    setContacts({
      email: listToLines(entity.contacts?.email),
      phone: listToLines(entity.contacts?.phone),
      website: listToLines(entity.contacts?.website),
    });
  }, [entity?.entity_id, entity?.canonical_name, entity?.contacts]);

  if (!entity) {
    return (
      <div className="p-4 text-sm text-slate-500">בחר ישות לעריכה</div>
    );
  }

  const nameDirty = name.trim() !== (entity.canonical_name || "");
  const contactsDirty =
    listToLines(entity.contacts?.email) !== contacts.email ||
    listToLines(entity.contacts?.phone) !== contacts.phone ||
    listToLines(entity.contacts?.website) !== contacts.website;

  return (
    <div className="p-4 text-xs text-slate-600 space-y-4">
      <div>
        <span className="text-slate-400">מזהה: </span>
        <code>{entity.entity_id}</code>
      </div>

      <div>
        <label className="text-slate-500 block mb-1">שם ישות (קנוני)</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm"
        />
        <button
          type="button"
          disabled={saving || !name.trim() || !nameDirty}
          onClick={() => onRename(name.trim())}
          className="mt-2 text-xs px-2 py-1 rounded border border-slate-300 hover:bg-teal-50 disabled:opacity-40"
        >
          שמור שם
        </button>
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-slate-500">פרטי קשר</span>
          {entity.contacts_manual && (
            <span className="text-[10px] text-teal-700">נערך ידנית</span>
          )}
        </div>
        <p className="text-[11px] text-slate-400 mb-2">שורה אחת לכל ערך</p>
        {CONTACT_KINDS.map(({ key, label }) => (
          <div key={key} className="mb-2">
            <label className="text-slate-400 block mb-0.5">{label}</label>
            <textarea
              value={contacts[key]}
              onChange={(e) =>
                setContacts((prev) => ({ ...prev, [key]: e.target.value }))
              }
              rows={2}
              className="w-full border border-slate-300 rounded px-2 py-1 text-sm font-mono"
            />
          </div>
        ))}
        <button
          type="button"
          disabled={saving || !contactsDirty}
          onClick={() =>
            onSaveContacts({
              email: linesToList(contacts.email),
              phone: linesToList(contacts.phone),
              website: linesToList(contacts.website),
            })
          }
          className="text-xs px-2 py-1 rounded border border-slate-300 hover:bg-teal-50 disabled:opacity-40"
        >
          שמור פרטי קשר
        </button>
      </div>

      <div>
        <span className="text-slate-400">כינויים: </span>
        {entity.aliases?.join(", ") || "—"}
      </div>

      {entity.topics?.length > 0 && (
        <div>
          <span className="text-slate-400">נושאים: </span>
          {entity.topics.join(", ")}
        </div>
      )}

      <div className="pt-3 border-t border-slate-200">
        <button
          type="button"
          disabled={saving}
          onClick={() => {
            if (
              window.confirm(
                `למחוק את "${entity.canonical_name}"? השמות יחזרו לזיהוי נפרד בלי איחוד.`
              )
            ) {
              onDelete();
            }
          }}
          className="text-xs px-3 py-1.5 rounded border border-rose-300 text-rose-700 hover:bg-rose-50 disabled:opacity-40"
        >
          מחק ישות
        </button>
      </div>

      <div className="pt-2 border-t border-slate-200 text-slate-500 space-y-1">
        <p>סמן טענות ולחץ &quot;הזז טענות&quot; להעברתן לישות אחרת/חדשה.</p>
        <p>&quot;הזז שם&quot; מעביר כינוי שלם; &quot;מזג&quot; מאחד ישות זו עם אחרת.</p>
      </div>
    </div>
  );
}
