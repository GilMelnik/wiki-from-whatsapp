import { useEffect, useMemo, useState } from "react";

export default function MoveClaimDialog({
  open,
  claim,
  topics,
  onClose,
  onConfirm,
  saving,
}) {
  const [targetTopicId, setTargetTopicId] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (open) {
      setTargetTopicId("");
      setQuery("");
    }
  }, [open, claim]);

  const grouped = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const byCategory = new Map();
    for (const topic of topics) {
      if (topic.id === claim?.topic_id) continue;
      if (needle) {
        const hay = `${topic.title} ${topic.id} ${topic.category_title}`.toLowerCase();
        if (!hay.includes(needle)) continue;
      }
      const key = topic.category_title || topic.category;
      if (!byCategory.has(key)) byCategory.set(key, []);
      byCategory.get(key).push(topic);
    }
    return [...byCategory.entries()].sort((a, b) => a[0].localeCompare(b[0], "he"));
  }, [topics, claim, query]);

  if (!open || !claim) return null;

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-xl max-h-[85vh] flex flex-col">
        <div className="p-4 border-b border-slate-200">
          <h3 className="font-semibold">העברת טענה לנושא אחר</h3>
          <p className="text-xs text-slate-500 mt-1 line-clamp-2">{claim.claim_text}</p>
          <p className="text-xs text-slate-400 mt-1">
            מ: <code>{claim.topic_id}</code>
          </p>
        </div>
        <div className="p-4 border-b border-slate-100">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="חיפוש נושא יעד..."
            className="w-full border border-slate-300 rounded px-3 py-2 text-sm"
            autoFocus
          />
        </div>
        <div className="flex-1 overflow-y-auto">
          {grouped.length === 0 && (
            <p className="p-4 text-sm text-slate-500">לא נמצאו נושאים.</p>
          )}
          {grouped.map(([categoryTitle, items]) => (
            <div key={categoryTitle}>
              <div className="px-4 py-2 text-xs font-semibold text-slate-500 bg-slate-50 sticky top-0">
                {categoryTitle}
              </div>
              {items.map((topic) => (
                <button
                  key={topic.id}
                  type="button"
                  onClick={() => setTargetTopicId(topic.id)}
                  className={`w-full text-right px-4 py-3 border-b border-slate-100 hover:bg-teal-50 ${
                    targetTopicId === topic.id ? "bg-teal-100" : ""
                  }`}
                >
                  <div className="font-medium text-sm">{topic.title}</div>
                  <div className="text-xs text-slate-500">
                    {topic.id} · {topic.merged_claim_count} טענות
                  </div>
                </button>
              ))}
            </div>
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
            disabled={!targetTopicId || saving}
            onClick={() => onConfirm(targetTopicId)}
            className="px-3 py-2 text-sm rounded bg-teal-600 text-white disabled:opacity-40"
          >
            {saving ? "מעביר..." : "העברה"}
          </button>
        </div>
      </div>
    </div>
  );
}
