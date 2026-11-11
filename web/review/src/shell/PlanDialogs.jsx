import { useState } from "react";
import { Field, IdField, submitOnEnter, TextArea, TextInput } from "./fields.jsx";
import { OperationDialog } from "./OperationDialog.jsx";
import { kindOf, labelItems, moveTargets, nodeById, pathTitles, textToAliases, titleOf } from "./planText.js";
import { Bidi, Ltr } from "./text.jsx";

// The plan operation dialogs every tool shares: a new page, category or label, a page split
// off another, a merge. The owner types the new id (checked while typing) and the texts; the
// plan tool's dry run shows whether the result is a valid plan. The plan tool saves with
// them; a dialog can also start from someone else's proposal (the assignment step's, the
// whole-wiki review's): `initial` (and `merged` / `survivorId` for a merge) prefills the form,
// `intro` shows the proposal above it, `title` / `confirmLabel` / `note` say what confirming
// does, and `save(op)` receives the operation as corrected.

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

export function AddPageDialog({ overview, parentId, onClose, save, initial, title = "דף חדש", confirmLabel = "הוספת הדף", intro = null, note }) {
  const page = usePageForm(initial);
  const [parent, setParent] = useState(parentId);
  const op = { op: "add_page", page_id: page.form.id, parent_id: parent, ...page.fields, position: null };
  return (
    <OperationDialog title={title} op={op} ready={page.complete && Boolean(parent)} overview={overview} confirmLabel={confirmLabel} note={note} onClose={onClose} onConfirm={save}>
      {intro}
      <ParentSelect overview={overview} value={parent} onChange={setParent} />
      <PageFields page={page} />
    </OperationDialog>
  );
}

export function SplitDialog({ overview, itemId, onClose, save, initial, title, confirmLabel = "פיצול", intro = null, note }) {
  const original = overview.pages[itemId];
  const page = usePageForm(initial);
  const [originalScope, setOriginalScope] = useState(initial?.originalScope ?? original.scope);
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
      title={title ?? <>פיצול דף חדש מתוך „<Bidi>{original.title_he}</Bidi>”</>}
      op={op}
      ready={page.complete && Boolean(originalScope.trim())}
      overview={overview}
      confirmLabel={confirmLabel}
      note={note}
      onClose={onClose}
      onConfirm={save}
    >
      {intro}
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

export function AddLabelDialog({ overview, kind = "", onClose, save, initial = {}, title: dialogTitle = "תווית חדשה", confirmLabel = "הוספת התווית", intro = null, note }) {
  const [id, setId] = useState(initial.id ?? "");
  const [validId, setValidId] = useState(false);
  const [title, setTitle] = useState(initial.title ?? "");
  const [description, setDescription] = useState(initial.description ?? "");
  const [labelKind, setLabelKind] = useState(kind);
  const op = { op: "add_label", label_id: id, title_he: title.trim(), description: description.trim(), kind: labelKind.trim() };
  return (
    <OperationDialog
      title={dialogTitle}
      op={op}
      ready={validId && Boolean(title.trim() && description.trim() && labelKind.trim())}
      overview={overview}
      confirmLabel={confirmLabel}
      note={note}
      onClose={onClose}
      onConfirm={save}
    >
      {intro}
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

/** Merge pages or labels into one. `merged` / `survivorId` prefill the choice (a suggested merge). */
export function MergeDialog({ overview, itemId, onClose, save, merged = [], survivorId = itemId, title, confirmLabel = "מיזוג", intro = null, note }) {
  const isLabel = kindOf(overview, itemId) === "label";
  const candidates = isLabel
    ? labelItems(overview).filter((label) => label.id !== itemId)
    : Object.keys(overview.pages).filter((id) => id !== itemId).map((id) => ({ id, ...overview.pages[id] }));
  const [others, setOthers] = useState(merged);
  const [survivor, setSurvivor] = useState(survivorId);
  const all = [itemId, ...others];
  const toggle = (id) => {
    setOthers((list) => (list.includes(id) ? list.filter((other) => other !== id) : [...list, id]));
    if (survivor === id) setSurvivor(itemId);
  };
  const op = { op: "merge", survivor_id: survivor, merged_ids: all.filter((id) => id !== survivor) };
  const what = isLabel ? "תוויות" : "דפים";
  return (
    <OperationDialog
      title={title ?? <>מיזוג {what}: <Bidi>„{titleOf(overview, itemId)}”</Bidi></>}
      op={op}
      ready={others.length > 0}
      overview={overview}
      confirmLabel={confirmLabel}
      tone="danger"
      note={note}
      onClose={onClose}
      onConfirm={save}
    >
      {intro}
      <section>
        <h3 className="section-title">עם מה למזג</h3>
        <ul className="max-h-56 space-y-0.5 overflow-y-auto rounded border border-slate-200 p-2">
          {candidates.map((item) => (
            <li key={item.id}>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={others.includes(item.id)} onChange={() => toggle(item.id)} />
                <Bidi>{item.title_he}</Bidi>
                <Ltr className="text-xs text-slate-400">{item.id}</Ltr>
                <span className="text-xs text-slate-400">
                  {isLabel ? item.labelKind : pathTitles(overview, item.id).join(" › ")}
                </span>
              </label>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h3 className="section-title">מי נשאר</h3>
        <p className="mb-2 text-xs text-slate-500">
          הפריט שנשאר שומר על המזהה, הכותרת, התחום והמקום שלו. השמות של האחרים נוספים לכינויים
          שלו, והמזהים שלהם יופנו אליו.
        </p>
        <div className="space-y-1">
          {all.map((id) => (
            <label key={id} className="flex items-center gap-2">
              <input type="radio" name="survivor" checked={survivor === id} onChange={() => setSurvivor(id)} />
              <Bidi>„{titleOf(overview, id)}”</Bidi> <Ltr className="text-xs text-slate-400">{id}</Ltr>
            </label>
          ))}
        </div>
      </section>
    </OperationDialog>
  );
}
