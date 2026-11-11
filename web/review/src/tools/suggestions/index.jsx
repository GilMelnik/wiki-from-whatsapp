import { useEffect, useMemo, useRef, useState } from "react";
import {
  ItemList,
  Layout,
  Panel,
  PanelState,
  Select,
  useEvents,
  useJobs,
  usePicking,
  useResource,
  useShortcuts,
  useStoredState,
  useToast,
} from "@shell";
import { ActionsPanel } from "./ActionsPanel.jsx";
import { api, suggestionPath } from "./api.js";
import { Detail } from "./Detail.jsx";
import { DialogHost } from "./Dialogs.jsx";
import { Overview } from "./Overview.jsx";
import { OverviewRow, SuggestionRow } from "./Rows.jsx";
import {
  ACTION_LABELS,
  actionTargets,
  ALL,
  applyCost,
  DEFAULT_VIEW,
  filterItems,
  filterOptions,
  newOutcomes,
  orderItems,
  outcomeKey,
  OVERVIEW_ID,
  SUGGESTIONS_KEY,
  TOOL_ID,
} from "./suggestions.js";

// What the whole-wiki review (step 8) proposed, for the owner to decide on: the list holds
// the suggestions (open ones first, in the reviewer's order), the main column one suggestion
// with its evidence and proposal (or the overview), the side column the actions. Decisions
// and corrections are saved at once by the store; applying runs in the background on the
// server, one at a time, and its outcome arrives as an event; nothing here decides what is
// allowed.

const APPLY_EVENT = "suggestions_apply";

function useOutcomeToasts(applies) {
  const toast = useToast();
  const seen = useRef(null);
  useEffect(() => {
    if (!applies) return;
    if (seen.current === null) {
      seen.current = new Set(applies.outcomes.map(outcomeKey)); // only what arrives from now on
      return;
    }
    for (const outcome of newOutcomes(applies, seen.current)) {
      seen.current.add(outcomeKey(outcome));
      if (outcome.status === "applied") toast.success(`בוצעה: ${outcome.title}`);
      else toast.error(`${outcome.status === "failed" ? "נכשלה" : "סורבה"}: ${outcome.title}. ${outcome.message}`);
    }
  }, [applies]);
}

