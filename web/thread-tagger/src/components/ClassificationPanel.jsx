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
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={isKnowledge}
            onChange={(e) => setIsKnowledge(e.target.checked)}
            className="w-4 h-4"
          />
          <span className="font-medium">ידע מועיל</span>
        </label>

        <div>
          <div className="text-sm font-medium mb-2">תגיות נושא</div>
          <div className="space-y-3 max-h-48 overflow-y-auto border rounded p-2 bg-white">
            {Object.entries(byCategory).map(([cat, catPages]) => (
              <div key={cat}>
                <div className="text-xs text-slate-500 mb-1">{cat}</div>
                <div className="flex flex-wrap gap-1">
                  {catPages.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => toggleTag(p.id)}
                      className={`text-xs px-2 py-1 rounded border ${
                        topicTags.includes(p.id)
                          ? "bg-blue-100 border-blue-400"
                          : "bg-slate-50 border-slate-200"
                      }`}
                    >
                      {p.title_he}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <div className="flex gap-1 mt-2">
            <input
              type="text"
              value={customTag}
              onChange={(e) => setCustomTag(e.target.value)}
              placeholder="תגית חדשה"
              className="flex-1 text-sm border rounded px-2 py-1"
              onKeyDown={(e) => e.key === "Enter" && addCustomTag()}
            />
            <button
              type="button"
              onClick={addCustomTag}
              className="text-sm px-2 py-1 bg-slate-200 rounded"
            >
              הוסף
            </button>
          </div>
          {topicTags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {topicTags.map((t) => (
                <span
                  key={t}
                  className="text-xs bg-blue-50 px-2 py-0.5 rounded"
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
          )}
        </div>

        <div>
          <label className="text-sm font-medium">ישויות</label>
          <input
            type="text"
            value={entities}
            onChange={(e) => setEntities(e.target.value)}
            className="w-full mt-1 text-sm border rounded px-2 py-1"
            placeholder="מופרד בפסיקים"
          />
        </div>

        <div>
          <label className="text-sm font-medium">סיבה</label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            className="w-full mt-1 text-sm border rounded px-2 py-1"
          />
        </div>
      </div>

      <div className="border-t p-3 space-y-2 bg-slate-50">
        <div className="flex gap-2">
          <button
            type="button"
            disabled={saving}
            onClick={() => onSave(payload())}
            className="flex-1 py-2 bg-blue-600 text-white rounded text-sm disabled:opacity-50"
          >
            שמור
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={() => onSaveAndNext(payload())}
            className="flex-1 py-2 bg-emerald-600 text-white rounded text-sm disabled:opacity-50"
          >
            שמור והבא
          </button>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onMerge}
            className="flex-1 py-1.5 text-sm border rounded bg-white hover:bg-slate-100"
          >
            מזג
          </button>
          <button
            type="button"
            onClick={onSplit}
            className="flex-1 py-1.5 text-sm border rounded bg-white hover:bg-slate-100"
          >
            פצל
          </button>
          <button
            type="button"
            onClick={onMove}
            className="flex-1 py-1.5 text-sm border rounded bg-white hover:bg-slate-100"
          >
            העבר
          </button>
        </div>
      </div>
    </div>
  );
}
