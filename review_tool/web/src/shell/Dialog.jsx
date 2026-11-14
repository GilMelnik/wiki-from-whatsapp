import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Kbd } from "./text.jsx";

// Modal dialog for anything that needs a decision or input before it happens
// (merge, split, move, delete). Quick feedback after the fact is a toast, not a dialog.
//
//   <Dialog open={mergeOpen} title="Merge conversations" confirmLabel="Merge"
//           confirmDisabled={!targetId} onClose={() => setMergeOpen(false)}
//           onConfirm={() => api.post("/threads/merge", { ids: [id, targetId] })}>
//     ...form fields...
//   </Dialog>
//
// `onConfirm` may be async: while it runs the buttons are disabled, an error it throws is
// shown inside the dialog (which stays open), and on success the dialog closes itself.
// Escape and a click outside cancel; Enter in a field confirms. Without `onConfirm` the
// dialog only informs and has a single close button. `tone="danger"` for destructive actions.

const SIZES = { md: "max-w-md", lg: "max-w-2xl" };
const CONFIRM_TONES = { primary: "btn-primary", danger: "btn-danger" };

export function Dialog({
  open,
  title,
  onClose,
  onConfirm,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  closeLabel = "Close",
  confirmDisabled = false,
  tone = "primary",
  size = "md",
  children,
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const panelRef = useRef(null);
  const titleId = useId();
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return undefined;
    setError("");
    const previouslyFocused = document.activeElement;
    const first = panelRef.current.querySelector("input, select, textarea, button");
    first?.focus();
    const onKeyDown = (event) => {
      if (event.key === "Escape" && !busyRef.current) onCloseRef.current();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus?.();
    };
  }, [open]);

  if (!open) return null;

  const confirm = async (event) => {
    event.preventDefault();
    if (busy || confirmDisabled) return;
    setBusy(true);
    setError("");
    try {
      await onConfirm();
      onClose();
    } catch (cause) {
      setError(cause?.message || String(cause));
    } finally {
      setBusy(false);
    }
  };

  const Body = onConfirm ? "form" : "div";
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <Body
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onSubmit={onConfirm ? confirm : undefined}
        className={`mx-4 flex max-h-[90vh] w-full flex-col rounded-lg border border-slate-200 bg-white p-5 shadow-xl ${SIZES[size]}`}
      >
        <h2 id={titleId} className="mb-3 text-lg font-semibold">
          {title}
        </h2>
        <div className="min-h-0 flex-1 overflow-y-auto text-sm">{children}</div>
        {error && (
          <p role="alert" dir="auto" className="mt-3 whitespace-pre-line text-sm text-rose-700">
            {error}
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            {onConfirm ? cancelLabel : closeLabel} <Kbd>Esc</Kbd>
          </button>
          {onConfirm && (
            <button
              type="submit"
              className={`btn ${CONFIRM_TONES[tone]}`}
              disabled={busy || confirmDisabled}
            >
              {confirmLabel}
            </button>
          )}
        </div>
      </Body>
    </div>,
    document.body
  );
}
