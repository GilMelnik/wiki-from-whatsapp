import { useEffect, useRef, useState } from "react";
import {
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
import { api, groupPath } from "./api.js";
import { GroupActions } from "./GroupActions.jsx";
import { DialogHost } from "./GroupDialogs.jsx";
import { GroupDetail } from "./GroupDetail.jsx";
import { GroupRow } from "./GroupRow.jsx";
import { DEFAULT_VIEW, PAGE_SIZE, pickedIn, SEARCH_DEBOUNCE_MS, togglePick, viewParams } from "./groups.js";
import { GroupsToolbar } from "./GroupsToolbar.jsx";

// The groups view: every group (list, paged and filtered by the server), the selected group
// in full (main) and what can be done with it (side). Every change is saved at once; the
// server answers which group to show next.

const NOTHING_PICKED = { groupId: null, claimIds: [] };

export function GroupsView({ tool, toolId, modeTabs, summary }) {
  const toast = useToast();
  const [view, setView] = useStoredState(toolId, "view", DEFAULT_VIEW);
  const [query, setQuery] = useState(view.q);
  const [selectedId, setSelectedId] = useStoredState(toolId, "selected", null);
  const [offset, setOffset] = useState(0);
  const [picked, setPicked] = useState(NOTHING_PICKED);
  const [dialog, setDialog] = useState(null);
  const [saving, setSaving] = useState(false);
  const searchRef = useRef(null);
  const params = viewParams(view);

  const list = useResource(() => api.get("/groups", { ...params, offset, limit: PAGE_SIZE }), [view, offset], {
    watch: tool.reads,
    keepPrevious: true,
  });
  const detail = useResource(() => (selectedId ? api.get(groupPath(selectedId), params) : null), [selectedId, view], {
    watch: tool.reads,
  });
  const group = detail.data?.group;
  const queue = detail.data?.queue;
  // A group opened from a link may lie outside the filtered list: it stays shown. Only a
  // group that is gone (merged, split away by a re-run) gives way to the first listed one.
  const goToPage = usePagedSelection({
    list,
    getKey: (item) => item.group_id,
    selectedKey: selectedId,
    onSelect: setSelectedId,
    position: queue?.position,
    lost: detail.error?.status === 404,
    offset,
    setOffset,
    pageSize: PAGE_SIZE,
  });

  const changeView = (patch) => {
    setView((previous) => ({ ...previous, ...patch }));
    goToPage(0);
  };
  useEffect(() => {
    if (query === view.q) return undefined;
    const timer = setTimeout(() => changeView({ q: query }), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  /** Save a change; the answer names the group to show next. Throws on failure. */
  const act = async (request, message) => {
    setSaving(true);
    try {
      const result = await request();
      setPicked(NOTHING_PICKED);
      summary.setData(result.summary);
      await list.reload();
      const next = result.group_id ?? result.target_id;
      if (next && next !== selectedId) setSelectedId(next);
      else await detail.reload();
      toast.success(message);
      return result;
    } finally {
      setSaving(false);
    }
  };
  const fire = (request, message) =>
    act(request, message).catch((error) => {
      toast.error(error);
      detail.reload();
    });

  const ready = Boolean(group) && !saving;
  const chosen = group ? pickedIn(picked, group.group_id) : [];
  const path = group ? groupPath(group.group_id) : null;
  const on = {
    open: setSelectedId,
    togglePick: (claimId) => setPicked((previous) => togglePick(previous, group.group_id, claimId)),
    setRepresentative: (claimId) =>
      fire(() => api.post(`${path}/representative`, { claim_id: claimId }), claimId ? "הטקסט המייצג עודכן" : "הטקסט המייצג ייבחר אוטומטית"),
    link: (otherId, linked) =>
      fire(() => api.post(`${path}/contradictions`, { group_id: otherId, linked }), linked ? "קישור הסתירה הוחזר" : "קישור הסתירה הוסר"),
    mergeWith: (otherId) => fire(() => api.post("/groups/merge", { group_ids: [group.group_id, otherId] }), "הקבוצות מוזגו"),
    notSame: (otherId) => fire(() => api.post("/not-same", { group_ids: [group.group_id, otherId] }), "סומן: לא אותו דבר"),
    merge: () => setDialog({ kind: "merge" }),
    split: () => chosen.length > 0 && chosen.length < group.size && setDialog({ kind: "split" }),
    move: () => chosen.length === 1 && setDialog({ kind: "move" }),
    addLink: () => setDialog({ kind: "link" }),
  };

  useShortcuts([
    { keys: "m", label: "מיזוג עם קבוצה אחרת", run: () => ready && on.merge() },
    { keys: "x", label: "פיצול הטענות שנבחרו לקבוצה חדשה", run: () => ready && on.split() },
    { keys: "v", label: "העברת הטענה שנבחרה לקבוצה אחרת", run: () => ready && on.move() },
    { keys: "c", label: "הוספת קישור סתירה", run: () => ready && on.addLink() },
    { keys: "f", label: "חיפוש קבוצה", run: () => searchRef.current?.focus() },
  ]);

  const listPanel = (
    <PanelState
      loading={list.loading}
      error={list.error}
      hasData={Boolean(list.data)}
      onRetry={list.reload}
      empty={list.data?.total === 0}
      emptyText={view.q ? "אין קבוצות שמתאימות לחיפוש" : "אין קבוצות בסינון הזה"}
    >
      <ItemList
        items={list.data?.items ?? []}
        getKey={(item) => item.group_id}
        selectedKey={selectedId}
        onSelect={setSelectedId}
        total={list.data?.total}
        prevKey={queue?.prev_id}
        nextKey={queue?.next_id}
        header={<Pager offset={offset} limit={PAGE_SIZE} total={list.data?.total ?? 0} onChange={goToPage} />}
        renderItem={(item) => <GroupRow group={item} />}
      />
    </PanelState>
  );

  return (
    <>
      <Layout
        toolId={toolId}
        toolbar={
          <GroupsToolbar ref={searchRef} modeTabs={modeTabs} query={query} onQuery={setQuery} view={view} onView={changeView} summary={summary.data} />
        }
        list={listPanel}
        main={<GroupDetail detail={detail} selectedId={selectedId} picked={picked} saving={saving} on={on} />}
        side={<GroupActions detail={detail} picked={picked} saving={saving} on={on} />}
        listWidth={{ initial: 360 }}
      />
      <DialogHost dialog={dialog} group={group} picked={picked} close={() => setDialog(null)} act={act} />
    </>
  );
}
