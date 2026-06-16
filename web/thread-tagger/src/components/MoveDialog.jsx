import { useEffect, useState } from "react";
import { kindLabel } from "../recentHistory";

function sortedIndices(set) {
  return [...set].sort((a, b) => a - b);
}

function SuggestionButton({ id, label, sublabel, onPick }) {
  return (
    <button
      type="button"
      onClick={() => onPick(id)}
      className="text-sm px-2 py-1.5 border rounded hover:bg-blue-50 text-right w-full"
    >
      <span className="font-medium">{label || id}</span>
      {sublabel && (
        <span className="text-xs text-slate-500 block">{sublabel}</span>
      )}
    </button>
  );
}

export default function MoveDialog({
  open,
  onClose,
  sourceId,
  selectedIndices,
  onConfirm,
  suggestions,
}) {
  const [targetId, setTargetId] = useState("");
  const [position, setPosition] = useState("append");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setTargetId("");
      setError("");
    }
  }, [open, sourceId]);

  if (!open) return null;

  const pick = (id) => setTargetId(id);

  const handleSubmit = async () => {
    const target = targetId.trim();
    if (!target) {
      setError("יש להזין מזהה שיחת יעד");
      return;
    }
    if (selectedIndices.size === 0) {
      setError("יש לבחור הודעות להעברה");
      return;
    }
    const indices = sortedIndices(selectedIndices);
    const start = indices[0];
    const end = indices[indices.length - 1];
    if (indices.length !== end - start + 1) {
      setError("הבחירה חייבת להיות רציפה");
      return;
    }
    setLoading(true);
    setError("");
    try {
      await onConfirm(sourceId, indices, target, position);
      onClose();
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const {
    prev_id,
    next_id,
    split_siblings = [],
    recent = [],
  } = suggestions || {};

  const hasSuggestions =
    prev_id || next_id || split_siblings.length > 0 || recent.length > 0;

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-5 mx-4 max-h-[90vh] overflow-y-auto">
        <h2 className="text-lg font-semibold mb-3">העברת הודעות</h2>
        <p className="text-sm text-slate-600 mb-3">
          מ-{sourceId} · {selectedIndices.size} הודעות נבחרו
        </p>

        {hasSuggestions && (
          <div className="mb-4 space-y-3">
            {(prev_id || next_id) && (
              <div>
                <div className="text-xs text-slate-500 mb-1">שכנות כרונולוגיות</div>
                <div className="flex gap-2">
                  {prev_id && (
                    <SuggestionButton
                      id={prev_id}
                      label="← שכנה קודמת"
                      sublabel={prev_id}
                      onPick={pick}
                    />
                  )}
                  {next_id && (
                    <SuggestionButton
                      id={next_id}
                      label="שכנה הבאה →"
                      sublabel={next_id}
                      onPick={pick}
                    />
                  )}
                </div>
              </div>
            )}

            {split_siblings.length > 0 && (
              <div>
                <div className="text-xs text-slate-500 mb-1">
                  מאותו פיצול
                </div>
                <div className="flex flex-wrap gap-1">
                  {split_siblings.map((id) => (
                    <SuggestionButton
                      key={id}
                      id={id}
                      label={id}
                      sublabel="חלק מפיצול"
                      onPick={pick}
                    />
                  ))}
                </div>
              </div>
            )}

            {recent.length > 0 && (
              <div>
                <div className="text-xs text-slate-500 mb-1">שינויים אחרונים</div>
                <div className="space-y-1">
                  {recent.slice(0, 5).map((entry) => (
                    <SuggestionButton
                      key={`${entry.thread_id}-${entry.timestamp}`}
                      id={entry.thread_id}
                      label={entry.thread_id}
                      sublabel={kindLabel(entry.kind)}
                      onPick={pick}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <input
          type="text"
          value={targetId}
          onChange={(e) => setTargetId(e.target.value)}
          placeholder="מזהה שיחת יעד"
          className="w-full border rounded px-3 py-2 text-sm mb-3"
        />

        <div className="space-y-2 mb-4">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              checked={position === "prepend"}
              onChange={() => setPosition("prepend")}
            />
            בתחילת שיחת היעד
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              checked={position === "append"}
              onChange={() => setPosition("append")}
            />
            בסוף שיחת היעד
          </label>
        </div>

        {error && <p className="text-red-600 text-sm mb-2">{error}</p>}

        <div className="flex gap-2 justify-end">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm border rounded">
            ביטול
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={handleSubmit}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded disabled:opacity-50"
          >
            העבר
          </button>
        </div>
      </div>
    </div>
  );
}
