const STATUS_STYLES = {
  pending: "bg-amber-100 text-amber-800",
  accepted: "bg-slate-100 text-slate-600",
  restored: "bg-emerald-100 text-emerald-800",
};

const STATUS_LABELS = {
  pending: "ממתין",
  accepted: "הוסר",
  restored: "שוחזר",
};

export default function ClaimList({ items, selectedId, onSelect, total }) {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="px-3 py-2 text-xs text-slate-500 border-b">
        {items.length} מתוך {total}
      </div>
      <ul className="divide-y divide-slate-100">
        {items.map((item) => (
          <li key={item.claim_id}>
            <button
              type="button"
              onClick={() => onSelect(item.claim_id)}
              className={`w-full text-right px-3 py-2 text-sm hover:bg-slate-100 ${
                selectedId === item.claim_id
                  ? "bg-blue-50 border-r-4 border-blue-500"
                  : ""
              }`}
            >
              <div className="font-medium truncate flex items-center gap-1 justify-end flex-wrap">
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded ${STATUS_STYLES[item.review_status] || STATUS_STYLES.pending}`}
                >
                  {STATUS_LABELS[item.review_status] || item.review_status}
                </span>
                <span className="truncate">{item.claim_id}</span>
              </div>
              <div className="text-xs text-slate-500 truncate mt-0.5">
                {item.claim_text}
              </div>
              <div className="text-xs text-slate-400 flex gap-2 flex-wrap mt-1">
                {item.redaction_summary && (
                  <span>{item.redaction_summary}</span>
                )}
                {item.thread_id && <span>{item.thread_id}</span>}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
