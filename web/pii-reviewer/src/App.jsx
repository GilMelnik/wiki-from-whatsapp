import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchClaim,
  fetchClaims,
  fetchMeta,
  fetchStats,
  reviewClaim,
} from "./api";
import ClaimList from "./components/ClaimList";
import ClaimReviewPanel, {
  ReviewActions,
} from "./components/ClaimReviewPanel";
import ResizeHandle from "./components/ResizeHandle";
import {
  clampListWidth,
  clampSideWidth,
  loadPanelLayout,
  savePanelLayout,
} from "./panelLayout";

const FILTER_OPTIONS = [
  { value: "pending", label: "ממתינות" },
  { value: "reviewed", label: "נבדקו" },
  { value: "all", label: "הכל" },
];

const SORT_OPTIONS = [
  { value: "claim_id", label: "מזהה" },
  { value: "thread_id", label: "שיחה" },
  { value: "date", label: "תאריך" },
  { value: "redactions", label: "הסרות" },
];

function listParams(state) {
  return {
    filter: state.filter,
    sort: state.sort,
    order: state.order,
    limit: "200",
    offset: "0",
  };
}

export default function App() {
  const [meta, setMeta] = useState(null);
  const [stats, setStats] = useState(null);
  const [filter, setFilter] = useState("pending");
  const [sort, setSort] = useState("claim_id");
  const [order, setOrder] = useState("asc");

  const [listItems, setListItems] = useState([]);
  const [listTotal, setListTotal] = useState(0);
  const [selectedId, setSelectedId] = useState(null);
  const [claimData, setClaimData] = useState(null);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");

  const layoutRef = useRef(null);
  const [listWidth, setListWidth] = useState(() => loadPanelLayout().listWidth);
  const [sideWidth, setSideWidth] = useState(() => loadPanelLayout().sideWidth);

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
    const [m, s] = await Promise.all([fetchMeta(), fetchStats()]);
    setMeta(m);
    setStats(s);
  }, []);

  const refreshList = useCallback(async () => {
    const params = listParams({ filter, sort, order });
    const data = await fetchClaims(params);
    setListItems(data.items);
    setListTotal(data.total);
    return data;
  }, [filter, sort, order]);

  const loadClaim = useCallback(
    async (claimId) => {
      if (!claimId) {
        setClaimData(null);
        return;
      }
      const data = await fetchClaim(claimId, listParams({ filter, sort, order }));
      setClaimData(data);
    },
    [filter, sort, order]
  );

  useEffect(() => {
    refreshMeta().catch((err) => setToast(err.message));
  }, [refreshMeta]);

  useEffect(() => {
    refreshList()
      .then((data) => {
        if (data.items.length === 0) {
          setSelectedId(null);
          setClaimData(null);
          return;
        }
        setSelectedId((current) => {
          if (current && data.items.some((i) => i.claim_id === current)) {
            return current;
          }
          return data.items[0].claim_id;
        });
      })
      .catch((err) => setToast(err.message));
  }, [refreshList]);

  useEffect(() => {
    if (selectedId) {
      loadClaim(selectedId).catch((err) => setToast(err.message));
    }
  }, [selectedId, loadClaim]);

  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(""), 3000);
    return () => clearTimeout(t);
  }, [toast]);

  const handleReview = async (decision) => {
    if (!selectedId) return;
    setSaving(true);
    try {
      const result = await reviewClaim(selectedId, decision);
      setMeta(result.meta);
      const nextId = claimData?.queue?.next_in_queue;
      await refreshList();
      await refreshMeta();
      if (nextId) {
        setSelectedId(nextId);
      } else if (filter === "pending") {
        const data = await fetchClaims(listParams({ filter, sort, order }));
        setSelectedId(data.items[0]?.claim_id || null);
      } else {
        setSelectedId(result.claim.claim_id);
        setClaimData({ claim: result.claim, queue: claimData?.queue });
      }
      setToast(decision === "accept" ? "ההסרה אושרה" : "הנתון שוחזר");
    } catch (err) {
      setToast(err.message);
    } finally {
      setSaving(false);
    }
  };

  const claim = claimData?.claim;

  return (
    <div className="h-screen flex flex-col">
      <header className="shrink-0 border-b border-slate-200 bg-white px-4 py-3">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-lg font-semibold">בדיקת פרטיות בטענות</h1>
            <p className="text-xs text-slate-500">
              {meta?.claims_path} · {meta?.review?.pending ?? 0} ממתינות
            </p>
          </div>
          {stats?.review && (
            <div className="flex gap-3 text-xs text-slate-600">
              <span>ממתינות: {stats.review.pending}</span>
              <span>אושרו: {stats.review.accepted}</span>
              <span>שוחזרו: {stats.review.restored}</span>
            </div>
          )}
        </div>
        <div className="flex gap-3 mt-3 flex-wrap items-center">
          <label className="text-sm flex items-center gap-1">
            סינון
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="border border-slate-300 rounded px-2 py-1 text-sm"
            >
              {FILTER_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm flex items-center gap-1">
            מיון
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              className="border border-slate-300 rounded px-2 py-1 text-sm"
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm flex items-center gap-1">
            כיוון
            <select
              value={order}
              onChange={(e) => setOrder(e.target.value)}
              className="border border-slate-300 rounded px-2 py-1 text-sm"
            >
              <option value="asc">עולה</option>
              <option value="desc">יורד</option>
            </select>
          </label>
        </div>
      </header>

      <div ref={layoutRef} className="flex-1 flex min-h-0">
        <aside
          className="shrink-0 flex flex-col border-l border-slate-200 bg-white"
          style={{ width: listWidth }}
        >
          <ClaimList
            items={listItems}
            selectedId={selectedId}
            onSelect={setSelectedId}
            total={listTotal}
          />
        </aside>

        <ResizeHandle
          label="שינוי רוחב רשימה"
          onResize={resizeListPanel}
          onResizeEnd={persistPanelLayout}
        />

        <main className="flex-1 flex flex-col min-w-0 bg-slate-50">
          <ClaimReviewPanel
            claim={claim}
            queue={claimData?.queue}
            saving={saving}
            onAccept={() => handleReview("accept")}
            onRestore={() => handleReview("restore")}
            onPrev={setSelectedId}
            onNext={setSelectedId}
          />
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
              החלטה נשמרת ל־<code className="text-[11px]">claims_edited.json</code>
            </p>
          </div>
          <ReviewActions
            claim={claim}
            saving={saving}
            onAccept={() => handleReview("accept")}
            onRestore={() => handleReview("restore")}
          />
          {claim && (
            <div className="p-4 text-xs text-slate-500 space-y-2 border-t border-slate-200">
              <div>תמיכה: {claim.support_count ?? "—"}</div>
              <div>הצהרות: {claim.statement_count ?? "—"}</div>
              <div>תגובות: {claim.reaction_endorser_count ?? "—"}</div>
              {claim.entities?.length > 0 && (
                <div>ישויות: {claim.entities.join(", ")}</div>
              )}
            </div>
          )}
        </aside>
      </div>

      {toast && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 bg-slate-800 text-white text-sm px-4 py-2 rounded-lg shadow-lg z-50">
          {toast}
        </div>
      )}
    </div>
  );
}
