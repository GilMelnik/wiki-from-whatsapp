import { useState } from "react";

export default function MergeDialog({
  open,
  onClose,
  currentId,
  neighbors,
  onConfirm,
}) {
  const [targetId, setTargetId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  if (!open) return null;

  const suggestions = [
    neighbors?.prev_id,
    neighbors?.next_id,
  ].filter(Boolean);

  const handleSubmit = async () => {
    const other = targetId.trim();
    if (!other) {
      setError("יש להזין מזהה שיחה");
      return;
    }
    setLoading(true);
    setError("");
    try {
      await onConfirm([currentId, other], other);
      onClose();
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-5 mx-4">
        <h2 className="text-lg font-semibold mb-3">מיזוג שיחות</h2>
        <p className="text-sm text-slate-600 mb-3">
          מיזוג <strong>{currentId}</strong> עם שיחה נוספת. ההודעות יסודרו לפי זמן.
        </p>

        {suggestions.length > 0 && (
          <div className="mb-3">
            <div className="text-xs text-slate-500 mb-1">שכנות כרונולוגיות</div>
            <div className="flex gap-2">
              {suggestions.map((id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setTargetId(id)}
                  className="text-sm px-2 py-1 border rounded hover:bg-slate-100"
                >
                  {id}
                </button>
              ))}
            </div>
          </div>
        )}

        <input
          type="text"
          value={targetId}
          onChange={(e) => setTargetId(e.target.value)}
          placeholder="מזהה שיחה לשילוב"
          className="w-full border rounded px-3 py-2 text-sm mb-3"
        />

        {error && <p className="text-red-600 text-sm mb-2">{error}</p>}

        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm border rounded"
          >
            ביטול
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={handleSubmit}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded disabled:opacity-50"
          >
            מזג
          </button>
        </div>
      </div>
    </div>
  );
}
