import { kindLabel } from "../recentHistory";

export default function RecentChangesPanel({ recent, selectedId, onSelect }) {
  if (!recent.length) {
    return (
      <div className="px-3 py-2 text-xs text-slate-400 border-b">
        אין שינויים אחרונים בסשן זה
      </div>
    );
  }

  return (
    <div className="border-b bg-white shrink-0 max-h-36 overflow-y-auto">
      <div className="px-3 py-1.5 text-xs font-medium text-slate-600 sticky top-0 bg-white border-b">
        שינויים אחרונים
      </div>
      <ul className="divide-y divide-slate-50">
        {recent.slice(0, 12).map((entry) => (
          <li key={`${entry.thread_id}-${entry.kind}-${entry.timestamp}`}>
            <button
              type="button"
              onClick={() => onSelect(entry.thread_id)}
              className={`w-full text-right px-3 py-1.5 text-xs hover:bg-violet-50 ${
                selectedId === entry.thread_id ? "bg-violet-100" : ""
              }`}
            >
              <span className="font-medium">{entry.thread_id}</span>
              <span className="text-slate-500 mr-2">· {kindLabel(entry.kind)}</span>
              {entry.related_ids?.length > 1 && (
                <span className="text-violet-600 block truncate">
                  +{entry.related_ids.length - 1} קשורות
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
