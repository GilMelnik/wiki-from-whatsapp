import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchMeta,
  fetchStats,
  fetchTaxonomy,
  fetchThread,
  fetchThreads,
  mergeThreads,
  moveMessages,
  splitThread,
  updateClassification,
} from "./api";
import ClassificationPanel from "./components/ClassificationPanel";
import MergeDialog from "./components/MergeDialog";
import MessageViewer from "./components/MessageViewer";
import MoveDialog from "./components/MoveDialog";
import RecentChangesPanel from "./components/RecentChangesPanel";
import SplitDialog from "./components/SplitDialog";
import SplitResultBanner from "./components/SplitResultBanner";
import StatsPanel from "./components/StatsPanel";
import ResizeHandle from "./components/ResizeHandle";
import ThreadList from "./components/ThreadList";
import {
  clampListWidth,
  clampSideWidth,
  loadPanelLayout,
  savePanelLayout,
} from "./panelLayout";
import {
  addRecent,
  addRecentBatch,
  highlightMap,
  loadRecent,
  splitGroup,
  splitSiblings,
} from "./recentHistory";
import {
  buildQuoteGraph,
  EMPTY_QUOTE_GRAPH,
  expandSelection,
  quoteClosureForIndex,
} from "./quoteGraph";

const FILTER_OPTIONS = [
  { value: "useless", label: "לא מועילות" },
  { value: "knowledge", label: "ידע מועיל" },
  { value: "all", label: "הכל" },
];

const SORT_OPTIONS = [
  { value: "num_messages", label: "מספר הודעות" },
  { value: "participants", label: "משתתפים" },
  { value: "start_time", label: "זמן התחלה" },
  { value: "duration", label: "משך זמן" },
];

function listParams(state) {
  const p = {
    filter: state.filter,
    sort: state.sort,
    order: state.order,
    limit: "200",
    offset: "0",
  };
  if (state.bucketFilter) {
    const bf = state.bucketFilter;
    if (bf.chart === "num_messages") {
      p.num_messages_min = String(bf.min);
      p.num_messages_max = String(bf.max);
    } else if (bf.chart === "num_unique_senders") {
      p.participants_min = String(bf.min);
      p.participants_max = String(bf.max);
    } else if (bf.chart === "duration_sec") {
      p.duration_min = String(bf.min);
      p.duration_max = String(bf.max);
    } else if (bf.chart === "start_time") {
      p.start_month = String(bf.min);
    }
  }
  return p;
}

function recordSplit(sourceId, threadIds) {
  const entries = [
    {
      thread_id: threadIds[0],
      kind: "split",
      source_id: sourceId,
      related_ids: threadIds,
      label: `פוצל ל-${threadIds.length} שיחות`,
    },
    ...threadIds.map((id) => ({
      thread_id: id,
      kind: id.includes("-split-") ? "new" : "split",
      source_id: sourceId,
      related_ids: threadIds,
    })),
  ];
  return addRecentBatch(entries);
}

