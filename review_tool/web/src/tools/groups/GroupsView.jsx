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
import { DEFAULT_VIEW, PAGE_SIZE, pickedIn, SEARCH_DEBOUNCE_MS, togglePick, viewParams, WITHHELD_MODE } from "./groups.js";
import { GroupsToolbar } from "./GroupsToolbar.jsx";

// A groups list: every group, or (mode "withheld") the groups the owner's rule keeps off the
// wiki (a negative statement that names an entity, from fewer than two members), computed
// live by the server; the list is paged and filtered by the server, the selected group in
// full (main) and what can be done with it (side). Every change is saved at once; the server
// answers which group to show next. Each mode keeps its own filters and selection.

const NOTHING_PICKED = { groupId: null, claimIds: [] };

export function GroupsView({ watch, toolId, mode, modeTabs, summary }) {
  const toast = useToast();
  const withheldList = mode === WITHHELD_MODE;
  const [stored, setView] = useStoredState(toolId, `${mode}.view`, DEFAULT_VIEW);
  const view = withheldList ? { ...stored, withheld: true } : stored;
  const [query, setQuery] = useState(view.q);
  const [selectedId, setSelectedId] = useStoredState(toolId, `${mode}.selected`, null);
  const [offset, setOffset] = useState(0);
  const [picked, setPicked] = useState(NOTHING_PICKED);
  const [dialog, setDialog] = useState(null);
  const [saving, setSaving] = useState(false);
  const searchRef = useRef(null);
  const params = viewParams(view);

  const list = useResource(() => api.get("/groups", { ...params, offset, limit: PAGE_SIZE }), [stored, offset], {
    watch,
    keepPrevious: true,
  });
  const detail = useResource(() => (selectedId ? api.get(groupPath(selectedId), params) : null), [selectedId, stored], {
    watch,
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
      fire(() => api.post(`${path}/representative`, { claim_id: claimId }), claimId ? "Representative text updated" : "The representative text will be chosen automatically"),
    link: (otherId, linked) =>
      fire(() => api.post(`${path}/contradictions`, { group_id: otherId, linked }), linked ? "Contradiction link restored" : "Contradiction link removed"),
    mergeWith: (pair) => {
      const other = pair.a.group_id === group.group_id ? pair.b : pair.a;
      if (pair.dispute) setDialog({ kind: "merge", choice: other.group_id });
      else fire(() => api.post("/groups/merge", { group_ids: [group.group_id, other.group_id] }), "Groups merged");
    },
    notSame: (otherId) => fire(() => api.post("/not-same", { group_ids: [group.group_id, otherId] }), "Marked: not the same"),
    merge: () => setDialog({ kind: "merge" }),
    split: () => chosen.length > 0 && chosen.length < group.size && setDialog({ kind: "split" }),
    move: () => chosen.length === 1 && setDialog({ kind: "move" }),
    addLink: () => setDialog({ kind: "link" }),
  };

  useShortcuts([
    { keys: "m", label: "Merge with another group", run: () => ready && on.merge() },
    { keys: "x", label: "Split the picked claims into a new group", run: () => ready && on.split() },
    { keys: "v", label: "Move the picked claim to another group", run: () => ready && on.move() },
    { keys: "c", label: "Add a contradiction link", run: () => ready && on.addLink() },
    { keys: "f", label: "Search for a group", run: () => searchRef.current?.focus() },
  ]);

  const listPanel = (
    <PanelState
      loading={list.loading}
      error={list.error}
      hasData={Boolean(list.data)}
      onRetry={list.reload}
      empty={list.data?.total === 0}
      emptyText={
        view.q
          ? "No groups match the search"
          : withheldList
            ? "No group is withheld: every negative statement that names an entity was reported by enough members"
            : "No groups in this filter"
      }
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
        renderItem={(item) => <GroupRow group={item} withheldList={withheldList} />}
      />
    </PanelState>
  );

  return (
    <>
      <Layout
        toolId={toolId}
        toolbar={
          <GroupsToolbar
            ref={searchRef}
            modeTabs={modeTabs}
            query={query}
            onQuery={setQuery}
            view={view}
            onView={changeView}
            summary={summary.data}
            withheldList={withheldList}
          />
        }
        list={listPanel}
        main={<GroupDetail detail={detail} selectedId={selectedId} picked={picked} saving={saving} minSupporters={summary.data?.withhold_min_supporters} on={on} />}
        side={<GroupActions detail={detail} picked={picked} saving={saving} on={on} />}
        listWidth={{ initial: 360 }}
      />
      <DialogHost dialog={dialog} group={group} picked={picked} minSupporters={summary.data?.withhold_min_supporters} watch={watch} close={() => setDialog(null)} act={act} />
    </>
  );
}
