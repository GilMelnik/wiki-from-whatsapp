export default function SplitResultBanner({
  splitResult,
  selectedId,
  onSelect,
  onDismiss,
}) {
  if (!splitResult?.thread_ids?.length) return null;

  const { source_id, thread_ids } = splitResult;

  return (
    <div className="px-4 py-3 bg-violet-50 border-b border-violet-200">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <span className="text-sm font-semibold text-violet-900">
          השיחה פוצלה
        </span>
        {source_id && (
          <span className="text-xs text-violet-600">
            מ-{source_id} → {thread_ids.length} שיחות
          </span>
        )}
        <button
          type="button"
          onClick={onDismiss}
          className="text-xs text-violet-500 hover:text-violet-800 mr-auto"
        >
          סגור
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        {thread_ids.map((id) => (
          <button
            key={id}
            type="button"
            onClick={() => onSelect(id)}
            className={`text-sm px-3 py-1.5 rounded-full border transition-colors ${
              selectedId === id
                ? "bg-violet-600 text-white border-violet-600"
                : "bg-white text-violet-800 border-violet-300 hover:bg-violet-100"
            }`}
          >
            {id}
            {id.includes("-split-") && (
              <span className="text-xs opacity-75 mr-1"> (חדש)</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
