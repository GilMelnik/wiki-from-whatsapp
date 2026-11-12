import { useEffect, useRef, useState } from "react";
import { countText, Dialog, formatCount, ItemList, Layout, Pager, PanelState, useResource, useShortcuts, useStoredState, useToast } from "@shell";
import { api } from "./api.js";
import { DecisionPanel } from "./DecisionPanel.jsx";
import { followSuggestion, SUGGESTION_PAGE_SIZE } from "./groups.js";
import { DisputeWarning } from "./MergeNotes.jsx";
import { PairView } from "./PairView.jsx";
import { SuggestionRow } from "./SuggestionRow.jsx";

// The merge-suggestion queue: pairs of groups close to (but below) step 5's own bar, best
// first. One key per decision: m merges, n remembers "not the same", s skips; after a
// decision the next pair comes up in place. A pair whose groups dispute each other asks
// before merging. The list follows the files on disk.

export function SuggestionsView({ toolId, modeTabs, summary, watch, onOpenGroup, runStep }) {
  const toast = useToast();
  const [offset, setOffset] = useState(0);
  const [selectedKey, setSelectedKey] = useStoredState(toolId, "pair", null);
  const [lastNotSame, setLastNotSame] = useState(null);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(null);
  const lastIndex = useRef(0);

  const list = useResource(() => api.get("/suggestions", { offset, limit: SUGGESTION_PAGE_SIZE }), [offset], {
    watch,
    keepPrevious: true,
  });
  const items = list.data?.items ?? [];
  const index = items.findIndex((item) => item.key === selectedKey);
  if (index !== -1) lastIndex.current = index;
  const pair = index === -1 ? null : items[index];

  // After a decision (or a change on disk) the pair may be gone: show the one now in its place.
  useEffect(() => {
    if (list.loading || !list.data) return;
    const next = followSuggestion(list.data.items, selectedKey, lastIndex.current);
    if (next !== selectedKey) setSelectedKey(next);
  }, [list.data, list.loading, selectedKey]);

  const goToPage = (nextOffset) => {
    lastIndex.current = 0;
    setSelectedKey(null);
    setOffset(nextOffset);
  };

  const decide = async (request, message, onDone) => {
    if (!pair || saving) return;
    setSaving(true);
    try {
      const result = await request();
      summary.setData(result.summary);
      onDone?.(result);
      await list.reload();
      toast.success(message);
    } catch (error) {
      toast.error(error);
      list.reload();
    } finally {
      setSaving(false);
    }
  };
  const groupIds = pair ? [pair.a.group_id, pair.b.group_id] : [];
  const mergeNow = () => decide(() => api.post("/groups/merge", { group_ids: groupIds }), "Groups merged");
  const merge = () => {
    if (!pair || saving) return;
    if (pair.dispute) setConfirming(pair);
    else mergeNow();
  };
  const notSame = () =>
    decide(() => api.post("/not-same", { group_ids: groupIds }), "Marked: not the same", (result) =>
      setLastNotSame({ id: result.record.id, key: pair.key })
    );
  const skip = () => {
    if (!pair) return;
    if (index + 1 < items.length) setSelectedKey(items[index + 1].key);
    else if (offset + SUGGESTION_PAGE_SIZE < (list.data?.total ?? 0)) goToPage(offset + SUGGESTION_PAGE_SIZE);
    else toast.info("This is the last suggestion");
  };
  const undo = async () => {
    if (!lastNotSame || saving) return;
    setSaving(true);
    try {
      const result = await api.del(`/not-same/${lastNotSame.id}`);
      summary.setData(result.summary);
      // Select the pair only once the reloaded list holds it again (else the list effect
      // would replace the selection with the pair in its old place).
      await list.reload();
      setSelectedKey(lastNotSame.key);
      setLastNotSame(null);
      toast.success("Decision undone; the pair will be suggested again");
    } catch (error) {
      toast.error(error);
    } finally {
      setSaving(false);
    }
  };

  useShortcuts([
    { keys: "m", label: "Merge the pair (the same)", run: merge },
    { keys: "n", label: "Not the same (do not suggest again)", run: notSame },
    { keys: "s", label: "Skip to the next suggestion", run: skip },
    { keys: "u", label: "Undo the last “not the same”", run: undo },
  ]);

  const unavailable = list.data && !list.data.available;
  const listPanel = (
    <PanelState
      loading={list.loading}
      error={list.error}
      hasData={Boolean(list.data)}
      onRetry={list.reload}
      empty={list.data?.total === 0}
      emptyText={
        unavailable ? (
          <span className="space-y-3">
            <span className="block">
              No vector yet for {countText(list.data.missing_embeddings, "claim", formatCount)}, so there are no
              suggestions. Step 5 will compute them.
            </span>
            {runStep && (
              <button type="button" className="btn btn-sm" onClick={runStep}>
                Run step 5
              </button>
            )}
          </span>
        ) : (
          "No merge suggestions: every close pair is already decided"
        )
      }
    >
      <ItemList
        items={items}
        getKey={(item) => item.key}
        selectedKey={selectedKey}
        onSelect={setSelectedKey}
        total={list.data?.total}
        header={<Pager offset={offset} limit={SUGGESTION_PAGE_SIZE} total={list.data?.total ?? 0} onChange={goToPage} />}
        renderItem={(item) => <SuggestionRow pair={item} />}
      />
    </PanelState>
  );

  const layout = (
    <Layout
      toolId={toolId}
      toolbar={modeTabs}
      list={listPanel}
      main={
        <PairView
          list={list}
          pair={pair}
          position={offset + Math.max(index, 0)}
          minSupporters={summary.data?.withhold_min_supporters}
          watch={watch}
          onOpen={onOpenGroup}
        />
      }
      side={
        <DecisionPanel
          pair={pair}
          saving={saving}
          canUndo={Boolean(lastNotSame)}
          onMerge={merge}
          onNotSame={notSame}
          onSkip={skip}
          onUndo={undo}
        />
      }
      listWidth={{ initial: 380 }}
    />
  );
  return (
    <>
      {layout}
      {confirming && (
        <Dialog
          open
          tone="danger"
          title="Merge two groups that dispute each other?"
          confirmLabel="Merge anyway"
          onClose={() => setConfirming(null)}
          onConfirm={() => (confirming.key === pair?.key ? mergeNow() : null)}
        >
          <DisputeWarning disputes={[confirming.dispute]} />
        </Dialog>
      )}
    </>
  );
}
