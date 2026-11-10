import { useState } from "react";
import {
  createApi,
  ItemList,
  Layout,
  Pager,
  PanelState,
  usePagedSelection,
  useResource,
  useShortcuts,
  useStoredState,
  useToast,
} from "@shell";
import { ClaimDetail } from "./ClaimDetail.jsx";
import { ClaimRow } from "./ClaimRow.jsx";
import { DecisionPanel } from "./DecisionPanel.jsx";
import { DECISION_TOASTS, PAGE_SIZE, toggleMark } from "./review.js";
import { Toolbar } from "./Toolbar.jsx";

// PII review: every claim whose phone numbers or emails step 3 scrubbed (list, paged by the
// server), the selected claim with its marks (main), and the decision (side). A decision is
// saved at once and the queue moves to the next claim the server names; "u" undoes the last
// decision. Everything follows the claims files on disk: a step-3 re-run shows up live.

const ID = "pii";
const api = createApi(ID);
const DIGITS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];

const claimPath = (claimId) => `/claims/${encodeURIComponent(claimId)}`;

function PiiTool({ tool }) {
  const toast = useToast();
  const [filter, setFilter] = useStoredState(ID, "filter", "pending");
  const [sort, setSort] = useStoredState(ID, "sort", "claim_id");
  const [order, setOrder] = useStoredState(ID, "order", "asc");
  const [selectedId, setSelectedId] = useStoredState(ID, "selected", null);
  const [offset, setOffset] = useState(0);
  const [choice, setChoice] = useState({ claimId: null, marks: [] });
  const [lastDecided, setLastDecided] = useState(null);
  const [saving, setSaving] = useState(false);
  const view = { filter, sort, order };

  const summary = useResource(() => api.get("/summary"), [], { watch: tool.reads });
  const list = useResource(
    () => api.get("/claims", { ...view, offset, limit: PAGE_SIZE }),
    [filter, sort, order, offset],
    { watch: tool.reads, keepPrevious: true }
  );
  const detail = useResource(
    () => (selectedId ? api.get(claimPath(selectedId), view) : null),
    [selectedId, filter, sort, order],
    { watch: tool.reads }
  );

  const items = list.data?.items ?? [];
  const claim = detail.data?.claim;
  const queue = detail.data?.queue;
  const chosen = claim && choice.claimId === claim.claim_id ? choice.marks : [];
  // The list shows the page of the selected claim (it may have moved after a decision); a
  // claim that left the view (decided elsewhere, removed by a step re-run, filter changed)
  // gives way to the first listed claim.
  const goToPage = usePagedSelection({
    list,
    getKey: (item) => item.claim_id,
    selectedKey: selectedId,
    onSelect: setSelectedId,
    position: queue?.position,
    lost: detail.error?.status === 404 || queue?.position === null,
    offset,
    setOffset,
    pageSize: PAGE_SIZE,
  });

  const changeView = (setter) => (value) => {
    setter(value);
    setOffset(0);
  };

  const toggle = (index) => {
    if (claim?.status !== "pending" || !claim.marks_match || index >= claim.redactions.length) return;
    setChoice({ claimId: claim.claim_id, marks: toggleMark(chosen, index) });
  };

  const save = async (path, body, onDone) => {
    setSaving(true);
    try {
      const result = await api.post(path, body);
      summary.setData(result.summary);
      await list.reload();
      onDone(result);
    } catch (error) {
      toast.error(error);
      detail.reload();
    } finally {
      setSaving(false);
    }
  };

  const decide = (decision) => {
    if (saving || claim?.status !== "pending") return;
    if (decision === "restore" && !claim.marks_match) return;
    const restore = decision === "restore" && chosen.length > 0 ? chosen : null;
    save(`${claimPath(claim.claim_id)}/decision`, { decision, restore, ...view }, (result) => {
      setLastDecided(claim.claim_id);
      if (result.next_id === claim.claim_id) detail.reload();
      else setSelectedId(result.next_id);
      toast.success(DECISION_TOASTS[result.claim.status]);
    });
  };

  const undoTarget = claim && claim.status !== "pending" ? (claim.can_undo ? claim.claim_id : null) : lastDecided;
  const undo = () => {
    if (saving || !undoTarget) return;
    save(`${claimPath(undoTarget)}/undo`, undefined, () => {
      setLastDecided(null);
      if (undoTarget === selectedId) detail.reload();
      else setSelectedId(undoTarget);
      toast.success("ההחלטה בוטלה, הטענה ממתינה שוב");
    });
  };

  useShortcuts([
    { keys: "a", label: "אישור ההסרה", run: () => decide("accept") },
    { keys: "r", label: "שחזור הפרטים (כולם, או רק המסומנים)", run: () => decide("restore") },
    { keys: DIGITS, label: "סימון פרט לשחזור לפי מספרו", run: (event) => toggle(Number(event.code.slice(-1)) - 1) },
    { keys: "u", label: "ביטול ההחלטה האחרונה", run: undo },
  ]);

  const listPanel = (
    <PanelState
      loading={list.loading}
      error={list.error}
      hasData={Boolean(list.data)}
      onRetry={list.reload}
      empty={list.data?.total === 0}
      emptyText={filter === "pending" ? "אין טענות שממתינות לבדיקה" : "אין טענות בסינון הזה"}
    >
      <ItemList
        items={items}
        getKey={(item) => item.claim_id}
        selectedKey={selectedId}
        onSelect={setSelectedId}
        total={list.data?.total}
        prevKey={queue?.prev_id}
        nextKey={queue?.next_id}
        header={<Pager offset={offset} limit={PAGE_SIZE} total={list.data?.total ?? 0} onChange={goToPage} />}
        renderItem={(item) => <ClaimRow claim={item} />}
      />
    </PanelState>
  );

  return (
    <Layout
      toolId={tool.id}
      toolbar={
        <Toolbar
          view={view}
          onFilter={changeView(setFilter)}
          onSort={changeView(setSort)}
          onOrder={changeView(setOrder)}
          summary={summary.data}
        />
      }
      list={listPanel}
      main={<ClaimDetail detail={detail} selectedId={selectedId} chosen={chosen} onSelect={setSelectedId} />}
      side={
        <DecisionPanel
          detail={detail}
          chosen={chosen}
          saving={saving}
          canUndoPrevious={Boolean(lastDecided)}
          onToggle={toggle}
          onAccept={() => decide("accept")}
          onRestore={() => decide("restore")}
          onUndo={undo}
        />
      }
      listWidth={{ initial: 340 }}
    />
  );
}

export default { id: ID, title: "בדיקת פרטיות בטענות", Component: PiiTool };
