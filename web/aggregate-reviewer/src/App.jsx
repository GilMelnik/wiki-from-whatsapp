import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchGroup,
  fetchGroups,
  fetchMeta,
  fetchStats,
  fetchTopics,
  moveMember,
  setRepresentative,
  splitCluster,
} from "./api";
import GroupDetailPanel from "./components/GroupDetailPanel";
import GroupList from "./components/GroupList";
import MoveMemberDialog from "./components/MoveMemberDialog";
import ResizeHandle from "./components/ResizeHandle";
import StatsPanel from "./components/StatsPanel";
import TopicSelect from "./components/TopicSelect";
import {
  clampListWidth,
  clampSideWidth,
  loadPanelLayout,
  savePanelLayout,
} from "./panelLayout";

function listParams(state) {
  const params = {
    sort: state.sort,
    order: state.order,
    limit: "200",
    offset: "0",
  };
  if (state.sizeFilter) {
    const size = state.sizeFilter.size;
    params.size_min = String(size);
    params.size_max = String(size);
  }
  return params;
}

export default function App() {
  const [meta, setMeta] = useState(null);
  const [stats, setStats] = useState(null);
  const [topics, setTopics] = useState([]);
  const [topicId, setTopicId] = useState("");
  const [sort, setSort] = useState("support");
  const [order, setOrder] = useState("desc");
  const [sizeFilter, setSizeFilter] = useState(null);

  const [listItems, setListItems] = useState([]);
  const [listTotal, setListTotal] = useState(0);
  const [selectedKey, setSelectedKey] = useState(null);
  const [groupData, setGroupData] = useState(null);
  const [selectedMembers, setSelectedMembers] = useState(new Set());
  const [moveMemberState, setMoveMemberState] = useState(null);
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
    const [m, s, t] = await Promise.all([fetchMeta(), fetchStats(), fetchTopics()]);
    setMeta(m);
    setStats(s);
    setTopics(t.items || []);
    setTopicId((current) => {
      if (current && t.items.some((item) => item.id === current)) return current;
      return t.items[0]?.id || "";
    });
  }, []);

  const refreshList = useCallback(async () => {
    if (!topicId) {
      setListItems([]);
      setListTotal(0);
      return { items: [], total: 0 };
    }
    const params = listParams({ sort, order, sizeFilter });
    const data = await fetchGroups(topicId, params);
    setListItems(data.items);
    setListTotal(data.total);
    return data;
  }, [topicId, sort, order, sizeFilter]);

  const loadGroup = useCallback(
    async (groupKey) => {
      if (!topicId || !groupKey) {
        setGroupData(null);
        return;
      }
      const params = listParams({ sort, order, sizeFilter });
      const data = await fetchGroup(topicId, groupKey, params);
      setGroupData(data);
      setSelectedMembers(new Set());
    },
    [topicId, sort, order, sizeFilter]
  );

  useEffect(() => {
    refreshMeta().catch((err) => setToast(err.message));
  }, [refreshMeta]);

  useEffect(() => {
    refreshList()
      .then((data) => {
        if (data.items.length === 0) {
          setSelectedKey(null);
          setGroupData(null);
          return;
        }
        setSelectedKey((current) => {
          if (current && data.items.some((i) => i.key === current)) return current;
          return data.items[0].key;
        });
      })
      .catch((err) => setToast(err.message));
  }, [refreshList]);

  useEffect(() => {
    if (selectedKey) {
      loadGroup(selectedKey).catch((err) => setToast(err.message));
    }
  }, [selectedKey, loadGroup]);

  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(""), 3000);
    return () => clearTimeout(t);
  }, [toast]);

  const afterMutation = async (nextKey) => {
    await Promise.all([refreshMeta(), refreshList()]);
    const key = nextKey || selectedKey;
    if (key) {
      setSelectedKey(key);
      await loadGroup(key);
    }
  };

  const handleSetRepresentative = async (sourceClaimId) => {
    if (!selectedKey) return;
    setSaving(true);
    try {
      await setRepresentative(topicId, selectedKey, sourceClaimId);
      await afterMutation(selectedKey);
      setToast("הטענה המייצגת עודכנה");
    } catch (err) {
      setToast(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleMoveConfirm = async (targetGroupKey) => {
    if (!moveMemberState || !selectedKey) return;
    setSaving(true);
    try {
      const result = await moveMember(
        topicId,
        selectedKey,
        moveMemberState.source_claim_id,
        targetGroupKey
      );
      setMoveMemberState(null);
      await afterMutation(result.group.key);
      setToast("הטענה הועברה");
    } catch (err) {
      setToast(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleSplit = async () => {
    if (!selectedKey || selectedMembers.size === 0) return;
    setSaving(true);
    try {
      const result = await splitCluster(topicId, selectedKey, [...selectedMembers]);
      await afterMutation(result.new.key);
      setToast("הקבוצה פוצלה");
    } catch (err) {
      setToast(err.message);
    } finally {
      setSaving(false);
    }
  };

  const toggleMember = (id) => {
    setSelectedMembers((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const group = groupData?.group;

  return (
    <div className="h-screen flex flex-col">
      <header className="shrink-0 border-b border-slate-200 bg-white px-4 py-2">
        <h1 className="text-lg font-semibold">בדיקת קיבוץ טענות</h1>
        <p className="text-xs text-slate-500">
          {meta?.aggregated_path} · {meta?.group_count ?? 0} קבוצות
        </p>
      </header>

      <StatsPanel
        stats={stats}
        activeSizeFilter={sizeFilter}
        onSizeClick={(bucket) =>
          setSizeFilter((current) =>
            current?.size === bucket?.size ? null : bucket
          )
        }
      />

      <div ref={layoutRef} className="flex-1 flex min-h-0">
        <aside
          className="shrink-0 flex flex-col border-l border-slate-200 bg-white"
          style={{ width: listWidth }}
        >
          <TopicSelect topics={topics} value={topicId} onChange={setTopicId} />
          <GroupList
            items={listItems}
            total={listTotal}
            selectedKey={selectedKey}
            onSelect={setSelectedKey}
            sort={sort}
            order={order}
            onSortChange={setSort}
            onOrderChange={setOrder}
          />
        </aside>

        <ResizeHandle
          label="שינוי רוחב רשימה"
          onResize={resizeListPanel}
          onResizeEnd={persistPanelLayout}
        />

        <main className="flex-1 flex flex-col min-w-0 bg-slate-50">
          <GroupDetailPanel
            group={group}
            queue={groupData?.queue}
            selectedMembers={selectedMembers}
            onToggleMember={toggleMember}
            onSetRepresentative={handleSetRepresentative}
            onMoveMember={setMoveMemberState}
            onSplit={handleSplit}
            onPrev={setSelectedKey}
            onNext={setSelectedKey}
            saving={saving}
          />
        </main>

        <ResizeHandle
          label="שינוי רוחב פאנל מידע"
          onResize={resizeSidePanel}
          onResizeEnd={persistPanelLayout}
        />

        <aside
          className="shrink-0 flex flex-col border-r border-slate-200 bg-slate-50 overflow-y-auto"
          style={{ width: sideWidth }}
        >
          <div className="p-4 border-b border-slate-200 bg-white">
            <h3 className="text-sm font-semibold">מידע</h3>
            <p className="text-xs text-slate-500 mt-1">
              שינויים נשמרים ל־
              <code className="text-[11px]">claims_aggregated_edited.json</code>
            </p>
          </div>
          {group && (
            <div className="p-4 text-xs text-slate-600 space-y-2">
              <div>
                <span className="text-slate-400">מפתח: </span>
                <code>{group.key}</code>
              </div>
              {group.entities?.length > 0 && (
                <div>ישויות: {group.entities.join(", ")}</div>
              )}
              {group.date_range && (
                <div>
                  תאריכים: {group.date_range[0] || "—"} – {group.date_range[1] || "—"}
                </div>
              )}
              <div className="pt-2 border-t border-slate-200 text-slate-500">
                <p>סמן טענות ולחץ &quot;פיצול&quot; ליצירת קבוצה חדשה.</p>
                <p className="mt-2">העבר טענה בודדת לקבוצה אחרת באותו נושא.</p>
              </div>
            </div>
          )}
        </aside>
      </div>

      <MoveMemberDialog
        open={Boolean(moveMemberState)}
        member={moveMemberState}
        groups={listItems}
        currentGroupKey={selectedKey}
        onClose={() => setMoveMemberState(null)}
        onConfirm={handleMoveConfirm}
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
