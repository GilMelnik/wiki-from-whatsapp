import { useEffect, useRef, useState } from "react";
import { countText, ItemList, Layout, Pager, PanelState, useBanner, usePagedSelection, useResource, useShortcuts, useStoredState, useToast } from "@shell";
import { api, entityPath } from "./api.js";
import { DialogHost } from "./Dialogs.jsx";
import { EditPanel } from "./EditPanel.jsx";
import {
  allPicked,
  DEFAULT_VIEW,
  NOTHING_PICKED,
  PAGE_SIZE,
  SEARCH_DEBOUNCE_MS,
  sectionsFor,
  togglePicked,
} from "./entities.js";
import { EntityDetail } from "./EntityDetail.jsx";
import { EntityRow } from "./EntityRow.jsx";
import { Toolbar } from "./Toolbar.jsx";

// Entity review: step 4's canonical entities (list, paged by the server), the selected
// entity with its members and their claims (main), and what can be done with it (side).
// Every change is saved at once; the server answers which entity to show next. Everything
// follows the files on disk: a step-4 re-run underneath shows up without a reload.

const ID = "entities";

const CONTACT_TOASTS = { accept: "Contact attached to the entity", reject: "Contact rejected", restore: "Rejection undone" };

function EntitiesTool({ tool }) {
  const toast = useToast();
  const [view, setView] = useStoredState(ID, "queue", DEFAULT_VIEW);
  const [query, setQuery] = useState(view.q);
  const [selectedId, setSelectedId] = useStoredState(ID, "selected", null);
  const [offset, setOffset] = useState(0);
  const [picked, setPicked] = useState(NOTHING_PICKED);
  const [dialog, setDialog] = useState(null);
  const [saving, setSaving] = useState(false);
  const nameRef = useRef(null);
  const searchRef = useRef(null);

  const summary = useResource(() => api.get("/summary"), [], { watch: tool.reads });
  const list = useResource(
    () => api.get("/entities", { ...view, offset, limit: PAGE_SIZE }),
    [view, offset],
    { watch: tool.reads, keepPrevious: true }
  );
  const detail = useResource(
    () => (selectedId ? api.get(entityPath(selectedId), view) : null),
    [selectedId, view],
    { watch: tool.reads }
  );
  const entity = detail.data?.entity;
  const queue = detail.data?.queue;
  // An entity opened from a link may lie outside the filtered list: it stays shown. Only an
  // entity that is gone (merged, deleted, re-run away) gives way to the first listed one.
  const goToPage = usePagedSelection({
    list,
    getKey: (item) => item.entity_id,
    selectedKey: selectedId,
    onSelect: setSelectedId,
    position: queue?.position,
    lost: detail.error?.status === 404,
    offset,
    setOffset,
    pageSize: PAGE_SIZE,
  });

  const changeView = (patch) => {
    // The review order reads top-down by sections: it has no direction of its own.
    const order = patch.sort === "review" ? { order: "desc" } : {};
    setView((previous) => ({ ...previous, ...patch, ...order }));
    goToPage(0);
  };
  useEffect(() => {
    if (query === view.q) return undefined;
    const timer = setTimeout(() => changeView({ q: query }), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  const fallback = summary.data?.analysis.fallback ?? 0;
  useBanner(
    "entities.analysis",
    fallback > 0
      ? {
          tone: "info",
          text: `New or changed since step 4 last ran: ${countText(fallback, "claim")}. Their mentions are highlighted by whole words only until step 4 runs again.`,
        }
      : null
  );

  /** Save a change; the answer says which entity to show next. Throws on failure. */
  const act = async (request, message) => {
    setSaving(true);
    try {
      const result = await request();
      setPicked(NOTHING_PICKED);
      if (result?.summary) summary.setData(result.summary);
      await list.reload();
      if (result?.entity_id && result.entity_id !== selectedId) setSelectedId(result.entity_id);
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

  const path = entity ? entityPath(entity.entity_id) : null;
  const pickedIds = entity ? allPicked(picked, entity.entity_id) : [];
  const post = (suffix, body, message) => fire(() => api.post(`${path}${suffix}`, body), message);
  const on = {
    open: setSelectedId,
    setStatus: (status) =>
      post("/status", { status, view }, status === "accepted" ? "Entity accepted" : "Entity back to suggested"),
    rename: (name) => post("/rename", { canonical_name: name }, "Name updated"),
    setCanonical: (name) => post("/rename", { canonical_name: name }, "Canonical name updated"),
    saveContacts: (contacts) => post("/contacts", contacts, "Contacts saved"),
    resolveContact: (body) => post("/contact", body, CONTACT_TOASTS[body.action]),
    confirm: (name) => post("/members/confirm", { name }, "Attachment confirmed"),
    exclude: (name, claimIds) =>
      post("/claims/exclude", { name, claim_ids: claimIds }, claimIds.length === 1 ? "Claim marked as unrelated" : "Claims marked as unrelated"),
    include: (name, claimIds) => post("/claims/include", { name, claim_ids: claimIds }, "Claim returned to the name"),
    togglePick: (name, claim) => setPicked((previous) => togglePicked(previous, entity.entity_id, name, claim)),
    moveMember: (name) => setDialog({ kind: "moveMember", name }),
    claims: (mode, name, claimIds) => setDialog({ kind: "claims", mode, name, claimIds }),
    deleteClaim: (claim) => setDialog({ kind: "deleteClaim", claim }),
    showAggregation: (group) => setDialog({ kind: "group", group }),
    merge: () => setDialog({ kind: "merge" }),
    mergeInto: (targetId) => setDialog({ kind: "merge", targetId }),
    reject: () => setDialog({ kind: "reject" }),
    remove: () => setDialog({ kind: "remove" }),
    aggregate: () => {
      if (pickedIds.length >= 2) setDialog({ kind: "createGroup", claimIds: pickedIds, texts: picked.texts });
    },
  };

  const ready = Boolean(entity) && !saving;
  useShortcuts([
    { keys: "a", label: "Accept the entity", run: () => ready && entity.status !== "accepted" && on.setStatus("accepted") },
    { keys: "m", label: "Merge into another entity", run: () => ready && on.merge() },
    { keys: "x", label: "Split into one entity per name", run: () => ready && entity.member_count > 1 && on.reject() },
    { keys: "g", label: "Combine the picked claims", run: () => ready && on.aggregate() },
    { keys: "delete", label: "Delete the entity", run: () => ready && on.remove() },
    { keys: "e", label: "Edit the canonical name", run: () => nameRef.current?.focus() },
    { keys: "f", label: "Search entities", run: () => searchRef.current?.focus() },
  ]);

  const listPanel = (
    <PanelState
      loading={list.loading}
      error={list.error}
      hasData={Boolean(list.data)}
      onRetry={list.reload}
      empty={list.data?.total === 0}
      emptyText={view.q ? "No entities match the search" : "No entities match this filter"}
    >
      <ItemList
        items={list.data?.items ?? []}
        getKey={(item) => item.entity_id}
        selectedKey={selectedId}
        onSelect={setSelectedId}
        total={list.data?.total}
        prevKey={queue?.prev_id}
        nextKey={queue?.next_id}
        header={<Pager offset={offset} limit={PAGE_SIZE} total={list.data?.total ?? 0} onChange={goToPage} />}
        sectionOf={sectionsFor(view)}
        renderItem={(item) => <EntityRow entity={item} />}
      />
    </PanelState>
  );

  return (
    <>
      <Layout
        toolId={tool.id}
        toolbar={
          <Toolbar
            ref={searchRef}
            query={query}
            onQuery={setQuery}
            view={view}
            onView={changeView}
            deletedNames={summary.data?.deleted_names ?? 0}
            onDeletedNames={() => setDialog({ kind: "deletedNames" })}
          />
        }
        list={listPanel}
        main={<EntityDetail detail={detail} selectedId={selectedId} picked={picked} saving={saving} reads={tool.reads} on={on} />}
        side={<EditPanel detail={detail} saving={saving} nameRef={nameRef} pickedCount={pickedIds.length} on={on} />}
        listWidth={{ initial: 320 }}
      />
      <DialogHost
        dialog={dialog}
        entity={entity}
        view={view}
        saving={saving}
        reads={tool.reads}
        close={() => setDialog(null)}
        act={act}
        fire={fire}
      />
    </>
  );
}

export default { id: ID, title: "Entities", Component: EntitiesTool };
