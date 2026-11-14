import { Kbd } from "@shell";
import { automaticText, knowledgeText } from "./threads.js";

// Step 2's answer for the opened conversation: does it carry knowledge worth extracting (only
// those go to step 3). The owner sets it by hand or goes back to step 2's answer; only the
// buttons that change something are shown. Before step 2 ran, or for a conversation newer
// than its last run, one line says why there is nothing to set.

const NOT_CLASSIFIED = "Step 2 has not classified this conversation yet (it is newer than step 2's last run).";

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
  if (label === null) return <p className="text-xs text-slate-500">{NOT_CLASSIFIED}</p>;
  return (
    <div className="space-y-2">
      <p className="flex flex-wrap items-baseline gap-x-2">
        <span className={`font-medium ${label.value ? "text-slate-800" : "text-amber-800"}`}>{knowledgeText(label)}</span>
        {label.manual && <span className="badge badge-sky">Set by hand</span>}
      </p>
      <p className="text-xs text-slate-500">{automaticText(label)}</p>
      {!label.value && <LabelButton label="It carries knowledge" keyName="y" disabled={disabled} onClick={() => onSet(true)} />}
      {label.value && <LabelButton label="It carries no knowledge" keyName="x" disabled={disabled} onClick={() => onSet(false)} />}
      {label.manual && <LabelButton label="Back to step 2's answer" keyName="o" disabled={disabled} onClick={() => onSet(null)} />}
    </div>
  );
}
