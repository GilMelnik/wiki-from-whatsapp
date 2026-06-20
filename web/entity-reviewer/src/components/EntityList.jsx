const STATUS_BADGE = {
  suggested: "bg-slate-100 text-slate-600",
  accepted: "bg-teal-100 text-teal-700",
  rejected: "bg-rose-100 text-rose-700",
};

const STATUS_LABEL = {
  suggested: "מוצע",
  accepted: "אושר",
  rejected: "נדחה",
};

export default function EntityList({
  items,
  total,
  selectedId,
  onSelect,
  status,
  onStatusChange,
  query,
  onQueryChange,
  sort,
  order,
  onSortChange,
  onOrderChange,
}) {
  return (
    <div className="flex flex-col min-h-0 flex-1">
      <div className="p-3 border-b border-slate-200 bg-white shrink-0 space-y-2">
        <input
          type="search"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="חיפוש ישות / כינוי..."
          className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm"
        />
        <div className="flex gap-2">
          <select
            value={status}
            onChange={(e) => onStatusChange(e.target.value)}
            className="flex-1 border border-slate-300 rounded px-2 py-1.5 text-xs"
          >
            <option value="">כל הסטטוסים</option>
            <option value="suggested">מוצע</option>
            <option value="accepted">אושר</option>
            <option value="rejected">נדחה</option>
          </select>
          <select
            value={sort}
            onChange={(e) => onSortChange(e.target.value)}
            className="border border-slate-300 rounded px-2 py-1.5 text-xs"
          >
            <option value="count">לפי אזכורים</option>
            <option value="size">לפי גודל קבוצה</option>
            <option value="score">לפי לכידות</option>
          </select>
          <button
            type="button"
            onClick={() => onOrderChange(order === "desc" ? "asc" : "desc")}
            className="border border-slate-300 rounded px-2 text-xs"
            title="הפוך סדר"
          >
            {order === "desc" ? "↓" : "↑"}
          </button>
        </div>
        <p className="text-[11px] text-slate-400">{total} ישויות</p>
      </div>

      <div className="flex-1 overflow-y-auto">
        {items.map((item) => {
          const selected = item.entity_id === selectedId;
          return (
            <button
              key={item.entity_id}
              type="button"
              onClick={() => onSelect(item.entity_id)}
              className={`w-full text-right px-3 py-2.5 border-b border-slate-100 bg-white hover:bg-slate-50 ${
                selected ? "entity-card-selected" : ""
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium truncate">
                  {item.canonical_name || "(ללא שם)"}
                </span>
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded shrink-0 ${
                    STATUS_BADGE[item.status] || STATUS_BADGE.suggested
                  }`}
                >
                  {STATUS_LABEL[item.status] || item.status}
                </span>
              </div>
              <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-500">
                {item.member_count > 1 && (
                  <span className="text-amber-700 font-medium">
                    {item.member_count} שמות
                  </span>
                )}
                <span>{item.total_count} אזכורים</span>
                {item.member_count > 1 && <span>לכידות {item.score}</span>}
              </div>
              {item.member_count > 1 && (
                <div className="mt-1 text-[11px] text-slate-400 truncate">
                  {item.aliases.join(" · ")}
                </div>
              )}
            </button>
          );
        })}
        {items.length === 0 && (
          <p className="p-4 text-sm text-slate-500">לא נמצאו ישויות.</p>
        )}
      </div>
    </div>
  );
}
