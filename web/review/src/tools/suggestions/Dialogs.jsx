import { useState } from "react";
import { AddLabelDialog, AddPageDialog, Bidi, Dialog, kindOf, MergeDialog, SplitDialog, TextArea, usePlanOverview } from "@shell";
import { applyQuestion, proposalOf, proposalFromOperation } from "./suggestions.js";

// The tool's dialogs: correcting a proposal before deciding on it, and the confirmation of an
// apply with paid model calls. A structural proposal is corrected in the plan tool's own
// dialog for that operation (shared through the shell: fields, live id check, dry run of what
// it would do), prefilled with the proposal; a text correction in a plain field. Saving keeps
// the correction on the suggestion (the store checks it against the current wiki and its
// refusal stays in the dialog); nothing changes in the wiki until the suggestion is applied.

const EDIT_NOTE = "השמירה מתקנת רק את ההצעה. התוכנית משתנה רק כשההצעה מתקבלת ומבוצעת.";

function Intro({ suggestion }) {
  return (
    <div className="rounded border border-sky-200 bg-sky-50 p-3 text-sky-900">
      <p>
        <Bidi className="font-medium">{suggestion.title}</Bidi>. <Bidi>{suggestion.reason}</Bidi>
      </p>
      <p className="mt-1 text-xs">המזהים החדשים הם ניחוש של הסוקר; תקנו כל שדה לפני השמירה.</p>
    </div>
  );
}

function Unavailable({ id, onClose }) {
  return (
    <Dialog open title="אי אפשר לערוך את ההצעה" onClose={onClose}>
      <p>
        הפריט <bdi dir="ltr">{id}</bdi> כבר אינו בתוכנית, ולכן ההצעה לא ניתנת לביצוע כפי שהיא. אפשר לדחות אותה.
      </p>
    </Dialog>
  );
}

function StructuralEdit({ suggestion, onClose, save }) {
  const plan = usePlanOverview();
  const overview = plan.data;
  if (!overview?.nodes) return null;
  const p = proposalOf(suggestion);
  const common = {
    overview,
    onClose,
    save: (op) => save(proposalFromOperation(op)),
    title: <>תיקון ההצעה: <Bidi>„{suggestion.title}”</Bidi></>,
    confirmLabel: "שמירת ההצעה המתוקנת",
    intro: <Intro suggestion={suggestion} />,
    note: EDIT_NOTE,
  };
  const pageInitial = { id: p.page_id, title: p.title_he, description: p.description, scope: p.scope };
  if (suggestion.kind === "split") {
    if (kindOf(overview, p.page_id) !== "page") return <Unavailable id={p.page_id} onClose={onClose} />;
    const initial = { ...pageInitial, id: p.new_page_id, originalScope: p.original_scope };
    return <SplitDialog {...common} itemId={p.page_id} initial={initial} />;
  }
  if (suggestion.kind === "merge") {
    if (!kindOf(overview, p.survivor_id)) return <Unavailable id={p.survivor_id} onClose={onClose} />;
    return <MergeDialog {...common} itemId={p.survivor_id} survivorId={p.survivor_id} merged={p.merged_ids} />;
  }
  if (suggestion.kind === "add_page") return <AddPageDialog {...common} parentId={p.parent_id} initial={pageInitial} />;
  return <AddLabelDialog {...common} kind={p.kind} initial={{ id: p.label_id, title: p.title_he, description: p.description }} />;
}

function CorrectionEdit({ suggestion, onClose, save }) {
  const proposal = proposalOf(suggestion);
  const [draft, setDraft] = useState(proposal.correction);
  return (
    <Dialog
      open
      size="lg"
      title={<>תיקון ההצעה: <Bidi>„{suggestion.title}”</Bidi></>}
      confirmLabel="שמירת ההצעה המתוקנת"
      confirmDisabled={!draft.trim()}
      onClose={onClose}
      onConfirm={() => save({ ...proposal, correction: draft.trim() })}
    >
      <div className="space-y-3">
        <p className="text-slate-600">
          {suggestion.kind === "mistake"
            ? "ההוראה לכותב הדפים: מה לתקן במשפט. כותב הדפים מתקן רק לפי הקבוצות של הדף, ומשאיר את שאר הדף מילה במילה."
            : "ההוראה לכותב הדפים: איך להציג את שני הצדדים בכל אחד מהדפים."}
        </p>
        <TextArea value={draft} onChange={setDraft} rows={5} />
        <p className="text-xs text-slate-500">{EDIT_NOTE}</p>
      </div>
    </Dialog>
  );
}

function ApplyConfirm({ cost, onClose, confirm }) {
  return (
    <Dialog open title="ביצוע ההצעות" confirmLabel="ביצוע" onClose={onClose} onConfirm={confirm}>
      <p>{applyQuestion(cost)}</p>
    </Dialog>
  );
}

/** `dialog = {type: "edit", suggestion} | {type: "apply", cost, confirm}`. */
export function DialogHost({ dialog, close, saveEdit }) {
  if (!dialog) return null;
  if (dialog.type === "apply") return <ApplyConfirm cost={dialog.cost} onClose={close} confirm={dialog.confirm} />;
  const { suggestion } = dialog;
  const save = (proposal) => saveEdit(suggestion.id, proposal);
  if (suggestion.kind === "mistake" || suggestion.kind === "contradiction") {
    return <CorrectionEdit suggestion={suggestion} onClose={close} save={save} />;
  }
  return <StructuralEdit suggestion={suggestion} onClose={close} save={save} />;
}
