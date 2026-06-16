import { kindLabel } from "../recentHistory";

const BADGE_STYLES = {
  split: "bg-violet-100 text-violet-700",
  merge: "bg-blue-100 text-blue-700",
  move: "bg-amber-100 text-amber-700",
  classification: "bg-slate-100 text-slate-600",
  new: "bg-emerald-100 text-emerald-700",
};

export default function ThreadList({
  items,
  selectedId,
  onSelect,
  total,
  highlightIds,
  splitHighlightIds,
  hasClassification = true,
}) {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="px-3 py-2 text-xs text-slate-500 border-b">
        {items.length} מתוך {total}
      </div>
      <ul className="divide-y divide-slate-100">
        {items.map((item) => {
          const recentKind = highlightIds?.get(item.thread_id);
          const inSplitGroup = splitHighlightIds?.has(item.thread_id);
          return (
            <li key={item.thread_id}>
              <button
                type="button"
                onClick={() => onSelect(item.thread_id)}
                className={`w-full text-right px-3 py-2 text-sm hover:bg-slate-100 ${
                  selectedId === item.thread_id
                    ? "bg-blue-50 border-r-4 border-blue-500"
                    : inSplitGroup
                      ? "bg-violet-50/80"
                      : recentKind
                        ? "bg-slate-50"
                        : ""
                }`}
              >
                <div className="font-medium truncate flex items-center gap-1 justify-end flex-wrap">
                  {recentKind && (
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded ${BADGE_STYLES[recentKind] || BADGE_STYLES.classification}`}
                    >
                      {kindLabel(recentKind)}
                    </span>
                  )}
                  {inSplitGroup && !recentKind && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-100 text-violet-700">
                      פיצול
                    </span>
                  )}
                  <span>{item.thread_id}</span>
                </div>
                <div className="text-xs text-slate-500 flex gap-2 flex-wrap">
                  <span>{item.num_messages} הודעות</span>
                  <span>{item.num_unique_senders} משתתפים</span>
                  <span>{item.start_time?.slice(0, 10)}</span>
                </div>
              {!hasClassification && (
                <span className="text-xs text-slate-400">ללא סיווג</span>
              )}
              {hasClassification && !item.is_knowledge_bearing && (
                <span className="text-xs text-amber-600">לא מועיל</span>
              )}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
