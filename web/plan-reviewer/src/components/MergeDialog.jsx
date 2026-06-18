import { useEffect, useState } from "react";

export default function MergeDialog({ open, pages, currentPageId, onClose, onConfirm, saving }) {
  const [targetId, setTargetId] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (open) {
      setTargetId("");
      setQuery("");
    }
  }, [open]);

  if (!open) return null;

  const needle = query.trim().toLowerCase();
  const options = pages.filter((p) => {
    if (p.id === currentPageId) return false;
    if (!needle) return true;
    return (
      p.title.toLowerCase().includes(needle) ||
      p.id.toLowerCase().includes(needle)
    );
  });

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[80vh] flex flex-col">
        <div className="p-4 border-b border-slate-200">
          <h3 className="font-semibold">מיזוג עמוד לתוך עמוד אחר</h3>
          <p className="text-xs text-slate-500 mt-1">
            העמוד הנוכחי יימחק. כל נושאי המקור שלו יצורפו לעמוד היעד.
          </p>
        </div>
        <div className="p-4 border-b border-slate-100">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="חיפוש עמוד יעד..."
            className="w-full border border-slate-300 rounded px-3 py-2 text-sm"
            autoFocus
          />
        </div>
        <div className="flex-1 overflow-y-auto">
          {options.map((page) => (
            <button
              key={page.id}
              type="button"
              onClick={() => setTargetId(page.id)}
              className={`w-full text-right px-4 py-3 border-b border-slate-100 hover:bg-teal-50 ${
                targetId === page.id ? "bg-teal-100" : ""
              }`}
            >
              <div className="font-medium text-sm">{page.title}</div>
              <div className="text-xs text-slate-500">{page.id}</div>
            </button>
          ))}
        </div>
        <div className="p-4 border-t border-slate-200 flex gap-2 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-2 text-sm rounded border border-slate-300"
          >
            ביטול
          </button>
          <button
            type="button"
            disabled={!targetId || saving}
            onClick={() => onConfirm(targetId)}
            className="px-3 py-2 text-sm rounded bg-teal-600 text-white disabled:opacity-40"
          >
            {saving ? "ממזג..." : "מיזוג"}
          </button>
        </div>
      </div>
    </div>
  );
}
