import { useEffect, useRef, useState } from "react";
import { Bidi, countText, createApi, formatCount, formatDateTime, ItemList, Kbd, Layout, PanelState, useBanner, useResource, useShortcuts, useStoredState, useToast } from "@shell";
import { ClaimDetail, EntityGroups } from "./Details.jsx";
import { countsText, keepSelection, keepToast, listsWarning, RELEASED_VIEW, TOOL_ID, VETOES_KEY, VIEWS, WHY_LABELS, WITHHELD_VIEW } from "./withheld.js";

// Withheld: the wiki publishes a negative claim about a named provider, place or public body
// only when at least two different members back it. The same-complaint check releases a lone
// one when a model found a second person making the same complaint. Here the owner looks at
// what the check released before the pages are written and keeps any of it withheld (a switch
// per claim; switching it off is the undo), and searches what stays withheld and why. The
// interface is English, the claims and names are Hebrew; no person is ever shown.

const api = createApi(TOOL_ID);
const entityKey = (entity) => entity.entity_id;
const claimKey = (claim) => claim.short_id;

function ViewTabs({ view, onView }) {
  return (
    <div role="tablist" className="flex gap-1">
      {VIEWS.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={view === option.value}
          className={`btn btn-sm ${view === option.value ? "btn-primary" : ""}`}
          onClick={() => onView(option.value)}
        >
          {option.label}
          <Kbd>{option.key}</Kbd>
        </button>
      ))}
    </div>
  );
}

function EntityRow({ entity }) {
  return (
    <span className="flex flex-col gap-0.5">
      <Bidi className="font-medium text-slate-900">{entity.entity_name}</Bidi>
      <span className="text-xs text-slate-500">
        {formatCount(entity.released)} released · {countText(entity.groups.length, "group")}
      </span>
    </span>
  );
}

function ClaimRow({ claim }) {
  return (
    <span className="flex flex-col gap-0.5">
      <Bidi as="span" className="line-clamp-2 text-slate-800">
        {claim.text}
      </Bidi>
      <span className="text-xs text-slate-500">
        <Bidi>{claim.entity_names.join(", ")}</Bidi> · {WHY_LABELS[claim.why] ?? claim.why}
      </span>
    </span>
  );
}

function WithheldTool({ tool }) {
  const toast = useToast();
  const searchRef = useRef(null);
  const [view, setView] = useStoredState(TOOL_ID, "view", RELEASED_VIEW);
  const [entityId, setEntityId] = useStoredState(TOOL_ID, "entity", null);
  const [claimId, setClaimId] = useStoredState(TOOL_ID, "claim", null);
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const watch = [...tool.reads, VETOES_KEY];

  const summary = useResource(() => api.get("/summary"), [], { watch });
  const released = useResource(() => (view === RELEASED_VIEW ? api.get("/released") : null), [view], { watch });
  const withheld = useResource(() => (view === WITHHELD_VIEW ? api.get("/withheld", { q: query }) : null), [view, query], {
    watch,
    keepPrevious: true,
  });

  const entities = released.data?.entities ?? [];
  const claims = withheld.data?.items ?? [];
  const entity = entities.find((item) => item.entity_id === entityId) ?? null;
  const claim = claims.find((item) => item.short_id === claimId) ?? null;
  const minSupporters = summary.data?.min_supporters ?? 2;
  useEffect(() => {
    if (released.data) setEntityId(keepSelection(entities, entityKey, entityId));
  }, [released.data]);
  useEffect(() => {
    if (withheld.data) setClaimId(keepSelection(claims, claimKey, claimId));
  }, [withheld.data]);

  const warning = listsWarning(summary.data?.lists);
  useBanner("withheld.lists", warning ? { tone: "warning", text: warning } : null);

  const onKeep = async (shortId, keep) => {
    if (saving) return;
    setSaving(true);
    try {
      const answer = await api.post(`/claims/${encodeURIComponent(shortId)}/keep`, { keep });
      summary.setData(answer.summary);
      if (view === RELEASED_VIEW) released.reload();
      else withheld.reload();
      toast.success(keepToast(keep));
    } catch (error) {
      toast.error(error);
    } finally {
      setSaving(false);
    }
  };

  useShortcuts([
    ...VIEWS.map((option) => ({ keys: option.key, label: `Show: ${option.label.toLowerCase()}`, run: () => setView(option.value) })),
    { keys: "f", label: "Search the withheld claims", run: () => (view === WITHHELD_VIEW ? searchRef.current?.focus() : setView(WITHHELD_VIEW)) },
  ]);

  const toolbar = (
    <>
      <ViewTabs view={view} onView={setView} />
      {view === WITHHELD_VIEW && (
        <input
          ref={searchRef}
          type="search"
          dir="auto"
          className="field w-56"
          placeholder="Search text, entities… (f)"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      )}
      <span className="text-xs text-slate-500">{countsText(summary.data?.counts)}</span>
      {summary.data?.answered_at && <span className="text-xs text-slate-500">Check answered {formatDateTime(summary.data.answered_at)}</span>}
    </>
  );

  const list =
    view === RELEASED_VIEW ? (
      <PanelState
        loading={released.loading}
        error={released.error}
        hasData={Boolean(released.data)}
        onRetry={released.reload}
        empty={entities.length === 0}
        emptyText="The check released nothing (yet)."
      >
        <ItemList items={entities} getKey={entityKey} selectedKey={entityId} onSelect={setEntityId} renderItem={(item) => <EntityRow entity={item} />} />
      </PanelState>
    ) : (
      <PanelState
        loading={withheld.loading}
        error={withheld.error}
        hasData={Boolean(withheld.data)}
        onRetry={withheld.reload}
        empty={claims.length === 0}
        emptyText={query ? "No withheld claim matches the search." : "Nothing is withheld."}
      >
        <ItemList items={claims} getKey={claimKey} selectedKey={claimId} onSelect={setClaimId} renderItem={(item) => <ClaimRow claim={item} />} />
      </PanelState>
    );

  const main =
    view === RELEASED_VIEW ? (
      <EntityGroups entity={entity} minSupporters={minSupporters} saving={saving} onKeep={onKeep} />
    ) : (
      <ClaimDetail claim={claim} minSupporters={minSupporters} saving={saving} onKeep={onKeep} />
    );

  return <Layout toolId={tool.id} toolbar={toolbar} list={list} main={main} listWidth={{ initial: 360 }} />;
}

export default { id: TOOL_ID, title: "Withheld", Component: WithheldTool };
