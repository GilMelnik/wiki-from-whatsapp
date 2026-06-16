import { useState } from "react";

function sortedIndices(set) {
  return [...set].sort((a, b) => a - b);
}

export default function SplitDialog({
  open,
  onClose,
  thread,
  selectedIndices,
  onConfirm,
}) {
  const [mode, setMode] = useState("sparse");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  if (!open || !thread) return null;

  const count = thread.messages?.length || 0;
  const indices = sortedIndices(selectedIndices);
  const rangeStart = indices.length ? indices[0] : null;
  const rangeEnd = indices.length ? indices[indices.length - 1] : null;
  const rangeCount =
    rangeStart != null && rangeEnd != null ? rangeEnd - rangeStart + 1 : 0;

  const handleSubmit = async () => {
    if (selectedIndices.size === 0) {
      setError("יש לבחור לפחות הודעה אחת");
      return;
    }
    if (selectedIndices.size === count) {
      setError("יש להשאיר לפחות הודעה אחת בשיחה המקורית");
      return;
    }
    setLoading(true);
    setError("");
    try {
      await onConfirm(thread.thread_id, mode, indices);
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
        <h2 className="text-lg font-semibold mb-3">פיצול שיחה</h2>
        <p className="text-sm text-slate-600 mb-3">
          {thread.thread_id} · {count} הודעות · {selectedIndices.size} נבחרו
        </p>

        <div className="space-y-3 mb-4">
          <label className="flex items-start gap-2 text-sm cursor-pointer">
            <input
              type="radio"
              className="mt-1"
              checked={mode === "sparse"}
              onChange={() => setMode("sparse")}
            />
            <span>
              <span className="font-medium">הודעות נבחרות בלבד</span>
              <span className="block text-slate-500 text-xs mt-0.5">
                שיחה חדשה עם {selectedIndices.size} הודעות (גם אם לא רציפות)
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm cursor-pointer">
            <input
              type="radio"
              className="mt-1"
              checked={mode === "range"}
              onChange={() => setMode("range")}
            />
            <span>
              <span className="font-medium">טווח מ-[m{rangeStart}] עד [m{rangeEnd}]</span>
              <span className="block text-slate-500 text-xs mt-0.5">
                כולל את כל {rangeCount} ההודעות בטווח (m{rangeStart}…m{rangeEnd})
              </span>
            </span>
          </label>
        </div>

        {error && <p className="text-red-600 text-sm mb-2">{error}</p>}

        <div className="flex gap-2 justify-end">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm border rounded">
            ביטול
          </button>
          <button
            type="button"
            disabled={loading || selectedIndices.size === 0}
            onClick={handleSubmit}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded disabled:opacity-50"
          >
            פצל
          </button>
        </div>
      </div>
    </div>
  );
}
