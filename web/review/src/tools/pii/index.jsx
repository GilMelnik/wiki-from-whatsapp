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
import { DECISION_TOASTS, PAGE_SIZE, sectionOf, toggleMark } from "./review.js";

// Privacy: every claim whose phone numbers or e-mail addresses step 3 removed. Decided here:
// per claim, keep the removal or put the details back. The list holds the claims in sections
// (to decide, removal kept, restored); the main column the claim with each removed detail in
// place, whose contact it is and the chat message it came from; the side column the decision.
// A decision is saved at once and the next claim to decide is shown; "u" undoes. Everything
// follows the files on disk: a step-3 re-run, or step 4 finding contacts, shows up live.

const ID = "pii";
const api = createApi(ID);
const DIGITS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];
const CONTEXT_KEYS = ["entities", "export"];

const claimPath = (claimId) => `/claims/${encodeURIComponent(claimId)}`;

function PiiTool({ tool }) {
  const toast = useToast();
  const [selectedId, setSelectedId] = useStoredState(ID, "selected", null);
  const [offset, setOffset] = useState(0);
  const [choice, setChoice] = useState({ claimId: null, marks: [] });
  const [lastDecided, setLastDecided] = useState(null);
  const [saving, setSaving] = useState(false);

  const list = useResource(() => api.get("/claims", { offset, limit: PAGE_SIZE }), [offset], {
    watch: tool.reads,
    keepPrevious: true,
  });
  const detail = useResource(() => (selectedId ? api.get(claimPath(selectedId)) : null), [selectedId], {
    watch: [...tool.reads, ...CONTEXT_KEYS],
  });

  const claim = detail.data?.claim;
  const queue = detail.data?.queue;
  const chosen = claim && choice.claimId === claim.claim_id ? choice.marks : [];
  // The list shows the page of the selected claim; a claim that is gone (a step re-run removed
  // it) gives way to the first listed one.
  const goToPage = usePagedSelection({
    list,
    getKey: (item) => item.claim_id,
    selectedKey: selectedId,
    onSelect: setSelectedId,
    position: queue?.position,
    lost: detail.error?.status === 404,
    offset,
    setOffset,
    pageSize: PAGE_SIZE,
  });

  const toggle = (index) => {
    if (claim?.status !== "pending" || !claim.marks_match || index >= claim.redactions.length) return;
    setChoice({ claimId: claim.claim_id, marks: toggleMark(chosen, index) });
  };

  const save = async (path, body, onDone) => {
    setSaving(true);
    try {
      const result = await api.post(path, body);
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
    save(`${claimPath(claim.claim_id)}/decision`, { decision, restore }, (result) => {
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
      toast.success("Decision undone; the claim is to decide again");
    });
  };

  useShortcuts([
    { keys: "a", label: "Keep the removal", run: () => decide("accept") },
    { keys: "r", label: "Restore the details (all, or only the marked ones)", run: () => decide("restore") },
    { keys: DIGITS, label: "Mark a detail to restore by its number", run: (event) => toggle(Number(event.code.slice(-1)) - 1) },
    { keys: "u", label: "Undo the last decision", run: undo },
  ]);

  const listPanel = (
    <PanelState
      loading={list.loading}
      error={list.error}
      hasData={Boolean(list.data)}
      onRetry={list.reload}
      empty={list.data?.total === 0}
      emptyText="Step 3 removed no phone number or e-mail address from any claim."
    >
      <ItemList
        items={list.data?.items ?? []}
        getKey={(item) => item.claim_id}
        selectedKey={selectedId}
        onSelect={setSelectedId}
        total={list.data?.total}
        prevKey={queue?.prev_id}
        nextKey={queue?.next_id}
        header={<Pager offset={offset} limit={PAGE_SIZE} total={list.data?.total ?? 0} onChange={goToPage} />}
        sectionOf={sectionOf}
        renderItem={(item) => <ClaimRow claim={item} />}
      />
    </PanelState>
  );

  return (
    <Layout
      toolId={tool.id}
      list={listPanel}
      main={<ClaimDetail detail={detail} selectedId={selectedId} chosen={chosen} />}
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

export default { id: ID, title: "Privacy", Component: PiiTool };
