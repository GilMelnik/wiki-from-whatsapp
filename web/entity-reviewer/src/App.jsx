import { useCallback, useEffect, useRef, useState } from "react";
import {
  copyClaims,
  createAggregation,
  decoupleAggregationClaim,
  deleteClaim,
  deleteEntity,
  excludeClaims,
  fetchEntities,
  fetchEntity,
  fetchMemberClaims,
  fetchMeta,
  fetchStats,
  mergeEntity,
  moveClaims,
  moveMember,
  renameEntity,
  resolveUncertainContact,
  setAggregationRepresentative,
  setCanonical,
  setContacts,
  setStatus,
} from "./api";
import EntityList from "./components/EntityList";
import EntityDetailPanel from "./components/EntityDetailPanel";
import EntityEditPanel from "./components/EntityEditPanel";
import MoveDialog from "./components/MoveDialog";
import ResizeHandle from "./components/ResizeHandle";
import {
  clampListWidth,
  clampSideWidth,
  loadPanelLayout,
  savePanelLayout,
} from "./panelLayout";

export default function App() {
  const [meta, setMeta] = useState(null);
  const [stats, setStats] = useState(null);
  const [status, setStatusFilter] = useState("");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [sort, setSort] = useState("count");
  const [order, setOrder] = useState("desc");

  const [listItems, setListItems] = useState([]);
  const [listTotal, setListTotal] = useState(0);
  const [selectedId, setSelectedId] = useState(null);
  const [entityData, setEntityData] = useState(null);
  const [selectedClaims, setSelectedClaims] = useState(new Set());
  const [dialog, setDialog] = useState(null);
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

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 250);
    return () => clearTimeout(t);
  }, [query]);

  const listParams = useCallback(
    () => ({ status, q: debouncedQuery, sort, order, limit: "300" }),
    [status, debouncedQuery, sort, order]
  );

  const refreshMeta = useCallback(async () => {
    const [m, s] = await Promise.all([fetchMeta(), fetchStats()]);
    setMeta(m);
    setStats(s);
  }, []);

  const refreshList = useCallback(async () => {
    const data = await fetchEntities(listParams());
    setListItems(data.items);
    setListTotal(data.total);
    return data;
  }, [listParams]);

  const loadEntity = useCallback(
    async (entityId) => {
      if (!entityId) {
        setEntityData(null);
        return;
      }
      try {
        const data = await fetchEntity(entityId, listParams());
        setEntityData(data);
        setSelectedClaims(new Set());
      } catch (err) {
        setEntityData(null);
        throw err;
      }
    },
    [listParams]
  );

  useEffect(() => {
    refreshMeta().catch((err) => setToast(err.message));
  }, [refreshMeta]);

  useEffect(() => {
    refreshList()
      .then((data) => {
        setSelectedId((current) => {
          if (current && data.items.some((i) => i.entity_id === current)) return current;
          return data.items[0]?.entity_id || null;
        });
      })
      .catch((err) => setToast(err.message));
  }, [refreshList]);

  useEffect(() => {
    if (selectedId) loadEntity(selectedId).catch((err) => setToast(err.message));
    else setEntityData(null);
  }, [selectedId, loadEntity]);

  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(""), 3000);
    return () => clearTimeout(t);
  }, [toast]);

  const afterMutation = async (preferredId) => {
    await Promise.all([refreshMeta(), refreshList()]);
    const data = await fetchEntities(listParams());
    const exists = (id) => id && data.items.some((i) => i.entity_id === id);
    const nextId = exists(preferredId)
      ? preferredId
      : exists(selectedId)
        ? selectedId
        : data.items[0]?.entity_id || null;
    setSelectedId(nextId);
    if (nextId) await loadEntity(nextId);
    else setEntityData(null);
  };

  const guard = async (fn, message, preferredId) => {
    setSaving(true);
    try {
      const result = await fn();
      setDialog(null);
      await afterMutation(preferredId ?? result?.entity?.entity_id);
      setToast(message);
    } catch (err) {
      setToast(err.message);
    } finally {
      setSaving(false);
    }
  };

  const toggleClaim = (name, claimId) => {
    const key = `${name}\u0001${claimId}`;
    setSelectedClaims((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const entity = entityData?.entity;

  const handleConfirmDialog = async (targetId) => {
    if (!dialog || !selectedId) return;
    if (dialog.kind === "member") {
      await guard(
        () => moveMember(selectedId, dialog.name, targetId),
        "השם הועבר",
        selectedId
      );
    } else if (dialog.kind === "claims") {
      await guard(
        () => moveClaims(selectedId, dialog.name, dialog.claimIds, targetId),
        "הטענות הועברו",
        selectedId
      );
    } else if (dialog.kind === "copy") {
      await guard(
        () => copyClaims(selectedId, dialog.name, dialog.claimIds, targetId),
        "הטענות הועתקו",
        targetId ?? selectedId
      );
    } else if (dialog.kind === "merge") {
      await guard(() => mergeEntity(selectedId, targetId), "הישות מוזגה", targetId);
    }
  };

  const handleDelete = async () => {
    if (!selectedId) return;
    setSaving(true);
    try {
      const result = await deleteEntity(selectedId);
      setDialog(null);
      await Promise.all([refreshMeta(), refreshList()]);
      const nextId = result.next_id || null;
      setSelectedId(nextId);
      if (nextId) await loadEntity(nextId);
      else setEntityData(null);
      setToast("הישות נמחקה");
    } catch (err) {
      setToast(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-screen flex flex-col">
      <header className="shrink-0 border-b border-slate-200 bg-white px-4 py-2 flex items-baseline justify-between">
        <div>
          <h1 className="text-lg font-semibold">איחוד ישויות</h1>
          <p className="text-xs text-slate-500">{meta?.entities_path}</p>
        </div>
        {stats && (
          <p className="text-xs text-slate-500">
            {stats.entity_count} ישויות · {stats.multi_member_count} מאוחדות ·{" "}
            {(stats.by_status?.accepted || 0)} אושרו · {(stats.by_status?.rejected || 0)} נדחו
          </p>
        )}
      </header>

      <div ref={layoutRef} className="flex-1 flex min-h-0">
        <aside
          className="shrink-0 flex flex-col border-l border-slate-200 bg-white"
          style={{ width: listWidth }}
        >
          <EntityList
            items={listItems}
            total={listTotal}
            selectedId={selectedId}
            onSelect={setSelectedId}
            status={status}
            onStatusChange={setStatusFilter}
            query={query}
            onQueryChange={setQuery}
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
          <EntityDetailPanel
            entity={entity}
            queue={entityData?.queue}
            selectedClaims={selectedClaims}
            onToggleClaim={toggleClaim}
            onSetCanonical={(name) =>
              guard(() => setCanonical(selectedId, name), "השם הקנוני עודכן", selectedId)
            }
            onMoveMember={(name) => setDialog({ kind: "member", name })}
            onMoveClaims={(name, claimIds) =>
              setDialog({ kind: "claims", name, claimIds })
            }
            onCopyClaims={(name, claimIds) =>
              setDialog({ kind: "copy", name, claimIds })
            }
            onExcludeClaim={(name, claimId) =>
              guard(
                () => excludeClaims(selectedId, name, [claimId]),
                "הטענה הוסרה מהישות",
                selectedId
              )
            }
            onExcludeClaims={(name, claimIds) =>
              guard(
                () => excludeClaims(selectedId, name, claimIds),
                claimIds.length === 1 ? "הטענה הוסרה מהישות" : "הטענות הוסרו מהישות",
                selectedId
              )
            }
            onDeleteClaim={(claimId) => {
              if (
                !window.confirm(
                  "למחוק את הטענה לחלוטין? היא תוסר מכל הישויות ולא תשמש בהמשך הצנרת."
                )
              )
                return;
              guard(() => deleteClaim(claimId), "הטענה נמחקה", selectedId);
            }}
            onOpenEntity={setSelectedId}
            onMerge={() => setDialog({ kind: "merge" })}
            onSetStatus={(value) =>
              guard(() => setStatus(selectedId, value), "הסטטוס עודכן", selectedId)
            }
            onFetchMemberClaims={(name, offset, limit) =>
              fetchMemberClaims(selectedId, name, offset, limit)
            }
            onCreateAggregation={(claimIds, representative) =>
              guard(
                () => createAggregation(claimIds, representative),
                "הטענות אוחדו",
                selectedId
              )
            }
            onSetRepresentative={(groupId, claimId) =>
              guard(
                () => setAggregationRepresentative(groupId, claimId),
                "הנציג עודכן",
                selectedId
              )
            }
            onDecoupleClaim={(groupId, claimId) =>
              guard(
                () => decoupleAggregationClaim(groupId, claimId),
                "הטענה נותקה מהאיחוד",
                selectedId
              )
            }
            onPrev={setSelectedId}
            onNext={setSelectedId}
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
            <h3 className="text-sm font-semibold">עריכה</h3>
            <p className="text-xs text-slate-500 mt-1">
              שינויים נשמרים ל־<code className="text-[11px]">entities_edited.json</code>
            </p>
          </div>
          <EntityEditPanel
            entity={entity}
            saving={saving}
            onRename={(canonicalName) =>
              guard(
                () => renameEntity(selectedId, canonicalName),
                "שם הישות עודכן",
                selectedId
              )
            }
            onSaveContacts={(payload) =>
              guard(
                () => setContacts(selectedId, payload),
                "פרטי הקשר עודכנו",
                selectedId
              )
            }
            onResolveUncertainContact={(payload) =>
              guard(
                () => resolveUncertainContact(selectedId, payload),
                payload.action === "accept" ? "הערך שויך לישות" : "הערך נדחה",
                selectedId
              )
            }
            onDelete={handleDelete}
          />
        </aside>
      </div>

      <MoveDialog
        open={Boolean(dialog)}
        title={
          dialog?.kind === "merge"
            ? "מזג ישות זו לתוך ישות אחרת"
            : dialog?.kind === "copy"
              ? `העתק ${dialog?.claimIds?.length || 0} טענות של "${dialog?.name}"`
              : dialog?.kind === "claims"
                ? `הזז ${dialog?.claimIds?.length || 0} טענות של "${dialog?.name}"`
                : `הזז את השם "${dialog?.name}"`
        }
        subtitle={
          dialog?.kind === "merge"
            ? "כל השמות של הישות הנוכחית יעברו לישות היעד"
            : dialog?.kind === "copy"
              ? "הטענות יישארו גם בישות הנוכחית"
              : "בחר ישות יעד קיימת או צור חדשה"
        }
        allowNew={dialog?.kind !== "merge"}
        currentEntityId={selectedId}
        onClose={() => setDialog(null)}
        onConfirm={handleConfirmDialog}
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
