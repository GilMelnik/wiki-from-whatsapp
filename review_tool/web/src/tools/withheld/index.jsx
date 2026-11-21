import { useEffect, useRef, useState } from "react";
import { Bidi, countText, createApi, formatCount, formatDateTime, ItemList, Kbd, Layout, PanelState, useBanner, useResource, useShortcuts, useStoredState, useToast } from "@shell";
import { ClaimDetail, EntityGroups, ReleasedByYou } from "./Details.jsx";
import {
  BY_ENTITY,
  BY_YOU_KEY,
  byEntity,
  claimState,
  CONVERSATION_LINK_ID,
  countsText,
  entitySections,
  keepSelection,
  keepToast,
  listsWarning,
  ORDERS,
  piecesOnly,
  reasonCounts,
  releasedRows,
  releaseToast,
  RELEASED_VIEW,
  shownClaims,
  STATUS,
  TOOL_ID,
  VETOES_KEY,
  VIEWS,
  WHY_LABELS,
  WITHHELD_VIEW,
} from "./withheld.js";

// Withheld: the wiki publishes a negative claim about a named provider, place or public body
// only when at least two different members back it. The same-complaint check releases a lone
// one when a model found a second person making the same complaint. Here the owner looks at
// what the check released before the pages are written and keeps any of it withheld (a switch
// per claim; switching it off is the undo); goes through what stays withheld and why, and
// releases any claim by hand (one decision per claim; "Withhold again" is the undo); and sees
// their own releases in one place. The withheld claims are listed by entity (each under the most
// specific thing it names, the most talked-about entity first, with how many are withheld per
// entity and per reason), or in the order of the chat. The interface is English, the claims and
// names are Hebrew; no person is ever shown.

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
  if (entity.entity_id === BY_YOU_KEY) {
    return (
      <span className="flex flex-col gap-0.5">
        <span className="font-medium text-slate-900">Released by you</span>
        <span className="text-xs text-slate-500">{countText(entity.by_you.length, "claim")} released by hand</span>
      </span>
    );
  }
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
        <Bidi>{claim.entity_names.join(", ")}</Bidi> ·{" "}
        {claim.withheld ? (
          (WHY_LABELS[claim.why] ?? claim.why)
        ) : (
          <span className={`badge ${STATUS[claimState(claim)].badge}`}>{STATUS[claimState(claim)].label}</span>
        )}
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
  const [order, setOrder] = useStoredState(TOOL_ID, "order", BY_ENTITY);
  // Only the claims that are one piece of a message the wiki tells in part.
  const [pieces, setPieces] = useStoredState(TOOL_ID, "pieces", false);
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  // The claims released (or unmarked) in the "Still withheld" view since it was loaded: they
  // keep their place in its list, marked, until the view is opened again.
  const [releasedHere, setReleasedHere] = useState(() => new Set());
  useEffect(() => setReleasedHere(new Set()), [view]);
  const watch = [...tool.reads, VETOES_KEY];

  const summary = useResource(() => api.get("/summary"), [], { watch });
  const released = useResource(() => (view === RELEASED_VIEW ? api.get("/released") : null), [view], { watch });
  const withheld = useResource(() => (view === WITHHELD_VIEW ? api.get("/withheld", { q: query }) : null), [view, query], {
    watch,
    keepPrevious: true,
  });

  const entities = released.data?.entities ?? [];
  const rows = releasedRows(entities, released.data?.by_you ?? []);
  const all = shownClaims(withheld.data?.items ?? [], releasedHere);
  const shown = pieces ? piecesOnly(all) : all;
  const claims = order === BY_ENTITY ? byEntity(shown) : shown;
  const pieceCount = piecesOnly(all).filter((item) => item.withheld).length;
  const entity = entities.find((item) => item.entity_id === entityId) ?? null;
  const claim = claims.find((item) => item.short_id === claimId) ?? null;
  const minSupporters = summary.data?.min_supporters ?? 2;
  useEffect(() => {
    if (released.data) setEntityId(keepSelection(rows, entityKey, entityId));
  }, [released.data]);
  useEffect(() => {
    if (withheld.data) setClaimId(keepSelection(claims, claimKey, claimId));
  }, [withheld.data]);

  const warning = listsWarning(summary.data?.lists);
  useBanner("withheld.lists", warning ? { tone: "warning", text: warning } : null);

  // One mark of one claim ("keep" or "release"), stored at once; the answer is the new summary.
  const mark = async (shortId, route, body, done) => {
    if (saving) return;
    setSaving(true);
    try {
      const answer = await api.post(`/claims/${encodeURIComponent(shortId)}/${route}`, body);
      summary.setData(answer.summary);
      if (view === RELEASED_VIEW) released.reload();
      else {
        setReleasedHere((before) => new Set(before).add(shortId));
        withheld.reload();
      }
      toast.success(done);
    } catch (error) {
      toast.error(error);
    } finally {
      setSaving(false);
    }
  };
  const onKeep = (shortId, keep) => mark(shortId, "keep", { keep }, keepToast(keep));
  const onRelease = (shortId, release) => mark(shortId, "release", { release }, releaseToast(release));

  useShortcuts([
    ...VIEWS.map((option) => ({ keys: option.key, label: `Show: ${option.label.toLowerCase()}`, run: () => setView(option.value) })),
    { keys: "f", label: "Search the withheld claims", run: () => (view === WITHHELD_VIEW ? searchRef.current?.focus() : setView(WITHHELD_VIEW)) },
  ]);
  useShortcuts(
    [
      { keys: " ", label: "Release the selected claim, or withhold it again (Space)", run: () => claim && onRelease(claim.short_id, claim.withheld) },
      { keys: "c", label: "Open the selected claim's conversation (new tab)", run: () => claim && document.getElementById(CONVERSATION_LINK_ID)?.click() },
    ],
    { enabled: view === WITHHELD_VIEW }
  );

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
      >
        <ItemList items={rows} getKey={entityKey} selectedKey={entityId} onSelect={setEntityId} renderItem={(item) => <EntityRow entity={item} />} />
      </PanelState>
    ) : (
      <PanelState
        loading={withheld.loading}
        error={withheld.error}
        hasData={Boolean(withheld.data)}
        onRetry={withheld.reload}
        empty={claims.length === 0}
        emptyText={query ? "No withheld claim matches the search." : pieces ? "No withheld claim is a piece of a published message." : "Nothing is withheld."}
      >
        <ItemList
          items={claims}
          getKey={claimKey}
          selectedKey={claimId}
          onSelect={setClaimId}
          renderItem={(item) => <ClaimRow claim={item} />}
          sectionOf={order === BY_ENTITY ? entitySections(claims) : undefined}
          header={
            <span className="flex flex-wrap items-center justify-end gap-2">
              <span>{reasonCounts(claims)}</span>
              <label className="flex items-center gap-1" title="Claims that share a message with a published claim: the wiki tells the rest of that message without them">
                <input type="checkbox" checked={pieces} onChange={(event) => setPieces(event.target.checked)} />
                Pieces of a published message ({formatCount(pieceCount)})
              </label>
              <select className="field" aria-label="Order of the list" value={order} onChange={(event) => setOrder(event.target.value)}>
                {ORDERS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </span>
          }
        />
      </PanelState>
    );

  const main =
    view === WITHHELD_VIEW ? (
      <ClaimDetail claim={claim} minSupporters={minSupporters} saving={saving} onKeep={onKeep} onRelease={onRelease} releaseKey="Space" />
    ) : entityId === BY_YOU_KEY ? (
      <ReleasedByYou claims={released.data?.by_you ?? []} saving={saving} onRelease={onRelease} />
    ) : (
      <EntityGroups entity={entity} minSupporters={minSupporters} saving={saving} onKeep={onKeep} />
    );

  return <Layout toolId={tool.id} toolbar={toolbar} list={list} main={main} listWidth={{ initial: 360 }} />;
}

export default { id: TOOL_ID, title: "Withheld", Component: WithheldTool };
