import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchCategories,
  fetchMeta,
  fetchPageClaims,
  fetchPages,
  fetchTopics,
  mergePages,
  moveClaim,
  updatePage,
} from "./api";
import ClaimsPanel from "./components/ClaimsPanel";
import MergeDialog from "./components/MergeDialog";
import MoveClaimDialog from "./components/MoveClaimDialog";
import PageEditor from "./components/PageEditor";
import PageList from "./components/PageList";
import ResizeHandle from "./components/ResizeHandle";
import {
  clampListWidth,
  clampSideWidth,
  loadPanelLayout,
  savePanelLayout,
} from "./panelLayout";

export default function App() {
  const [meta, setMeta] = useState(null);
  const [sections, setSections] = useState([]);
  const [pages, setPages] = useState([]);
  const [categories, setCategories] = useState([]);
  const [topics, setTopics] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [listFilter, setListFilter] = useState("");
  const [claims, setClaims] = useState([]);
  const [claimsTotal, setClaimsTotal] = useState(0);
  const [claimsLoading, setClaimsLoading] = useState(false);
  const [selectedClaim, setSelectedClaim] = useState(null);
  const [saving, setSaving] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [toast, setToast] = useState("");

  const layoutRef = useRef(null);
  const [listWidth, setListWidth] = useState(() => loadPanelLayout().listWidth);
  const [sideWidth, setSideWidth] = useState(() => loadPanelLayout().sideWidth);

  const selectedPage = useMemo(
    () => pages.find((p) => p.id === selectedId) || null,
    [pages, selectedId]
  );

  const persistPanelLayout = useCallback(() => {
    savePanelLayout({ listWidth, sideWidth });
  }, [listWidth, sideWidth]);

  const resizeListPanel = useCallback((event) => {
    const rect = layoutRef.current?.getBoundingClientRect();
    if (!rect) return;
    const rtl = document.documentElement.dir === "rtl";
    const width = rtl ? rect.right - event.clientX : event.clientX - rect.left;
    setListWidth(clampListWidth(width));
  }, []);

  const resizeSidePanel = useCallback((event) => {
    const rect = layoutRef.current?.getBoundingClientRect();
    if (!rect) return;
    const rtl = document.documentElement.dir === "rtl";
    const width = rtl ? event.clientX - rect.left : rect.right - event.clientX;
    setSideWidth(clampSideWidth(width));
  }, []);

  const refreshMeta = useCallback(async () => {
    setMeta(await fetchMeta());
  }, []);

  const refreshPages = useCallback(async () => {
    const data = await fetchPages();
    setSections(data.sections || []);
    setPages(data.items || []);
    return data;
  }, []);

  const refreshTopics = useCallback(async () => {
    const data = await fetchTopics();
    setTopics(data.items || []);
  }, []);

  const loadClaims = useCallback(async (pageId) => {
    if (!pageId) {
      setClaims([]);
      setClaimsTotal(0);
      return;
    }
    setClaimsLoading(true);
    try {
      const data = await fetchPageClaims(pageId, { limit: "200", offset: "0" });
      setClaims(data.items || []);
      setClaimsTotal(data.total || 0);
    } finally {
      setClaimsLoading(false);
    }
  }, []);

  useEffect(() => {
    Promise.all([refreshMeta(), refreshPages(), fetchCategories(), refreshTopics()])
      .then(([_, pageData, catData]) => {
        setCategories(catData.items || []);
        const first = pageData.items?.[0]?.id;
        if (first) setSelectedId(first);
      })
      .catch((err) => setToast(err.message));
  }, [refreshMeta, refreshPages, refreshTopics]);

  useEffect(() => {
    loadClaims(selectedId).catch((err) => setToast(err.message));
    setSelectedClaim(null);
  }, [selectedId, loadClaims]);

  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(""), 3500);
    return () => clearTimeout(t);
  }, [toast]);

  const handleSavePage = async (fields) => {
    if (!selectedId) return;
    setSaving(true);
    try {
      const result = await updatePage(selectedId, fields);
      setMeta(result.meta);
      await refreshPages();
      setToast("העמוד נשמר");
    } catch (err) {
      setToast(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleMerge = async (targetId) => {
    if (!selectedId) return;
    setSaving(true);
    try {
      const result = await mergePages(selectedId, targetId);
      setMeta(result.meta);
      setSections(result.sections || []);
      await refreshPages();
      setSelectedId(targetId);
      setMergeOpen(false);
      setToast("העמודים מוזגו");
    } catch (err) {
      setToast(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleMoveClaim = async (targetTopicId) => {
    if (!selectedClaim) return;
    setSaving(true);
    try {
      await moveClaim({
        topicId: selectedClaim.topic_id,
        claimKey: selectedClaim.key,
        targetTopicId,
      });
      await Promise.all([refreshMeta(), refreshPages(), refreshTopics()]);
      await loadClaims(selectedId);
      setMoveOpen(false);
      setSelectedClaim(null);
      setToast("הטענה הועברה");
    } catch (err) {
      setToast(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-screen flex flex-col">
      <header className="shrink-0 border-b border-slate-200 bg-white px-4 py-3">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-lg font-semibold">עריכת תוכנית הויקי</h1>
            <p className="text-xs text-slate-500">
              {meta?.plan_path} · {meta?.page_count ?? 0} עמודים
            </p>
          </div>
          {meta && (
            <div className="flex gap-3 text-xs text-slate-600">
              <span>{meta.topic_count} נושאים</span>
              <span>{meta.total_claims} טענות</span>
              <span>{meta.link_count} קישורים</span>
            </div>
          )}
        </div>
      </header>

      <div ref={layoutRef} className="flex-1 flex min-h-0">
        <aside
          className="shrink-0 flex flex-col border-l border-slate-200 bg-white"
          style={{ width: listWidth }}
        >
          <PageList
            sections={sections}
            selectedId={selectedId}
            onSelect={setSelectedId}
            filter={listFilter}
            onFilterChange={setListFilter}
          />
        </aside>

        <ResizeHandle
          label="שינוי רוחב רשימה"
          onResize={resizeListPanel}
          onResizeEnd={persistPanelLayout}
        />

        <main className="flex-1 flex flex-col min-w-0 min-h-0 bg-slate-50">
          <PageEditor
            page={selectedPage}
            categories={categories}
            saving={saving}
            onSave={handleSavePage}
          />
          <div className="border-t border-slate-200 bg-white min-h-[38%] max-h-[45%] flex flex-col">
            <ClaimsPanel
              claims={claims}
              total={claimsTotal}
              loading={claimsLoading}
              selectedClaimKey={selectedClaim?.key}
              onSelectClaim={setSelectedClaim}
              onMoveClaim={(claim) => {
                setSelectedClaim(claim);
                setMoveOpen(true);
              }}
            />
          </div>
        </main>

        <ResizeHandle
          label="שינוי רוחב פאנל פעולות"
          onResize={resizeSidePanel}
          onResizeEnd={persistPanelLayout}
        />

        <aside
          className="shrink-0 flex flex-col border-r border-slate-200 bg-slate-50"
          style={{ width: sideWidth }}
        >
          <div className="p-4 border-b border-slate-200 bg-white">
            <h3 className="text-sm font-semibold">פעולות</h3>
            <p className="text-xs text-slate-500 mt-1">
              שינויים נשמרים ל־
              <code className="text-[11px]">wiki_plan_edited.json</code>
              {" "}ו־
              <code className="text-[11px]">claims_aggregated_edited.json</code>
            </p>
          </div>
          <div className="p-4 space-y-3">
            <button
              type="button"
              disabled={!selectedId || saving}
              onClick={() => setMergeOpen(true)}
              className="w-full px-3 py-2 text-sm rounded border border-slate-300 bg-white hover:bg-teal-50 disabled:opacity-40"
            >
              מיזוג עם עמוד אחר
            </button>
            <p className="text-xs text-slate-500 leading-relaxed">
              לעריכת כותרת, קטגוריה וחיפוש רקע — השתמשו בטופס במרכז. לבחירת טענה
              להעברה — לחצו &quot;העבר...&quot; ליד הטענה או בחרו אותה ואז העבירו.
            </p>
            {selectedPage && (
              <div className="text-xs text-slate-500 border-t border-slate-200 pt-3 space-y-1">
                <div>קטגוריה: {selectedPage.category_title}</div>
                <div>נושאי מקור: {(selectedPage.source_tags || []).join(", ")}</div>
              </div>
            )}
          </div>
        </aside>
      </div>

      <MergeDialog
        open={mergeOpen}
        pages={pages}
        currentPageId={selectedId}
        onClose={() => setMergeOpen(false)}
        onConfirm={handleMerge}
        saving={saving}
      />

      <MoveClaimDialog
        open={moveOpen}
        claim={selectedClaim}
        topics={topics}
        onClose={() => setMoveOpen(false)}
        onConfirm={handleMoveClaim}
        saving={saving}
      />

      {toast && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 bg-slate-800 text-white text-sm px-4 py-2 rounded-lg shadow-lg z-50">
          {toast}
        </div>
      )}
    </div>
  );
}