export default function App() {
  const [meta, setMeta] = useState(null);
  const inspectOnly = meta?.inspect_only ?? false;
  const hasClassification = meta?.has_classification ?? true;

  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState("num_messages");
  const [order, setOrder] = useState("desc");
  const [bucketFilter, setBucketFilter] = useState(null);
  const [activeBucket, setActiveBucket] = useState(null);

  const [listItems, setListItems] = useState([]);
  const [listTotal, setListTotal] = useState(0);
  const [stats, setStats] = useState(null);
  const [taxonomy, setTaxonomy] = useState(null);

  const [selectedId, setSelectedId] = useState(null);
  const [threadData, setThreadData] = useState(null);
  const [selectedIndices, setSelectedIndices] = useState(new Set());

  const [recent, setRecent] = useState(() => loadRecent());
  const [splitResult, setSplitResult] = useState(null);

  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");
  const [mergeOpen, setMergeOpen] = useState(false);
  const [splitOpen, setSplitOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);

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

  const queueParams = { filter, sort, order };

  const messages = threadData?.thread?.messages || [];
  const quoteGraph = useMemo(
    () => (messages.length ? buildQuoteGraph(messages) : EMPTY_QUOTE_GRAPH),
    [messages]
  );

  const highlightIds = useMemo(() => highlightMap(recent), [recent]);

  const splitHighlightIds = useMemo(() => {
    if (splitResult?.thread_ids) {
      return new Set(splitResult.thread_ids);
    }
    const group = selectedId ? splitGroup(recent, selectedId) : null;
    if (group?.related_ids) {
      return new Set(group.related_ids);
    }
    return new Set();
  }, [splitResult, recent, selectedId]);

  const isSplitPart = useMemo(() => {
    if (splitHighlightIds.has(selectedId)) return true;
    return Boolean(selectedId && splitGroup(recent, selectedId));
  }, [splitHighlightIds, selectedId, recent]);

  const moveSuggestions = useMemo(() => {
    const neighbors = threadData?.neighbors || {};
    const siblings = selectedId ? splitSiblings(recent, selectedId) : [];
    const recentForMove = recent.filter(
      (e) => e.thread_id !== selectedId && e.thread_id !== threadData?.thread?.thread_id
    );
    return {
      prev_id: neighbors.prev_id,
      next_id: neighbors.next_id,
      split_siblings: siblings,
      recent: recentForMove,
    };
  }, [threadData, selectedId, recent]);

  const refreshList = useCallback(async () => {
    const data = await fetchThreads(listParams({ filter, sort, order, bucketFilter }));
    setListItems(data.items);
    setListTotal(data.total);
  }, [filter, sort, order, bucketFilter]);

  const refreshStats = useCallback(async () => {
    const data = await fetchStats(filter);
    setStats(data);
  }, [filter]);

  const loadThread = useCallback(
    async (id) => {
      if (!id) return;
      const data = await fetchThread(id, queueParams);
      setThreadData(data);
      setSelectedIndices(new Set());
    },
    [filter, sort, order]
  );

  useEffect(() => {
    fetchMeta()
      .then((m) => {
        setMeta(m);
        if (m.inspect_only || !m.has_classification) {
          setFilter("all");
        } else {
          setFilter("useless");
        }
      })
      .catch((e) => setToast(e.message));
    fetchTaxonomy().then(setTaxonomy);
  }, []);

  useEffect(() => {
    if (!meta) return;
    refreshList().catch((e) => setToast(e.message));
    refreshStats().catch((e) => setToast(e.message));
  }, [meta, refreshList, refreshStats]);

  useEffect(() => {
    if (selectedId) loadThread(selectedId).catch((e) => setToast(e.message));
  }, [selectedId, loadThread]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  const handleSelect = (id) => setSelectedId(id);

  const handleToggle = (index) => {
    const { quotesTarget, quotedBy } = quoteGraph;
    setSelectedIndices((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        for (const i of quoteClosureForIndex(index, quotesTarget, quotedBy)) {
          next.add(i);
        }
      }
      return next;
    });
  };

  const handleRangeSelect = (start, end) => {
    const { quotesTarget, quotedBy } = quoteGraph;
    const range = [];
    for (let i = start; i <= end; i++) range.push(i);
    setSelectedIndices(expandSelection(range, quotesTarget, quotedBy));
  };

  const handleBucketClick = (chartKey, bucket, index) => {
    if (
      activeBucket?.chart === chartKey &&
      activeBucket?.index === index
    ) {
      setBucketFilter(null);
      setActiveBucket(null);
    } else {
      setBucketFilter({ chart: chartKey, ...bucket });
      setActiveBucket({ chart: chartKey, index });
    }
  };

  const afterMutation = async (newId, options = {}) => {
    await refreshList();
    await refreshStats();
    if (newId) {
      setSelectedId(newId);
      if (!options.skipLoad) {
        await loadThread(newId);
      }
    } else if (selectedId) {
      await loadThread(selectedId);
    }
  };

  const handleSave = async (payload, andNext = false) => {
    if (!selectedId) return;
    setSaving(true);
    try {
      await updateClassification(selectedId, payload);
      setRecent(
        addRecent({
          thread_id: selectedId,
          kind: "classification",
          label: "סיווג עודכן",
        })
      );
      setToast("נשמר");
      await refreshList();
      await refreshStats();
      await loadThread(selectedId);
      if (andNext && threadData?.queue?.next_in_queue) {
        setSelectedId(threadData.queue.next_in_queue);
      }
    } catch (e) {
      setToast(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleMerge = async (threadIds, inheritId) => {
    const survivorId = [...threadIds].sort()[0];
    const result = await mergeThreads({
      thread_ids: threadIds,
      survivor_id: survivorId,
      inherit_classification: inheritId,
    });
    setRecent(
      addRecent({
        thread_id: result.survivor_id,
        kind: "merge",
        related_ids: threadIds,
        label: `מוזג מ-${threadIds.join(", ")}`,
      })
    );
    setSplitResult(null);
    setToast(`מוזג ל-${result.survivor_id}`);
    await afterMutation(result.survivor_id);
  };

  const handleSplit = async (sourceId, mode, messageIndices) => {
    const result = await splitThread({
      source_id: sourceId,
      mode,
      message_indices: messageIndices,
    });
    const focusId = result.focus_thread_id || result.new_thread_id;
    const threadIds = result.thread_ids || [focusId];

    setSplitResult({
      source_id: sourceId,
      thread_ids: threadIds,
      threads: [result.remainder, result.thread].filter(Boolean),
      classifications: result.classification ? [result.classification] : [],
      focus_id: focusId,
    });

    setRecent(recordSplit(sourceId, threadIds));
    setFilter("all");
    setToast(`נוצרה שיחה ${focusId}`);

    const listData = await fetchThreads(
      listParams({ filter: "all", sort, order, bucketFilter })
    );
    setListItems(listData.items);
    setListTotal(listData.total);
    await refreshStats();

    setSelectedId(focusId);
    setThreadData({
      thread: result.thread,
      classification: result.classification,
      enriched: null,
      neighbors: null,
      queue: null,
    });
    setSelectedIndices(new Set());
    loadThread(focusId).catch((e) => setToast(e.message));
  };

  const handleSplitSelect = (id) => {
    setSelectedId(id);
    const idx = splitResult?.thread_ids?.indexOf(id);
    if (idx >= 0 && splitResult?.threads?.[idx]) {
      setThreadData((prev) => ({
        ...prev,
        thread: splitResult.threads[idx],
        classification: splitResult.classifications[idx],
      }));
    }
    loadThread(id).catch((e) => setToast(e.message));
  };

  const handleMove = async (sourceId, indices, targetId, position) => {
    const result = await moveMessages({
      source_id: sourceId,
      message_indices: indices,
      target_id: targetId,
      position,
    });
    setRecent(
      addRecent({
        thread_id: targetId,
        kind: "move",
        source_id: sourceId,
        related_ids: [sourceId, targetId],
        label: `הודעות הועברו מ-${sourceId}`,
      })
    );
    setToast(
      result.source_removed
        ? `הועבר; ${result.source_removed} נמחק`
        : "הודעות הועברו"
    );
    await afterMutation(result.target_id);
  };

  const neighbors = threadData?.neighbors;

  return (
    <div className="h-screen flex flex-col">
      <header className="bg-white border-b px-4 py-3 flex flex-wrap items-center gap-3 shadow-sm">
        <h1 className="text-lg font-bold">
          {inspectOnly ? "צפייה בשיחות" : "סקירת שיחות"}
        </h1>

        {inspectOnly && (
          <span className="text-xs px-2 py-1 rounded bg-sky-100 text-sky-800 border border-sky-200">
            מצב צפייה — ללא סיווג
          </span>
        )}

        {meta?.threads_path && (
          <span className="text-xs text-slate-500 truncate max-w-xs" title={meta.threads_path}>
            {meta.threads_path}
          </span>
        )}

        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="text-sm border rounded px-2 py-1"
        >
          {FILTER_OPTIONS.filter(
            (o) =>
              hasClassification || o.value === "all"
          ).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>

        <select
          value={sort}
          onChange={(e) => setSort(e.target.value)}
          className="text-sm border rounded px-2 py-1"
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              מיון: {o.label}
            </option>
          ))}
        </select>

        <select
          value={order}
          onChange={(e) => setOrder(e.target.value)}
          className="text-sm border rounded px-2 py-1"
        >
          <option value="desc">יורד</option>
          <option value="asc">עולה</option>
        </select>

        {bucketFilter && (
          <button
            type="button"
            onClick={() => {
              setBucketFilter(null);
              setActiveBucket(null);
            }}
            className="text-xs px-2 py-1 bg-amber-100 rounded"
          >
            נקה סינון גרף ×
          </button>
        )}

        {toast && (
          <span className="text-sm text-emerald-700 mr-auto">{toast}</span>
        )}
      </header>

      <div ref={layoutRef} className="flex-1 flex overflow-hidden min-h-0">
        <aside
          style={{ width: listWidth }}
          className="shrink-0 bg-slate-50 flex flex-col overflow-hidden min-w-0"
        >
          <RecentChangesPanel
            recent={recent}
            selectedId={selectedId}
            onSelect={handleSelect}
          />
          <StatsPanel
            stats={stats}
            onBucketClick={handleBucketClick}
            activeBucket={activeBucket}
          />
          <ThreadList
            items={listItems}
            selectedId={selectedId}
            onSelect={handleSelect}
            total={listTotal}
            highlightIds={highlightIds}
            splitHighlightIds={splitHighlightIds}
            hasClassification={hasClassification}
          />
        </aside>

        <ResizeHandle
          label="שנה רוחב רשימת השיחות"
          onResize={resizeListPanel}
          onResizeEnd={persistPanelLayout}
        />

        <main className="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden">
          <SplitResultBanner
            splitResult={splitResult}
            selectedId={selectedId}
            onSelect={handleSplitSelect}
            onDismiss={() => setSplitResult(null)}
          />
          <div className="flex items-center gap-2 px-4 py-2 border-b bg-white text-sm shrink-0">
            <span className="text-slate-500">כרונולוגיה:</span>
            <button
              type="button"
              disabled={!neighbors?.prev_id}
              onClick={() => setSelectedId(neighbors.prev_id)}
              className="px-2 py-1 border rounded disabled:opacity-40"
            >
              ← שכנה קודמת
            </button>
            <button
              type="button"
              disabled={!neighbors?.next_id}
              onClick={() => setSelectedId(neighbors.next_id)}
              className="px-2 py-1 border rounded disabled:opacity-40"
            >
              שכנה הבאה →
            </button>
            <span className="text-slate-400 mr-auto">|</span>
            <span className="text-slate-500">תור:</span>
            <button
              type="button"
              disabled={!threadData?.queue?.prev_in_queue}
              onClick={() => setSelectedId(threadData.queue.prev_in_queue)}
              className="px-2 py-1 border rounded disabled:opacity-40"
            >
              ← קודם
            </button>
            <button
              type="button"
              disabled={!threadData?.queue?.next_in_queue}
              onClick={() => setSelectedId(threadData.queue.next_in_queue)}
              className="px-2 py-1 border rounded disabled:opacity-40"
            >
              הבא →
            </button>
          </div>
          <MessageViewer
            thread={threadData?.thread}
            context={threadData?.context}
            selectedIndices={selectedIndices}
            onToggle={handleToggle}
            onRangeSelect={handleRangeSelect}
            onNavigateToThread={handleSelect}
            isSplitPart={isSplitPart}
          />
        </main>

        <ResizeHandle
          label="שנה רוחב פאנל התגיות"
          onResize={resizeSidePanel}
          onResizeEnd={persistPanelLayout}
        />

        {hasClassification ? (
          <aside
            style={{ width: sideWidth }}
            className="shrink-0 bg-slate-50 flex flex-col min-h-0 min-w-0"
          >
            <ClassificationPanel
              classification={threadData?.classification}
              taxonomy={taxonomy}
              saving={saving}
              onSave={(p) => handleSave(p, false)}
              onSaveAndNext={(p) => handleSave(p, true)}
              onMerge={() => setMergeOpen(true)}
              onSplit={() => setSplitOpen(true)}
              onMove={() => setMoveOpen(true)}
            />
          </aside>
        ) : (
          <aside
            style={{ width: sideWidth }}
            className="shrink-0 bg-slate-50 flex flex-col min-h-0 overflow-hidden min-w-0"
          >
            <div className="flex-1 min-h-0 overflow-y-auto p-4 text-sm text-slate-600">
              <h2 className="font-semibold mb-2">פעולות על שיחות</h2>
              <p className="mb-4 text-xs">
                אין קובץ סיווג — ניתן לצפות, לפצל, למזג ולהעביר הודעות.
                גלילה בתצוגת ההודעות מציגה שיחות סמוכות וחלקי פיצול בצבע שונה.
                להוספת תגיות הרץ classify ופתח ללא --inspect.
              </p>
            </div>
            <div className="shrink-0 border-t p-4 space-y-2 bg-white">
              <button
                type="button"
                onClick={() => setMergeOpen(true)}
                className="w-full py-2 text-sm border rounded bg-white hover:bg-slate-100"
              >
                מזג שיחות
              </button>
              <button
                type="button"
                onClick={() => setSplitOpen(true)}
                className="w-full py-2 text-sm border rounded bg-white hover:bg-slate-100"
              >
                פצל שיחה
              </button>
              <button
                type="button"
                onClick={() => setMoveOpen(true)}
                className="w-full py-2 text-sm border rounded bg-white hover:bg-slate-100"
              >
                העבר הודעות
              </button>
            </div>
          </aside>
        )}
      </div>

      <MergeDialog
        open={mergeOpen}
        onClose={() => setMergeOpen(false)}
        currentId={selectedId}
        neighbors={neighbors}
        onConfirm={handleMerge}
      />
      <SplitDialog
        open={splitOpen}
        onClose={() => setSplitOpen(false)}
        thread={threadData?.thread}
        selectedIndices={selectedIndices}
        onConfirm={handleSplit}
      />
      <MoveDialog
        open={moveOpen}
        onClose={() => setMoveOpen(false)}
        sourceId={selectedId}
        selectedIndices={selectedIndices}
        onConfirm={handleMove}
        suggestions={moveSuggestions}
      />
    </div>
  );
}
