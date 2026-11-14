import { Kbd } from "@shell";
import { automaticText, labelActions, labelStateText, NEXT_RUN_TEXT, needsLabel } from "./threads.js";

// The knowledge label of the opened conversation: does it carry knowledge worth extracting
// (only those go to step 3). One line says the state in plain words (the label and who decided
// it, or that it needs one); the owner labels any conversation by hand, or takes their label
// away. A conversation that needs a label and gets none here is classified by step 2's next
// run; nothing here starts it.

function LabelButton({ label, keyName, disabled, onClick }) {
  return (
    <button type="button" className="btn btn-block justify-between" disabled={disabled} onClick={onClick}>
      <span>{label}</span>
      <Kbd>{keyName}</Kbd>
    </button>
  );
}

export function KnowledgeControl({ knowledge, label, disabled, onSet }) {
  if (!knowledge) return null;
  if (!knowledge.ready) return <p className="text-xs text-slate-500">{knowledge.reason}</p>;
  if (label === undefined) return null;
  const needs = needsLabel(label);
  const automatic = automaticText(label);
  return (
    <div className="space-y-2">
      <p className="flex flex-wrap items-baseline gap-x-2">
        <span className={`font-medium ${needs || !label.value ? "text-amber-800" : "text-slate-800"}`}>{labelStateText(label)}</span>
        {label?.manual && label.current && <span className="badge badge-sky">Set by hand</span>}
      </p>
      {needs && <p className="text-xs text-slate-500">{NEXT_RUN_TEXT}</p>}
      {automatic && <p className="text-xs text-slate-500">{automatic}</p>}
      {labelActions(label).map((action) => (
        <LabelButton key={action.key} label={action.text} keyName={action.key} disabled={disabled} onClick={() => onSet(action.value)} />
      ))}
    </div>
  );
}
