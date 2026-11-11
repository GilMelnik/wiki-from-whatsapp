import { forwardRef, useEffect, useState } from "react";
import { useIdCheck } from "./planApi.js";
import { normalizeId, PERMANENT_ID_NOTE } from "./planText.js";
import { Bidi } from "./text.jsx";

// Form pieces: labelled inputs, the id field that checks a new plan id with the plan tool's
// server while the owner types, and the inline editor of a text in the main column.

export function Field({ label, hint, children }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export function TextInput({ value, onChange, ...rest }) {
  return <input dir="auto" className="field w-full" value={value} onChange={(event) => onChange(event.target.value)} {...rest} />;
}

export const TextArea = forwardRef(function TextArea({ value, onChange, rows = 3, ...rest }, ref) {
  return (
    <textarea
      ref={ref}
      dir="auto"
      rows={rows}
      className="field w-full leading-relaxed"
      value={value}
      onChange={(event) => onChange(event.target.value)}
      {...rest}
    />
  );
});

/** For a <select> in a dialog: Enter confirms, as it does in a text field. */
export function submitOnEnter(event) {
  if (event.key === "Enter") {
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }
}

/** A new, permanent id: normalized while typing and checked with the server. Calls
 * `onValid(ok)` whenever the answer changes. */
export function IdField({ label = "מזהה (באותיות לטיניות)", value, onChange, onValid }) {
  const check = useIdCheck(value);
  const ok = Boolean(check?.ok) && check.id === value;
  useEffect(() => onValid(ok), [ok]);
  return (
    <Field label={label}>
      <input
        dir="ltr"
        className="field w-full font-mono"
        value={value}
        placeholder="surrogate-gifts"
        spellCheck={false}
        onChange={(event) => onChange(normalizeId(event.target.value))}
      />
      <span className="block text-xs">
        {!value ? null : check === null ? (
          <span className="text-slate-400">בודק…</span>
        ) : ok ? (
          <span className="text-emerald-700">המזהה פנוי.</span>
        ) : (
          <span className="text-rose-700">{check.problem}</span>
        )}
      </span>
      <span className="block rounded bg-amber-50 p-2 text-xs text-amber-900">{PERMANENT_ID_NOTE}</span>
    </Field>
  );
}

/**
 * A text of the selected item shown as is, with an edit button that turns it into a field.
 * `onSave(text)` may be async; on failure the field stays open (the caller shows the error).
 */
export function EditableText({ label, value, onSave, multiline = false, rows = 3, disabled, placeholder, className = "" }) {
  const [draft, setDraft] = useState(null);
  const editing = draft !== null;
  useEffect(() => setDraft(null), [value]);
  const save = async (event) => {
    event.preventDefault();
    if (draft.trim() === value) return setDraft(null);
    try {
      await onSave(draft.trim());
      setDraft(null);
    } catch {
      // the caller reported the error; keep the draft
    }
  };
  return (
    <section className={className}>
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="section-title mb-0">{label}</h3>
        {!editing && (
          <button type="button" className="btn btn-sm" disabled={disabled} onClick={() => setDraft(value)}>
            עריכה
          </button>
        )}
      </div>
      {editing ? (
        <form className="space-y-2" onSubmit={save}>
          {multiline ? (
            <TextArea value={draft} onChange={setDraft} rows={rows} autoFocus />
          ) : (
            <TextInput value={draft} onChange={setDraft} autoFocus />
          )}
          <div className="flex gap-2">
            <button type="submit" className="btn btn-sm btn-primary" disabled={disabled || !draft.trim()}>
              שמירה
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setDraft(null)}>
              ביטול
            </button>
          </div>
        </form>
      ) : value ? (
        <Bidi as="p" className="whitespace-pre-line leading-relaxed">
          {value}
        </Bidi>
      ) : (
        <p className="text-slate-400">{placeholder ?? "(ריק)"}</p>
      )}
    </section>
  );
}
