/** Number of selected messages and a "deselect all" button (Escape does the same). */
export default function SelectionBar({ count, onClear }) {
  return (
    <div className="inline-flex items-center gap-2 text-sm">
      <span className={count ? "text-amber-700 font-medium" : "text-slate-400"}>
        {count} נבחרו
      </span>
      <button
        type="button"
        disabled={!count}
        onClick={onClear}
        title="ביטול כל הבחירה (Esc)"
        className="px-2 py-1 border rounded bg-white hover:bg-slate-100 disabled:opacity-40"
      >
        בטל בחירה
      </button>
    </div>
  );
}
