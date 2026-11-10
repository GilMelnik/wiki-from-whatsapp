import { useState } from "react";
import { Bidi } from "@shell";
import { Field, IdField, submitOnEnter, TextArea, TextInput } from "./fields.jsx";
import { OperationDialog } from "./OperationDialog.jsx";
import { moveTargets, nodeById, textToAliases, titleOf } from "./plan.js";

// Dialogs that create something: a page, a category, a label, or a page split off another.
// The owner types the new id (checked while typing) and the texts; the server's dry run shows
// whether the result is a valid plan.

const SCOPE_HINT =
  "מה שייך לדף ומה לא. זה מה שהמסווג קורא כדי לשבץ כל טענה, אז כתבו גבולות ברורים: „כולל…”, „לא כולל… (זה בדף …)”.";

function usePageForm(initial = {}) {
  const [form, setForm] = useState({ id: "", title: "", description: "", scope: "", he: "", en: "", ...initial });
  const [validId, setValidId] = useState(false);
  const set = (name) => (value) => setForm((previous) => ({ ...previous, [name]: value }));
  const complete = validId && form.title.trim() && form.description.trim() && form.scope.trim();
  const fields = {
    title_he: form.title.trim(),
    description: form.description.trim(),
    scope: form.scope.trim(),
    aliases: { he: textToAliases(form.he), en: textToAliases(form.en) },
  };
  return { form, set, setValidId, complete: Boolean(complete), fields };
}

function PageFields({ page, scopeRows = 6 }) {
  const { form, set, setValidId } = page;
  return (
    <>
      <IdField value={form.id} onChange={set("id")} onValid={setValidId} />
      <Field label="כותרת">
        <TextInput value={form.title} onChange={set("title")} />
      </Field>
      <Field label="תיאור קצר (שורה אחת, מוצג בניווט)">
        <TextInput value={form.description} onChange={set("description")} />
      </Field>
      <Field label="תחום" hint={SCOPE_HINT}>
        <TextArea value={form.scope} onChange={set("scope")} rows={scopeRows} />
      </Field>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="כינויים בעברית (אחד בשורה, לא חובה)">
          <TextArea value={form.he} onChange={set("he")} rows={2} />
        </Field>
        <Field label="כינויים באנגלית (אחד בשורה, לא חובה)">
          <TextArea value={form.en} onChange={set("en")} rows={2} />
        </Field>
      </div>
    </>
  );
}

function ParentSelect({ overview, value, onChange, excludeId, allowTop = false }) {
  const targets = moveTargets(overview, excludeId);
  return (
    <Field label="בתוך הקטגוריה">
      <select className="field w-full" value={value ?? ""} onChange={(event) => onChange(event.target.value || null)} onKeyDown={submitOnEnter}>
        {allowTop && <option value="">הרמה העליונה</option>}
        {targets.map((target) => (
          <option key={target.id} value={target.id}>
            {"  ".repeat(target.depth - 1)}
            {target.title_he}
          </option>
        ))}
      </select>
    </Field>
  );
}

export function AddPageDialog({ overview, parentId, onClose, save }) {
  const page = usePageForm();
  const [parent, setParent] = useState(parentId);
  const op = { op: "add_page", page_id: page.form.id, parent_id: parent, ...page.fields, position: null };
  return (
    <OperationDialog title="דף חדש" op={op} ready={page.complete && Boolean(parent)} overview={overview} confirmLabel="הוספת הדף" onClose={onClose} onConfirm={save}>
      <ParentSelect overview={overview} value={parent} onChange={setParent} />
      <PageFields page={page} />
    </OperationDialog>
  );
}

