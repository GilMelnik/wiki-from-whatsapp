import { Dialog } from "@shell";
import { usePreview } from "./api.js";
import { consequenceLines } from "./plan.js";

// The dialog of every structural change. While the owner fills in the form, the server runs
// the operation as a dry run and the dialog shows what it would mean downstream (what
// disappears, what is redirected, whose claims are classified again) or, verbatim, which
// plan rules it would break. Confirm is possible only after a successful dry run; it saves
// through `onConfirm(op)` (throws → the error stays in the dialog).

const LINE_TONES = {
  info: "border-sky-200 bg-sky-50 text-sky-900",
  warning: "border-amber-200 bg-amber-50 text-amber-900",
  danger: "border-rose-200 bg-rose-50 text-rose-900",
};

export function Consequences({ preview, error, pending, overview }) {
  if (error) {
    return (
      <p role="status" dir="auto" className="whitespace-pre-line rounded border border-rose-200 bg-rose-50 p-2 text-rose-800">
        {error.message}
      </p>
    );
  }
  if (!preview) {
    return <p className="text-xs text-slate-400">{pending ? "בודק מה השינוי יעשה…" : "מלאו את הפרטים כדי לראות מה השינוי יעשה."}</p>;
  }
  return (
    <ul className={`space-y-1 ${pending ? "opacity-60" : ""}`}>
      {consequenceLines(preview.edit, overview).map((line) => (
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
          <h3 className="section-title">מה השינוי יעשה</h3>
          <Consequences preview={preview} error={error} pending={pending} overview={overview} />
          <p className="mt-2 text-xs text-slate-500">
            אחרי השמירה השלבים שאחרי התוכנית (שיוך הטענות וכתיבת הדפים) יסומנו כלא מעודכנים. אפשר
            לבטל את השינוי האחרון בכפתור „ביטול” (u).
          </p>
        </section>
      </div>
    </Dialog>
  );
}
