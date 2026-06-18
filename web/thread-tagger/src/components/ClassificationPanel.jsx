import { useEffect, useState } from "react";

export default function ClassificationPanel({
  classification,
  taxonomy,
  onSave,
  onSaveAndNext,
  onMerge,
  onSplit,
  onMove,
  saving,
}) {
  const [isKnowledge, setIsKnowledge] = useState(false);
  const [topicTags, setTopicTags] = useState([]);
  const [entities, setEntities] = useState("");
  const [reason, setReason] = useState("");
  const [customTag, setCustomTag] = useState("");

  useEffect(() => {
    if (!classification) return;
    setIsKnowledge(!!classification.is_knowledge_bearing);
    setTopicTags(classification.topic_tags || []);
    setEntities((classification.entities || []).join(", "));
    setReason(classification.reason || "");
  }, [classification]);

  if (!classification) {
    return (
      <div className="p-4 text-slate-400 text-sm">אין סיווג</div>
    );
  }

  const pages = taxonomy?.pages || [];
  const byCategory = pages.reduce((acc, p) => {
    const cat = taxonomy.categories?.[p.category] || p.category;
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(p);
    return acc;
  }, {});

  const toggleTag = (id) => {
    setTopicTags((prev) =>
      prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]
    );
  };

  const addCustomTag = () => {
    const tag = customTag.trim();
    if (tag && !topicTags.includes(tag)) {
      setTopicTags([...topicTags, tag]);
    }
    setCustomTag("");
  };

  const payload = () => ({
    is_knowledge_bearing: isKnowledge,
    topic_tags: topicTags,
    entities: entities
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
    reason,
  });

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      <div className="flex-1 min-h-0 flex flex-col overflow-hidden p-3 pb-2">
        <div className="flex items-center justify-between gap-3 mb-2 shrink-0">
          <div className="text-sm font-medium">תגיות נושא</div>
          <label className="flex items-center gap-2 cursor-pointer text-sm shrink-0">
            <input
              type="checkbox"
              checked={isKnowledge}
              onChange={(e) => setIsKnowledge(e.target.checked)}
              className="w-4 h-4"
            />
            <span>ידע מועיל</span>
          </label>
        </div>

        <div className="flex-1 min-h-0 flex flex-col border rounded bg-white overflow-hidden">
          {topicTags.length > 0 && (
            <div className="shrink-0 max-h-20 overflow-y-auto border-b px-2 py-1.5 bg-blue-50/50">
              <div className="flex flex-wrap gap-1">
                {topicTags.map((t) => (
                  <span
                    key={t}
                    className="text-xs bg-blue-50 px-2 py-0.5 rounded border border-blue-100"
                  >
                    {t}
                    <button
                      type="button"
                      className="mr-1 text-red-500"
                      onClick={() => toggleTag(t)}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-4">
            {Object.entries(byCategory).map(([cat, catPages]) => (
              <div key={cat}>
                <div className="text-xs font-medium text-slate-500 mb-2 sticky top-0 bg-white py-1 z-[1]">
                  {cat}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {catPages.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => toggleTag(p.id)}
                      className={`text-sm px-2.5 py-1.5 rounded border ${
                        topicTags.includes(p.id)
                          ? "bg-blue-100 border-blue-400 text-blue-900"
                          : "bg-slate-50 border-slate-200 hover:bg-slate-100"
                      }`}
                    >
                      {p.title_he}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="shrink-0 flex gap-1 border-t px-2 py-2 bg-slate-50">
            <input
              type="text"
              value={customTag}
              onChange={(e) => setCustomTag(e.target.value)}
              placeholder="תגית חדשה"
              className="flex-1 text-sm border rounded px-2 py-1.5 bg-white"
              onKeyDown={(e) => e.key === "Enter" && addCustomTag()}
            />
            <button
              type="button"
              onClick={addCustomTag}
              className="text-sm px-3 py-1.5 bg-slate-200 rounded hover:bg-slate-300"
            >
              הוסף
            </button>
          </div>
        </div>
      </div>

      <div className="shrink-0 border-t bg-slate-50">
        <details className="group border-b">
          <summary className="cursor-pointer list-none px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100">
            <span className="group-open:hidden">ישויות וסיבה ▾</span>
            <span className="hidden group-open:inline">ישויות וסיבה ▴</span>
          </summary>
          <div className="px-3 pb-3 space-y-2">
            <div>
              <label className="text-xs font-medium text-slate-600">ישויות</label>
              <input
                type="text"
                value={entities}
                onChange={(e) => setEntities(e.target.value)}
                className="w-full mt-1 text-sm border rounded px-2 py-1.5 bg-white"
                placeholder="מופרד בפסיקים"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-600">סיבה</label>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                className="w-full mt-1 text-sm border rounded px-2 py-1.5 bg-white"
              />
            </div>
          </div>
        </details>

        <div className="p-2 space-y-1.5">
          <div className="flex gap-1.5">
            <button
              type="button"
              disabled={saving}
              onClick={() => onSave(payload())}
              className="flex-1 py-1.5 bg-blue-600 text-white rounded text-sm disabled:opacity-50"
            >
              שמור
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => onSaveAndNext(payload())}
              className="flex-1 py-1.5 bg-emerald-600 text-white rounded text-sm disabled:opacity-50"
            >
              שמור והבא
            </button>
          </div>
          <div className="flex gap-1.5">
            <button
              type="button"
              onClick={onMerge}
              className="flex-1 py-1 text-xs border rounded bg-white hover:bg-slate-100"
            >
              מזג
            </button>
            <button
              type="button"
              onClick={onSplit}
              className="flex-1 py-1 text-xs border rounded bg-white hover:bg-slate-100"
            >
              פצל
            </button>
            <button
              type="button"
              onClick={onMove}
              className="flex-1 py-1 text-xs border rounded bg-white hover:bg-slate-100"
            >
              העבר
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
