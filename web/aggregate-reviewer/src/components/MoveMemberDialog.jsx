import { useEffect, useMemo, useState } from "react";

export default function MoveMemberDialog({
  open,
  member,
  groups,
  currentGroupKey,
  onClose,
  onConfirm,
  saving,
}) {
  const [targetKey, setTargetKey] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (open) {
      setTargetKey("");
      setQuery("");
    }
  }, [open, member]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return groups.filter((g) => {
      if (g.key === currentGroupKey) return false;
      if (!needle) return true;
      const hay = `${g.claim_text} ${g.key}`.toLowerCase();
      return hay.includes(needle);
    });
  }, [groups, currentGroupKey, query]);

  if (!open || !member) return null;

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-xl max-h-[85vh] flex flex-col">
        <div className="p-4 border-b border-slate-200">
          <h3 className="font-semibold">העברת טענה לקבוצה אחרת</h3>
          <p className="text-xs text-slate-500 mt-1 line-clamp-2">{member.claim_text}</p>
          <p className="text-xs text-slate-400 mt-1">
            <code>{member.source_claim_id}</code>
          </p>
        </div>
        <div className="p-4 border-b border-slate-100">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="חיפוש קבוצה..."
            className="w-full border border-slate-300 rounded px-3 py-2 text-sm"
            autoFocus
          />
        </div>
        <div className="flex-1 overflow-y-auto">
          {filtered.length === 0 && (
            <p className="p-4 text-sm text-slate-500">לא נמצאו קבוצות.</p>
          )}
          {filtered.map((group) => (
            <button
              key={group.key}
              type="button"
              onClick={() => setTargetKey(group.key)}
              className={`w-full text-right px-4 py-3 border-b border-slate-100 hover:bg-teal-50 ${
                targetKey === group.key ? "bg-teal-100" : ""
              }`}
            >
              <div className="text-sm line-clamp-2">{group.claim_text}</div>
              <div className="text-xs text-slate-500 mt-1">
                {group.endorsement_count} טענות · תמיכה {group.support_count ?? "—"}
              </div>
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
            disabled={!targetKey || saving}
            onClick={() => onConfirm(targetKey)}
            className="px-3 py-2 text-sm rounded bg-teal-600 text-white disabled:opacity-40"
          >
            {saving ? "מעביר..." : "העברה"}
          </button>
        </div>
      </div>
    </div>
  );
}
