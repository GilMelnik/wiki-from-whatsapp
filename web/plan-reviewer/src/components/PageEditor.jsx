import { useEffect, useState } from "react";

export default function PageEditor({ page, categories, saving, onSave }) {
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("");
  const [searchFocus, setSearchFocus] = useState("");
  const [rationale, setRationale] = useState("");
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!page) {
      setTitle("");
      setCategory("");
      setSearchFocus("");
      setRationale("");
      setDirty(false);
      return;
    }
    setTitle(page.title || "");
    setCategory(page.category || "emergent");
    setSearchFocus(page.search_focus || "");
    setRationale(page.rationale || "");
    setDirty(false);
  }, [page]);

  if (!page) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
        בחרו עמוד מהרשימה
      </div>
    );
  }

  const markDirty = () => setDirty(true);

  const handleSave = () => {
    onSave({
      title,
      category,
      search_focus: searchFocus,
      rationale,
    });
    setDirty(false);
  };

  return (
    <div className="flex-1 overflow-y-auto p-5">
      <div className="max-w-3xl space-y-4">
        <div>
          <h2 className="text-xl font-semibold">{page.title}</h2>
          <p className="text-xs text-slate-500 mt-1">
            מזהה: <code className="bg-slate-100 px-1 rounded">{page.id}</code>
            {" · "}
            {page.claim_count} טענות גולמיות · {page.merged_claim_count} לאחר מיזוג
          </p>
        </div>

        <label className="block text-sm">
          <span className="font-medium">כותרת בעברית</span>
          <input
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              markDirty();
            }}
            className="mt-1 w-full border border-slate-300 rounded px-3 py-2"
          />
        </label>

        <label className="block text-sm">
          <span className="font-medium">קטגוריה (תפריט ניווט)</span>
          <select
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              markDirty();
            }}
            className="mt-1 w-full border border-slate-300 rounded px-3 py-2"
          >
            {categories.map((cat) => (
              <option key={cat.id} value={cat.id}>
                {cat.title}
              </option>
            ))}
          </select>
        </label>

        <label className="block text-sm">
          <span className="font-medium">חיפוש רקע (אנגלית)</span>
          <input
            value={searchFocus}
            onChange={(e) => {
              setSearchFocus(e.target.value);
              markDirty();
            }}
            dir="ltr"
            className="mt-1 w-full border border-slate-300 rounded px-3 py-2 text-left"
            placeholder="e.g. gay surrogacy costs overview"
          />
        </label>

        <label className="block text-sm">
          <span className="font-medium">הערת תכנון</span>
          <textarea
            value={rationale}
            onChange={(e) => {
              setRationale(e.target.value);
              markDirty();
            }}
            rows={3}
            className="mt-1 w-full border border-slate-300 rounded px-3 py-2"
          />
        </label>

        <div className="text-sm">
          <span className="font-medium">נושאי מקור (source_tags)</span>
          <div className="mt-2 flex flex-wrap gap-2">
            {(page.source_tags || []).map((tag) => (
              <span
                key={tag}
                className="text-xs bg-slate-200 text-slate-700 px-2 py-1 rounded"
              >
                {tag}
              </span>
            ))}
          </div>
        </div>

        <button
          type="button"
          disabled={!dirty || saving}
          onClick={handleSave}
          className="px-4 py-2 rounded bg-teal-600 text-white text-sm font-medium disabled:opacity-40 hover:bg-teal-700"
        >
          {saving ? "שומר..." : "שמירת שינויים"}
        </button>
      </div>
    </div>
  );
}
