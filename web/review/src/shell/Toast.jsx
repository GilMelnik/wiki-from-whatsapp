import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { TOAST_MS } from "./config.js";

// Short feedback after something happened: "נשמר", "מוזג ל-t-12", or an error message.
//
//   const toast = useToast();
//   try { await api.post(...); toast.success("נשמר"); } catch (error) { toast.error(error); }
//
// Toasts disappear by themselves (errors stay longer and can be closed). Anything the user
// must decide or must not miss is a Dialog or a banner instead.

const ToastContext = createContext(null);
const TONES = { info: "bg-slate-800", success: "bg-emerald-700", error: "bg-rose-700" };

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((toast) => toast.id !== id));
  }, []);

  const show = useCallback(
    (tone, message) => {
      const id = nextId.current;
      nextId.current += 1;
      const text = message instanceof Error ? message.message : String(message);
      setToasts((list) => [...list, { id, tone, text }]);
      setTimeout(() => dismiss(id), TOAST_MS[tone]);
    },
    [dismiss]
  );

  const api = useMemo(
    () => ({
      info: (message) => show("info", message),
      success: (message) => show("success", message),
      error: (message) => show("error", message),
    }),
    [show]
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-4 z-[60] flex flex-col items-center gap-2"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role={toast.tone === "error" ? "alert" : "status"}
            className={`pointer-events-auto flex max-w-xl items-center gap-3 rounded-lg px-4 py-2 text-sm text-white shadow-lg ${TONES[toast.tone]}`}
          >
            <span dir="auto">{toast.text}</span>
            {toast.tone === "error" && (
              <button
                type="button"
                aria-label="סגירה"
                className="text-white/80 hover:text-white"
                onClick={() => dismiss(toast.id)}
              >
                ×
              </button>
            )}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** `{ info(message), success(message), error(messageOrError) }` */
export function useToast() {
  const api = useContext(ToastContext);
  if (api === null) throw new Error("useToast must be used inside the review shell");
  return api;
}
