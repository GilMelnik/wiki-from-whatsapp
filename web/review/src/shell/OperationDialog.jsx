import { Dialog } from "./Dialog.jsx";
import { formatCount } from "./format.js";
import { usePreview } from "./planApi.js";
import { assignmentLines, consequenceLines } from "./planText.js";

// The dialog of every structural change. While the owner fills in the form, the server runs
// the operation as a dry run and the dialog shows what it would mean downstream (what
// disappears, what is redirected, whose claims are classified again, and once step 6b has
// run how many claim groups the next assignment run redirects, asks again or takes off a
// deleted page) or, verbatim, which plan rules it would break. Confirm is possible only after
// a successful dry run; it calls `onConfirm(op)` (throws → the error stays in the dialog).
// `note` replaces the line under the consequences (what happens after saving) for a dialog
// that does not save the plan itself (e.g. correcting a suggested operation).

const PLAN_SAVE_NOTE =
  "After saving, the steps that follow the plan (assigning the claims and writing the pages) are marked out of date. The last change can be undone with the “Undo” button (u).";

const LINE_TONES = {
  info: "border-sky-200 bg-sky-50 text-sky-900",
  warning: "border-amber-200 bg-amber-50 text-amber-900",
  danger: "border-rose-200 bg-rose-50 text-rose-900",
};

export function Consequences({ preview, error, pending, overview }) {
  if (error) {
    return (
      <p role="status" className="whitespace-pre-line rounded border border-rose-200 bg-rose-50 p-2 text-rose-800">
        {error.message}
      </p>
    );
  }
  if (!preview) {
    return <p className="text-xs text-slate-400">{pending ? "Checking what the change will do…" : "Fill in the details to see what the change will do."}</p>;
  }
  return (
    <ul className={`space-y-1 ${pending ? "opacity-60" : ""}`}>
      {[...consequenceLines(preview.edit, overview), ...assignmentLines(preview.assignments, overview, formatCount)].map((line) => (
        <li key={line.text} className={`rounded border p-2 ${LINE_TONES[line.tone]}`}>
          {line.text}
        </li>
      ))}
    </ul>
  );
}

export function OperationDialog({
  title,
  op,
  overview,
  ready = true,
  confirmLabel,
  tone,
  size = "lg",
  onClose,
  onConfirm,
  note = PLAN_SAVE_NOTE,
  children,
}) {
  const { preview, error, pending } = usePreview(ready ? op : null);
  return (
    <Dialog
      open
      title={title}
      size={size}
      tone={tone}
      confirmLabel={confirmLabel}
      confirmDisabled={!ready || pending || !preview || Boolean(error)}
      onClose={onClose}
      onConfirm={() => onConfirm(op)}
    >
      <div className="space-y-4">
        {children}
        <section>
          <h3 className="section-title">What the change will do</h3>
          <Consequences preview={preview} error={error} pending={pending} overview={overview} />
          <p className="mt-2 text-xs text-slate-500">{note}</p>
        </section>
      </div>
    </Dialog>
  );
}