export function SplitDialog({ overview, itemId, onClose, save }) {
  const original = overview.pages[itemId];
  const page = usePageForm();
  const [originalScope, setOriginalScope] = useState(original.scope);
  const [parent, setParent] = useState(nodeById(overview, itemId).parent_id);
  const samePlace = parent === nodeById(overview, itemId).parent_id;
  const op = {
    op: "split",
    page_id: itemId,
    new_page_id: page.form.id,
    ...page.fields,
    parent_id: samePlace ? null : parent,
    original_scope: originalScope.trim() === original.scope ? null : originalScope.trim(),
  };
  return (
    <OperationDialog
      title={<>פיצול דף חדש מתוך „<Bidi>{original.title_he}</Bidi>”</>}
      op={op}
      ready={page.complete && Boolean(originalScope.trim())}
      overview={overview}
      confirmLabel="פיצול"
      onClose={onClose}
      onConfirm={save}
    >
      <p className="text-slate-600">
        הדף „<Bidi>{original.title_he}</Bidi>” נשאר עם המזהה שלו. הטענות שלו יסווגו מחדש בין שני
        הדפים לפי התחומים, לכן כדאי לצמצם כאן גם את התחום שלו.
      </p>
      <PageFields page={page} scopeRows={5} />
      <Field label={`התחום של „${original.title_he}” אחרי הפיצול`}>
        <TextArea value={originalScope} onChange={setOriginalScope} rows={5} />
      </Field>
      <ParentSelect overview={overview} value={parent} onChange={setParent} excludeId={itemId} />
    </OperationDialog>
  );
}

export function AddCategoryDialog({ overview, parentId = null, onClose, save }) {
  const [id, setId] = useState("");
  const [validId, setValidId] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [parent, setParent] = useState(parentId);
  const op = { op: "add_category", node_id: id, title_he: title.trim(), description: description.trim(), parent_id: parent, position: null };
  return (
    <OperationDialog
      title={parent ? <>קטגוריה חדשה בתוך „<Bidi>{titleOf(overview, parent)}</Bidi>”</> : "קטגוריה חדשה ברמה העליונה"}
      op={op}
      ready={validId && Boolean(title.trim())}
      overview={overview}
      confirmLabel="הוספת הקטגוריה"
      onClose={onClose}
      onConfirm={save}
    >
      <ParentSelect overview={overview} value={parent} onChange={setParent} allowTop />
      <IdField value={id} onChange={setId} onValid={setValidId} />
      <Field label="כותרת">
        <TextInput value={title} onChange={setTitle} />
      </Field>
      <Field label="תיאור (לא חובה)">
        <TextInput value={description} onChange={setDescription} />
      </Field>
    </OperationDialog>
  );
}

export function AddLabelDialog({ overview, kind = "", onClose, save }) {
  const [id, setId] = useState("");
  const [validId, setValidId] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [labelKind, setLabelKind] = useState(kind);
  const op = { op: "add_label", label_id: id, title_he: title.trim(), description: description.trim(), kind: labelKind.trim() };
  return (
    <OperationDialog
      title="תווית חדשה"
      op={op}
      ready={validId && Boolean(title.trim() && description.trim() && labelKind.trim())}
      overview={overview}
      confirmLabel="הוספת התווית"
      onClose={onClose}
      onConfirm={save}
    >
      <IdField value={id} onChange={setId} onValid={setValidId} />
      <Field label="כותרת">
        <TextInput value={title} onChange={setTitle} />
      </Field>
      <Field label="תיאור: מתי טענה מקבלת את התווית" hint="המסווג קורא את התיאור כדי להחליט אם להדביק את התווית לטענה.">
        <TextArea value={description} onChange={setDescription} rows={3} />
      </Field>
      <KindField overview={overview} value={labelKind} onChange={setLabelKind} />
    </OperationDialog>
  );
}

export function KindField({ overview, value, onChange }) {
  const listId = "plan-label-kinds";
  return (
    <Field label="סוג (קבוצת התוויות, באותיות לטיניות, למשל country)">
      <input
        dir="ltr"
        list={listId}
        className="field w-full font-mono"
        value={value}
        onChange={(event) => onChange(event.target.value.toLowerCase())}
      />
      <datalist id={listId}>
        {(overview.label_kinds ?? []).map(({ kind }) => (
          <option key={kind} value={kind} />
        ))}
      </datalist>
    </Field>
  );
}
