import { windowLabel } from "../goldWindows";

/** Window selector, previous / next, overall progress and the "window done" toggle. */
export default function GoldBar({ gold }) {
  if (!gold.enabled) return null;
  const { windows, index, current } = gold;
  const doneCount = windows.filter((w) => w.done).length;

  return (
    <div className="bg-yellow-50 border-b border-yellow-300 px-4 py-2 flex flex-wrap items-center gap-3 text-sm shrink-0">
      <span className="font-semibold text-yellow-900">חלונות זהב</span>

      <select
        value={index ?? ""}
        onChange={(e) => gold.select(e.target.value === "" ? null : Number(e.target.value))}
        className="text-sm border rounded px-2 py-1 bg-white"
      >
        <option value="">כל השיחות (ללא חלון)</option>
        {windows.map((w) => (
          <option key={w.start} value={w.index}>
            {w.done ? "✓ " : ""}
            {`חלון ${w.index + 1}: ${windowLabel(w)} · ${w.message_count} הודעות`}
          </option>
        ))}
      </select>

      <button
        type="button"
        disabled={index === null || index === 0}
        onClick={() => gold.select(index - 1)}
        className="px-2 py-1 border rounded bg-white disabled:opacity-40"
      >
        ← חלון קודם
      </button>
      <button
        type="button"
        disabled={index === null ? !windows.length : index >= windows.length - 1}
        onClick={() => gold.select(index === null ? 0 : index + 1)}
        className="px-2 py-1 border rounded bg-white disabled:opacity-40"
      >
        חלון הבא →
      </button>

      <span className="text-yellow-900">
        {doneCount} / {windows.length} הושלמו
      </span>

      {current && (
        <>
          <button
            type="button"
            onClick={gold.toggleDone}
            className={`px-3 py-1 rounded border font-medium ${
              current.done
                ? "bg-emerald-600 text-white border-emerald-700"
                : "bg-white text-emerald-800 border-emerald-400 hover:bg-emerald-50"
            }`}
          >
            {current.done ? "✓ החלון הושלם" : "סמן חלון כהושלם"}
          </button>
          <span className="text-xs text-slate-600">
            {current.thread_ids.length} שיחות · {current.message_count} הודעות בחלון.
            הודעות בחלון מסומנות בפס צהוב, הודעות מחוצה לו מעומעמות. סינון ומיון אינם חלים
            בזמן שנבחר חלון.
          </span>
        </>
      )}
    </div>
  );
}
