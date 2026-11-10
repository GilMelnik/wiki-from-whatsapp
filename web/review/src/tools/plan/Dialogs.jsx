import { useState } from "react";
import { Bidi, Ltr } from "@shell";
import { IdField, submitOnEnter } from "./fields.jsx";
import { AddCategoryDialog, AddLabelDialog, AddPageDialog, SplitDialog } from "./FormDialogs.jsx";
import { OperationDialog } from "./OperationDialog.jsx";
import { KIND_LABELS, kindOf, labelItems, moveTargets, nodeById, pathTitles, titleOf } from "./plan.js";

// The one dialog that is open (`dialog.kind`) and the operation it sends. `save(op)` saves
// and throws on failure (the dialog shows the error and stays open).

function Quote({ overview, id }) {
  return <Bidi>„{titleOf(overview, id)}”</Bidi>;
}

function MergeDialog({ overview, itemId, onClose, save }) {
  const isLabel = kindOf(overview, itemId) === "label";
  const candidates = isLabel
    ? labelItems(overview).filter((label) => label.id !== itemId)
    : Object.keys(overview.pages).filter((id) => id !== itemId).map((id) => ({ id, ...overview.pages[id] }));
  const [others, setOthers] = useState([]);
  const [survivor, setSurvivor] = useState(itemId);
  const all = [itemId, ...others];
  const toggle = (id) => {
    setOthers((list) => (list.includes(id) ? list.filter((other) => other !== id) : [...list, id]));
    if (survivor === id) setSurvivor(itemId);
  };
  const op = { op: "merge", survivor_id: survivor, merged_ids: all.filter((id) => id !== survivor) };
  const what = isLabel ? "תוויות" : "דפים";
  return (
    <OperationDialog
      title={<>מיזוג {what}: <Quote overview={overview} id={itemId} /></>}
      op={op}
      ready={others.length > 0}
      overview={overview}
      confirmLabel="מיזוג"
      tone="danger"
      onClose={onClose}
      onConfirm={save}
    >
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
              <Quote overview={overview} id={id} /> <Ltr className="text-xs text-slate-400">{id}</Ltr>
            </label>
          ))}
        </div>
      </section>
    </OperationDialog>
  );
}

function MoveDialog({ overview, itemId, onClose, save }) {
  const node = nodeById(overview, itemId);
  const targets = moveTargets(overview, itemId);
  const [parent, setParent] = useState(node.parent_id ?? "");
  const parentId = parent === "" ? null : parent;
  const op = { op: "move", item_id: itemId, parent_id: parentId, position: null };
  return (
    <OperationDialog
      title={<>העברת <Quote overview={overview} id={itemId} /></>}
      op={op}
      ready={parentId !== node.parent_id}
      overview={overview}
      confirmLabel="העברה"
      size="md"
      onClose={onClose}
      onConfirm={save}
    >
      <label className="block space-y-1">
        <span className="text-xs font-medium text-slate-600">אל (בסוף הרשימה שלה; החצים משנים את הסדר)</span>
        <select
          className="field w-full"
          value={parent}
          onChange={(event) => setParent(event.target.value)}
          onKeyDown={submitOnEnter}
        >
          {node.kind === "category" && <option value="">הרמה העליונה</option>}
          {targets.map((target) => (
            <option key={target.id} value={target.id}>
              {"  ".repeat(target.depth - 1)}
              {target.title_he}
              {target.id === node.parent_id ? " (המקום הנוכחי)" : ""}
            </option>
          ))}
        </select>
      </label>
    </OperationDialog>
  );
}

function ChangeIdDialog({ overview, itemId, onClose, save }) {
  const [newId, setNewId] = useState("");
  const [valid, setValid] = useState(false);
  const kind = KIND_LABELS[kindOf(overview, itemId)];
  return (
    <OperationDialog
      title={<>החלפת המזהה של <Quote overview={overview} id={itemId} /></>}
      op={{ op: "change_id", item_id: itemId, new_id: newId }}
      ready={valid}
      overview={overview}
      confirmLabel="החלפת המזהה"
      tone="danger"
      onClose={onClose}
      onConfirm={save}
    >
      <p>
        ה{kind} יקבל את המזהה החדש ויישאר כפי שהוא בכל השאר. המזהה הנוכחי <Ltr>{itemId}</Ltr> יוצא
        משימוש לתמיד, ושיוכים וקישורים אליו יופנו לחדש.
      </p>
      <IdField label="המזהה החדש" value={newId} onChange={setNewId} onValid={setValid} />
    </OperationDialog>
  );
}

function DeleteDialog({ overview, itemId, onClose, save }) {
  return (
    <OperationDialog
      title={<>מחיקת <Quote overview={overview} id={itemId} /></>}
      op={{ op: "delete", item_id: itemId }}
      overview={overview}
      confirmLabel="מחיקה"
      tone="danger"
      size="md"
      onClose={onClose}
      onConfirm={save}
    >
      <p>
        המזהה <Ltr>{itemId}</Ltr> יוצא משימוש לתמיד. אם יש לפריט תחליף, עדיף למזג אותו לתוכו: כך
        הטענות והקישורים שלו עוברים אליו.
      </p>
    </OperationDialog>
  );
}

function ScopeDialog({ overview, itemId, scope, onClose, save }) {
  return (
    <OperationDialog
      title={<>שינוי התחום של <Quote overview={overview} id={itemId} /></>}
      op={{ op: "edit", item_id: itemId, changes: { scope } }}
      overview={overview}
      confirmLabel="שמירת התחום"
      onClose={onClose}
      onConfirm={save}
    >
      <div className="grid gap-3 md:grid-cols-2">
        <section>
          <h3 className="section-title">התחום עכשיו</h3>
          <Bidi as="p" className="whitespace-pre-line rounded bg-slate-50 p-2 leading-relaxed">
            {overview.pages[itemId].scope}
          </Bidi>
        </section>
        <section>
          <h3 className="section-title">התחום החדש</h3>
          <Bidi as="p" className="whitespace-pre-line rounded bg-sky-50 p-2 leading-relaxed">
            {scope}
          </Bidi>
        </section>
      </div>
    </OperationDialog>
  );
}

const DIALOGS = {
  merge: MergeDialog,
  move: MoveDialog,
  changeId: ChangeIdDialog,
  delete: DeleteDialog,
  scope: ScopeDialog,
  split: SplitDialog,
  addPage: AddPageDialog,
  addCategory: AddCategoryDialog,
  addLabel: AddLabelDialog,
};

export function DialogHost({ dialog, overview, close, save }) {
  if (!dialog || !overview?.nodes) return null;
  // The item went away meanwhile (a re-plan underneath): nothing to act on.
  if (dialog.itemId && !kindOf(overview, dialog.itemId)) return null;
  const Component = DIALOGS[dialog.kind];
  return <Component {...dialog} overview={overview} onClose={close} save={save} />;
}
