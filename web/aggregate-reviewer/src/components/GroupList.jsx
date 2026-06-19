export default function GroupList({
  items,
  total,
  selectedKey,
  onSelect,
  sort,
  order,
  onSortChange,
  onOrderChange,
  sizeFilter,
}) {
  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-2 border-b border-slate-100 bg-slate-50 shrink-0 flex gap-2 flex-wrap">
        <select
          value={sort}
          onChange={(e) => onSortChange(e.target.value)}
          className="border border-slate-300 rounded px-2 py-1 text-xs flex-1 min-w-0"
        >
          <option value="support">תמיכה</option>
          <option value="size">גודל</option>
        </select>
        <select
          value={order}
          onChange={(e) => onOrderChange(e.target.value)}
          className="border border-slate-300 rounded px-2 py-1 text-xs"
        >
          <option value="desc">יורד</option>
          <option value="asc">עולה</option>
        </select>
      </div>
      <div className="px-3 py-2 text-xs text-slate-500 border-b border-slate-100">
        {total} קבוצות
        {sizeFilter && (
          <span className="text-teal-700"> · {sizeFilter.description}</span>
        )}
      </div>
      <div className="flex-1 overflow-y-auto">
        {items.length === 0 && (
          <p className="p-4 text-sm text-slate-500">אין קבוצות בסינון הנוכחי.</p>
        )}
        {items.map((item) => {
          const selected = item.key === selectedKey;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onSelect(item.key)}
              className={`w-full text-right px-3 py-2.5 border-b border-slate-100 hover:bg-teal-50 ${
                selected ? "bg-teal-100 border-teal-200" : ""
              }`}
            >
              <p className="text-sm line-clamp-2 leading-relaxed">{item.claim_text}</p>
              <div className="mt-1 flex gap-2 text-xs text-slate-500">
                <span>{item.endorsement_count} טענות</span>
                <span>תמיכה: {item.support_count ?? "—"}</span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
