export default function TopicSelect({ topics, value, onChange, sizeFilter }) {
  return (
    <div className="p-3 border-b border-slate-200 bg-white shrink-0">
      <label className="text-xs text-slate-500 block mb-1">נושא</label>
      {sizeFilter && (
        <p className="text-[11px] text-teal-700 mb-1">
          מסונן: {sizeFilter.description}
        </p>
      )}
      {topics.length === 0 ? (
        <p className="text-sm text-slate-500">אין נושאים עם קבוצות בסינון זה.</p>
      ) : (
        <select
          value={value || ""}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm"
        >
          {topics.map((topic) => (
            <option key={topic.id} value={topic.id}>
              {topic.title} ({topic.group_count})
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
