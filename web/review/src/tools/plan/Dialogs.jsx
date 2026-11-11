import { useState } from "react";
import {
  AddCategoryDialog,
  AddLabelDialog,
  AddPageDialog,
  Bidi,
  formatCount,
  groupCount,
  IdField,
  kindOf,
  Ltr,
  MergeDialog,
  moveTargets,
  nodeById,
  OperationDialog,
  SplitDialog,
  submitOnEnter,
  TextArea,
  titleOf,
} from "@shell";
import { proposalForm } from "./assign.js";
import { KIND_LABELS } from "./plan.js";

// The one dialog that is open (`dialog.kind`) and the operation it sends. `save(op)` saves
// and `accept(proposal, op)` accepts a proposal of the assignment step with that operation;
// both throw on failure (the dialog shows the error and stays open).

function Quote({ overview, id }) {
  return <>“<Bidi>{titleOf(overview, id)}</Bidi>”</>;
}

function MoveDialog({ overview, itemId, onClose, save }) {
  const node = nodeById(overview, itemId);
  const targets = moveTargets(overview, itemId);
  const [parent, setParent] = useState(node.parent_id ?? "");
  const parentId = parent === "" ? null : parent;
  const op = { op: "move", item_id: itemId, parent_id: parentId, position: null };
  return (
    <OperationDialog
      title={<>Move <Quote overview={overview} id={itemId} /></>}
      op={op}
      ready={parentId !== node.parent_id}
      overview={overview}
      confirmLabel="Move"
      size="md"
      onClose={onClose}
      onConfirm={save}
    >
      <label className="block space-y-1">
        <span className="text-xs font-medium text-slate-600">To (at the end of its list; the arrows change the order)</span>
        <select
          className="field w-full"
          value={parent}
          onChange={(event) => setParent(event.target.value)}
          onKeyDown={submitOnEnter}
        >
          {node.kind === "category" && <option value="">Top level</option>}
          {targets.map((target) => (
            <option key={target.id} value={target.id}>
              {"  ".repeat(target.depth - 1)}
              {target.title_he}
              {target.id === node.parent_id ? " (current place)" : ""}
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
      title={<>Change the id of <Quote overview={overview} id={itemId} /></>}
      op={{ op: "change_id", item_id: itemId, new_id: newId }}
      ready={valid}
      overview={overview}
      confirmLabel="Change the id"
      tone="danger"
      onClose={onClose}
      onConfirm={save}
    >
      <p>
        The {kind} gets the new id and stays as it is in everything else. The current id{" "}
        <Ltr>{itemId}</Ltr> is retired for good, and assignments and links to it will redirect to the new one.
      </p>
      <IdField label="New id" value={newId} onChange={setNewId} onValid={setValid} />
    </OperationDialog>
  );
}

function DeleteDialog({ overview, itemId, onClose, save }) {
  return (
    <OperationDialog
      title={<>Delete <Quote overview={overview} id={itemId} /></>}
      op={{ op: "delete", item_id: itemId }}
      overview={overview}
      confirmLabel="Delete"
      tone="danger"
      size="md"
      onClose={onClose}
      onConfirm={save}
    >
      <p>
        The id <Ltr>{itemId}</Ltr> is retired for good. If the item has a replacement, merging it into
        that one is better: that way its claims and links move there.
      </p>
    </OperationDialog>
  );
}

/** A scope edit, old next to new. `editable`: the new scope can still be corrected (when
 * accepting a proposal, which `intro` shows). */
export function ScopeDialog({ overview, itemId, scope, onClose, save, editable = false, title, confirmLabel = "Save the scope", intro = null }) {
  const [draft, setDraft] = useState(scope);
  const newScope = editable ? draft.trim() : scope;
  return (
    <OperationDialog
      title={title ?? <>Change the scope of <Quote overview={overview} id={itemId} /></>}
      op={{ op: "edit", item_id: itemId, changes: { scope: newScope } }}
      ready={Boolean(newScope)}
      overview={overview}
      confirmLabel={confirmLabel}
      onClose={onClose}
      onConfirm={save}
    >
      {intro}
      <div className="grid gap-3 md:grid-cols-2">
        <section>
          <h3 className="section-title">Scope now</h3>
          <Bidi as="p" className="whitespace-pre-line rounded bg-slate-50 p-2 leading-relaxed">
            {overview.pages[itemId].scope}
          </Bidi>
        </section>
        <section>
          <h3 className="section-title">New scope</h3>
          {editable ? (
            <TextArea value={draft} onChange={setDraft} rows={8} />
          ) : (
            <Bidi as="p" className="whitespace-pre-line rounded bg-sky-50 p-2 leading-relaxed">
              {scope}
            </Bidi>
          )}
        </section>
      </div>
    </OperationDialog>
  );
}

function Intro({ proposal }) {
  return (
    <div className="rounded border border-sky-200 bg-sky-50 p-3 text-sky-900">
      <p>
        Proposal: <Bidi className="font-medium">{proposal.title_he}</Bidi>. <Bidi>{proposal.reason}</Bidi>
      </p>
      <p className="mt-1 text-xs">
        Any field can be corrected before accepting. Accepting saves the change in the plan and marks
        the proposal accepted; the assignment step places its {groupCount(proposal.groups.length, formatCount)} at
        its next run.
      </p>
    </div>
  );
}

function AcceptDialog({ proposal, overview, onClose, accept }) {
  const form = proposalForm(proposal, overview);
  const common = {
    overview,
    onClose,
    save: (op) => accept(proposal, op),
    title: <>Accept the proposal: “<Bidi>{proposal.title_he}</Bidi>”</>,
    confirmLabel: "Accept the proposal",
    intro: <Intro proposal={proposal} />,
  };
  const initial = { id: form.id, title: form.title, description: form.description, scope: form.scope };
  if (proposal.kind === "page") return <AddPageDialog {...common} parentId={form.parentId} initial={initial} />;
  if (proposal.kind === "label") return <AddLabelDialog {...common} kind={form.kind} initial={initial} />;
  return <ScopeDialog {...common} itemId={proposal.target.page_id} scope={form.scope} editable />;
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
  accept: AcceptDialog,
};

/** `dialog = {type, itemId, ...props}`: `type` picks the dialog, the rest are its props (a
 * new label's `kind`, a split's page, a proposal). */
export function DialogHost({ dialog, overview, close, save, accept }) {
  if (!dialog || !overview?.nodes) return null;
  const { type, ...props } = dialog;
  // The item went away meanwhile (a re-plan underneath): nothing to act on.
  if (props.itemId && !kindOf(overview, props.itemId)) return null;
  const Component = DIALOGS[type];
  return <Component {...props} overview={overview} onClose={close} save={save} accept={accept} />;
}
