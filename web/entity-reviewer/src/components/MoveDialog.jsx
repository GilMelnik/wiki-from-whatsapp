import { useEffect, useState } from "react";
import { fetchEntities } from "../api";

export default function MoveDialog({
  open,
  title,
  subtitle,
  allowNew,
  currentEntityId,
  onClose,
  onConfirm,
  saving,
}) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState([]);
  const [targetId, setTargetId] = useState(undefined);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setTargetId(undefined);
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    let active = true;
    const t = setTimeout(() => {
      fetchEntities({ q: query, limit: "40", sort: "count", order: "desc" })
        .then((data) => {
          if (active) setItems(data.items || []);
        })
        .catch(() => {});
    }, 150);
    return () => {
      active = false;
      clearTimeout(t);
    };
  }, [open, query]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-xl max-h-[85vh] flex flex-col">
        <div className="p-4 border-b border-slate-200">
          <h3 className="font-semibold">{title}</h3>
          {subtitle && <p className="text-xs text-slate-500 mt-1">{subtitle}</p>}
        </div>
        <div className="p-4 border-b border-slate-100">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="חיפוש ישות יעד..."
            className="w-full border border-slate-300 rounded px-3 py-2 text-sm"
            autoFocus
          />
        </div>
        <div className="flex-1 overflow-y-auto">
          {allowNew && (
            <button
              type="button"
              onClick={() => setTargetId(null)}
              className={`w-full text-right px-4 py-3 border-b border-slate-100 hover:bg-teal-50 ${
                targetId === null ? "bg-teal-100" : ""
              }`}
            >
              <div className="text-sm font-medium">+ ישות חדשה</div>
              <div className="text-xs text-slate-500">צור ישות נפרדת חדשה</div>
            </button>
          )}
          {items
            .filter((e) => e.entity_id !== currentEntityId)
            .map((entity) => (
              <button
                key={entity.entity_id}
                type="button"
                onClick={() => setTargetId(entity.entity_id)}
                className={`w-full text-right px-4 py-3 border-b border-slate-100 hover:bg-teal-50 ${
                  targetId === entity.entity_id ? "bg-teal-100" : ""
                }`}
              >
                <div className="text-sm font-medium">{entity.canonical_name}</div>
                <div className="text-xs text-slate-500 mt-0.5">
                  {entity.member_count} שמות · {entity.total_count} אזכורים
                  {entity.aliases?.length > 1 && ` · ${entity.aliases.join(" · ")}`}
                </div>
              </button>
            ))}
          {items.length === 0 && (
            <p className="p-4 text-sm text-slate-500">לא נמצאו ישויות.</p>
          )}
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
            disabled={targetId === undefined || saving}
            onClick={() => onConfirm(targetId)}
            className="px-3 py-2 text-sm rounded bg-teal-600 text-white disabled:opacity-40"
          >
            {saving ? "מבצע..." : "אישור"}
          </button>
        </div>
      </div>
    </div>
  );
}
