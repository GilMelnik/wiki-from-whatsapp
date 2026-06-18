export default function PageList({ sections, selectedId, onSelect, filter, onFilterChange }) {
  const needle = filter.trim().toLowerCase();

  return (
    <div className="flex flex-col h-full">
      <div className="p-3 border-b border-slate-200 bg-white">
        <input
          type="search"
          value={filter}
          onChange={(e) => onFilterChange(e.target.value)}
          placeholder="חיפוש עמוד..."
          className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm"
        />
      </div>
      <div className="flex-1 overflow-y-auto">
        {sections.map((section) => {
          const pages = section.pages.filter((page) => {
            if (!needle) return true;
            return (
              page.title.toLowerCase().includes(needle) ||
              page.id.toLowerCase().includes(needle)
            );
          });
          if (pages.length === 0) return null;
          return (
            <div key={section.category_title} className="mb-1">
              <div className="px-3 py-2 text-xs font-semibold text-slate-500 bg-slate-100 sticky top-0">
                {section.category_title}
                <span className="font-normal text-slate-400 mr-1">({pages.length})</span>
              </div>
              <ul>
                {pages.map((page) => {
                  const active = page.id === selectedId;
                  return (
                    <li key={page.id}>
                      <button
                        type="button"
                        onClick={() => onSelect(page.id)}
                        className={`w-full text-right px-3 py-2 text-sm border-b border-slate-100 hover:bg-teal-50 ${
                          active ? "bg-teal-100 border-r-4 border-r-teal-600" : ""
                        }`}
                      >
                        <div className="font-medium truncate">{page.title}</div>
                        <div className="text-xs text-slate-500 mt-0.5 flex gap-2">
                          <span>{page.id}</span>
                          <span>· {page.merged_claim_count} טענות</span>
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </div>
    </div>
  );
}