function SuggestionsTool({ tool }) {
  const toast = useToast();
  const [view, setView] = useStoredState(TOOL_ID, "view", DEFAULT_VIEW);
  const [selectedId, setSelectedId] = useStoredState(TOOL_ID, "selected", OVERVIEW_ID);
  const [dialog, setDialog] = useState(null);
  const [saving, setSaving] = useState(false);
  const watch = useMemo(() => [...new Set([...tool.reads, ...tool.writes, SUGGESTIONS_KEY])], [tool.reads, tool.writes]);
  const { runningByArtifact } = useJobs();
  const lockedBy = tool.writes.map((key) => runningByArtifact[key]).find(Boolean);

  const list = useResource(() => api.get("/suggestions"), [], { watch });
  const data = list.data;
  useOutcomeToasts(data?.applies);
  const items = useMemo(() => orderItems(data?.items ?? []), [data]);
  const shown = filterItems(items, view);
  const rows = [{ id: OVERVIEW_ID }, ...shown];
  const picking = usePicking(`${view.kind}:${view.status}`);
  const item = items.find((candidate) => candidate.id === selectedId) ?? null;
  const detail = useResource(() => (item ? api.get(suggestionPath(item.id)) : null), [item?.id], { watch });
  // The apply queue moved (started, finished, refused): the list and the shown item follow.
  useEvents(APPLY_EVENT, () => {
    list.reload();
    if (item) detail.reload();
  });

  useEffect(() => {
    if (data && selectedId !== OVERVIEW_ID && !item) setSelectedId(OVERVIEW_ID);
  }, [data]);

  const marked = picking.count > 0;
  const targets = marked ? items.filter((candidate) => picking.has(candidate.id)) : item ? [item] : [];
  const accepted = actionTargets(items, "apply");
  const refresh = () => {
    list.reload();
    detail.reload();
  };
  const guarded = async (send) => {
    setSaving(true);
    try {
      return await send();
    } catch (error) {
      toast.error(error);
      return null;
    } finally {
      setSaving(false);
    }
  };
  const queue = (chosen, body) => {
    const cost = applyCost(chosen);
    const send = () =>
      api.post("/applies", body).then((answer) => {
        picking.clear();
        refresh();
        const refused = Object.values(answer.refused);
        toast.info(`${answer.queued.length === 1 ? "הצעה אחת נשלחה" : `${answer.queued.length} הצעות נשלחו`} לביצוע ברקע.`);
        if (refused.length) toast.error(refused.join("\n"));
      });
    if (cost.paid > 0) setDialog({ type: "apply", cost, confirm: send });
    else guarded(send);
  };
  const act = {
    decide: (action) => {
      const ids = actionTargets(targets, action).map((target) => target.id);
      if (ids.length === 0 || lockedBy) return;
      guarded(async () => {
        const answer = await api.post("/suggestions/decide", { ids, action });
        picking.clear();
        refresh();
        toast.success(`${ACTION_LABELS[action]}: ${answer.done.length === 1 ? "הצעה אחת" : `${answer.done.length} הצעות`}`);
        const refused = Object.values(answer.refused);
        if (refused.length) toast.error(refused.join("\n"));
      });
    },
    apply: () => {
      const chosen = actionTargets(targets, "apply");
      if (chosen.length && !lockedBy) queue(chosen, { ids: chosen.map((target) => target.id) });
    },
    applyAll: () => accepted.length && !lockedBy && queue(accepted, { all: true }),
    withdraw: (id) => guarded(() => api.post("/applies/withdraw", { ids: [id] }).then(refresh)),
    edit: () => {
      if (!item || marked || lockedBy || item.busy || !["pending", "accepted"].includes(item.status)) return;
      setDialog({ type: "edit", suggestion: detail.data?.suggestion ?? item });
    },
  };
  const saveEdit = async (id, proposal) => {
    await api.post(`${suggestionPath(id)}/edit`, { proposal });
    refresh();
    toast.success("ההצעה המתוקנת נשמרה");
  };
  const changeView = (patch) => setView((previous) => ({ ...previous, ...patch }));

  useShortcuts([
    { keys: "a", label: "קבלת ההצעה (או המסומנות)", run: () => act.decide("accept") },
    { keys: "x", label: "דחיית ההצעה (או המסומנות)", run: () => act.decide("reject") },
    { keys: "o", label: "החזרה להמתנה", run: () => act.decide("reopen") },
    { keys: "e", label: "תיקון ההצעה לפני ההחלטה", run: () => act.edit() },
    { keys: "y", label: "ביצוע ההצעה שהתקבלה (או המסומנות)", run: () => act.apply() },
    { keys: "shift+y", label: "ביצוע כל ההצעות שהתקבלו", run: () => act.applyAll() },
    { keys: " ", label: "סימון ההצעה הנבחרת (רווח)", run: () => item && picking.toggle(item.id) },
  ]);

  const toolbar = data?.exists && (
    <>
      <Select label="סוג" value={view.kind} options={filterOptions(items, "kind", "kind_name", "כל הסוגים")} onChange={(kind) => changeView({ kind })} />
      <Select label="מצב" value={view.status} options={filterOptions(items, "status", "status_name", "כל המצבים")} onChange={(status) => changeView({ status })} />
      {(view.kind !== ALL || view.status !== ALL) && (
        <button type="button" className="btn btn-sm" onClick={() => setView(DEFAULT_VIEW)}>
          הצגת הכול
        </button>
      )}
    </>
  );
  const listColumn = (
    <PanelState loading={list.loading} error={list.error} hasData={Boolean(data)} onRetry={list.reload}>
      <ItemList
        items={rows}
        getKey={(row) => row.id}
        selectedKey={item ? item.id : OVERVIEW_ID}
        onSelect={setSelectedId}
        total={items.length + 1}
        renderItem={(row) =>
          row.id === OVERVIEW_ID ? (
            <OverviewRow data={data} />
          ) : (
            <SuggestionRow item={row} applies={data.applies} picked={picking.has(row.id)} onToggle={() => picking.toggle(row.id)} />
          )
        }
      />
    </PanelState>
  );
  const busy = Boolean(data?.applies?.running) || (data?.applies?.queue?.length ?? 0) > 0;
  const main = !data ? (
    <Panel>
      <PanelState loading={list.loading} error={list.error} onRetry={list.reload} />
    </Panel>
  ) : item ? (
    <Detail detail={detail} applies={data.applies} busy={busy} />
  ) : (
    <Overview data={data} onSelect={setSelectedId} onFilter={(status) => changeView({ status, kind: ALL })} />
  );
  return (
    <>
      <Layout
        toolId={TOOL_ID}
        toolbar={toolbar}
        list={listColumn}
        main={main}
        side={
          data?.exists && (
            <ActionsPanel
              item={item}
              targets={targets}
              marked={marked}
              applies={data.applies}
              accepted={accepted}
              act={act}
              lockedBy={lockedBy}
              saving={saving}
              clear={picking.clear}
            />
          )
        }
        listWidth={{ initial: 360 }}
      />
      <DialogHost dialog={dialog} close={() => setDialog(null)} saveEdit={saveEdit} />
    </>
  );
}

export default { id: TOOL_ID, title: "הצעות לשיפור הוויקי", Component: SuggestionsTool, quietStale: [SUGGESTIONS_KEY] };
