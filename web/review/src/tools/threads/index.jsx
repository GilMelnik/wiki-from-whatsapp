import { useMemo, useState } from "react";
import {
  blockRows,
  ConversationView,
  ItemList,
  Layout,
  MESSAGE_VIEWS,
  MessageViewBar,
  Panel,
  PanelState,
  selectedByThread,
  selectionRules,
  streamRows,
  TimelineView,
  useJobs,
  useMessageSelection,
  useShortcuts,
  useThreadColors,
  useToast,
} from "@shell";
import { ActionsPanel } from "./ActionsPanel.jsx";
import { MergeDialog } from "./MergeDialog.jsx";
import { ThreadRow, WindowThreadRow } from "./Rows.jsx";
import {
  CLASSIFIED_KEY,
  emptyListText,
  mergeCandidates,
  monthOf,
  moveTargets,
  restOfThread,
  THREADS_KEY,
  TOOL_ID,
  windowRows,
} from "./threads.js";
import { Toolbar } from "./Toolbar.jsx";
import { api, useThreads } from "./useThreads.js";

// Step 1's split of the chat into conversations, for the owner to check and correct. Decided
// here: does this message belong to this conversation; where does a conversation start and
// end. The list holds the conversations (all of them, searchable; or those of one gold
// window); the main column shows the messages with what the decision rests on: who replied
// to whom (and whether the quoted message sits in another conversation), the pauses, the
// conversations right before and after, and, in the timeline, where chat messages are not
// shown, with buttons to load them. The side column moves the selected messages, makes them
// a conversation of their own, merges, undoes, and sets step 2's knowledge label of the
// opened conversation (the list can be filtered by it). Every change is saved at once by the
// server: to step 1's edited file, a label to step 2's.

const CONVERSATION = MESSAGE_VIEWS.conversation;
const TIMELINE = MESSAGE_VIEWS.timeline;

function OldFormat({ summary }) {
  return (
    <Panel>
      <div className="space-y-3 p-6 text-sm">
        <p className="note note-warning">{summary.problem}</p>
        <p className="text-slate-600">Nothing in this file is changed here. After step 1 has run, the conversations show in this tool.</p>
      </div>
    </Panel>
  );
}

function ListColumn({ data, colors, rows }) {
  if (data.gold) {
    const { windowView, windowData } = data;
    return (
      <PanelState loading={windowView.loading || !windowData} error={windowView.error} hasData={Boolean(windowData)} onRetry={windowView.reload}
        empty={windowData && rows.length === 0} emptyText="No message of the chat lies in this window.">
        <ItemList items={rows} getKey={(row) => row.thread_id} selectedKey={data.selectedId} onSelect={data.setSelectedId}
          renderItem={(row) => <WindowThreadRow row={row} info={colors.get(row.thread_id)} />} />
      </PanelState>
    );
  }
  const { list, search } = data;
  return (
    <PanelState loading={list.loading} error={list.error} hasData={Boolean(list.data)} onRetry={list.reload}
      empty={list.data?.items.length === 0} emptyText={emptyListText(search)}>
      {list.data && (
        <ItemList items={list.data.items} total={list.data.total} getKey={(row) => row.thread_id} selectedKey={data.selectedId}
          onSelect={data.setSelectedId} sectionOf={search.sort === "time" ? monthOf : undefined}
          renderItem={(row) => <ThreadRow row={row} query={list.data.query} />} />
      )}
    </PanelState>
  );
}

function ThreadsTool({ tool }) {
  const data = useThreads(tool);
  const toast = useToast();
  const { runningByArtifact } = useJobs();
  // The write guard refuses every change while step 1 or step 2 rewrites a file this tool writes.
  const locked = runningByArtifact[THREADS_KEY] ?? runningByArtifact[CLASSIFIED_KEY] ?? null;
  const [busy, setBusy] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const selection = useMessageSelection(data.subject);
  const colors = useThreadColors(data.subject);
  const summary = data.summary.data;
  const timeline = data.view === TIMELINE;

  const listRows = useMemo(() => (data.gold ? windowRows(data.windowData) : []), [data.gold, data.windowData]);
  colors.assignAll(listRows.map((row) => row.thread_id)); // window conversations: A, B, C… in speaking order
  const view = data.shownData;
  const blocks = view?.blocks ?? [];
  const rows = useMemo(() => (timeline ? streamRows(blocks) : blocks.flatMap(blockRows)), [blocks, timeline]);
  const inWindow = useMemo(() => (view?.in_window_ids ? new Set(view.in_window_ids) : null), [view]);
  const rules = selectionRules(selectedByThread(rows, selection.selected), new Map(blocks.map((b) => [b.thread_id, b.size])));
  const targets = moveTargets(blocks, rules);
  const rest = rules.count === 1 ? restOfThread(blocks, rules.keys[0]) : { reason: null };
  const currentWindow = data.gold ? data.windows[data.windowIndex] : null;

  const afterEdit = async (answer, open, undone = false) => {
    toast.success(undone ? `Undone: ${answer.label}` : answer.label);
    selection.clear();
    const reopen = (open || answer.removed?.includes(data.selectedId)) && answer.focus !== data.selectedId;
    if (reopen) data.setSelectedId(answer.focus);
    else data.keepInView(answer.focus);
    await data.reloadAll(!reopen);
  };
  const edit = async (path, body, open = false) => {
    if (locked || busy) return;
    setBusy(true);
    try {
      await afterEdit(await data.saving(() => api.post(path, body)), open, path === "/undo");
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(false);
    }
  };
  const act = {
    move: (target) => edit("/move", { message_ids: rules.keys, target_id: target }),
    newThread: () => !rules.newThread && edit("/move", { message_ids: rules.keys, target_id: null }),
    splitHere: () => rest.keys && edit("/move", { message_ids: rest.keys, target_id: null }),
    openMerge: () => data.selectedId && !locked && setMergeOpen(true),
    undo: () => summary?.undo && edit("/undo", undefined, !data.gold),
    setLabel: async (value) => {
      const label = data.label.data?.label;
      if (locked || busy || !label || !data.selectedId) return;
      if ((value === null && !label.manual) || value === label.value) return;
      setBusy(true);
      try {
        const answer = await api.post(`/knowledge/${encodeURIComponent(data.selectedId)}`, { value });
        data.label.setData(answer);
        await data.reloadLabels();
        toast.success(value === null ? "Back to step 2's answer" : value ? "Marked: carries knowledge" : "Marked: carries no knowledge");
      } catch (error) {
        toast.error(error);
      } finally {
        setBusy(false);
      }
    },
  };
  const merge = async (ids) => afterEdit(await data.saving(() => api.post("/merge", { thread_ids: ids })), !data.gold);
  const toggleDone = async () => {
    if (!currentWindow || locked) return;
    try {
      await api.post(`/windows/${currentWindow.index}/done`, { done: !currentWindow.done });
      await data.summary.reload();
      toast.success(currentWindow.done ? "The window is open again" : "The window is marked done");
    } catch (error) {
      toast.error(error);
    }
  };

  useShortcuts([
    { keys: "v", label: "Switch between the conversation view and the timeline", run: () => data.setView(timeline ? CONVERSATION : TIMELINE) },
    { keys: "escape", label: "Clear the selection", run: selection.clear },
    { keys: "n", label: "New conversation from the selected messages", run: act.newThread },
    { keys: "s", label: "New conversation from the selected message to its end", run: act.splitHere },
    { keys: "m", label: "Merge the opened conversation with another", run: act.openMerge },
    { keys: "u", label: "Undo the last change", run: act.undo },
    ...targets.slice(0, 9).map((target, index) => ({
      keys: String(index + 1),
      label: index === 0 ? "Move the selection to the conversation with that number (1–9)" : undefined,
      run: () => rules.count > 0 && !target.reason && act.move(target.threadId),
    })),
    { keys: "d", label: "Mark the gold window done, or reopen it", run: toggleDone },
    { keys: "[", label: "Previous gold window", run: () => data.gold && data.selectWindow(data.windowIndex - 1) },
    { keys: "]", label: "Next gold window", run: () => data.gold && data.selectWindow(data.windowIndex + 1) },
    { keys: "/", label: "Search the messages", run: () => document.getElementById("threads-search")?.focus() },
    { keys: "y", label: "The opened conversation carries knowledge (step 2's label)", run: () => act.setLabel(true) },
    { keys: "x", label: "The opened conversation carries no knowledge", run: () => act.setLabel(false) },
    { keys: "o", label: "Back to step 2's answer for the opened conversation", run: () => act.setLabel(null) },
  ]);

  const ready = summary?.state === "ready";
  const viewProps = {
    blocks, colors, selection, currentId: data.selectedId, onSelectThread: data.setSelectedId,
    quietMinutes: summary?.quiet_minutes, inWindow, window: view?.window ?? null, highlight: data.gold ? "" : data.search.q,
  };
  const main = !summary ? (
    <Panel>
      <PanelState loading={data.summary.loading} error={data.summary.error} onRetry={data.summary.reload} />
    </Panel>
  ) : !ready ? (
    <OldFormat summary={summary} />
  ) : (
    <section className="flex min-h-0 flex-1 flex-col">
      <MessageViewBar view={data.view} onView={data.setView} count={rules.count} onClear={selection.clear} />
      <PanelState loading={data.shown.loading || (Boolean(data.shown.data) && !view)} error={data.shown.error} hasData={Boolean(view)}
        onRetry={data.shown.reload} empty={!view && !data.shown.loading} emptyText="Pick a conversation in the list.">
        {view && (timeline ? (
          <TimelineView {...viewProps} chatSize={view.chat_size} onLoad={data.loadContext} step={summary.context_step} />
        ) : (
          <ConversationView {...viewProps} />
        ))}
      </PanelState>
    </section>
  );
  return (
    <>
      <Layout
        toolId={TOOL_ID}
        toolbar={ready && <Toolbar data={data} locked={Boolean(locked)} onToggleDone={toggleDone} />}
        list={ready && <ListColumn data={data} colors={colors} rows={listRows} />}
        main={main}
        side={ready && (
          <ActionsPanel rules={rules} targets={targets} rest={rest} colors={colors} current={data.selectedId}
            undo={summary.undo} locked={locked} busy={busy} act={act} knowledge={data.knowledge.data} label={data.label.data?.label} />
        )}
        listWidth={{ initial: 340 }}
      />
      <MergeDialog open={mergeOpen} currentId={data.selectedId} candidates={mergeCandidates(blocks, data.selectedId)} colors={colors}
        onClose={() => setMergeOpen(false)} onMerge={merge} />
    </>
  );
}

export default { id: TOOL_ID, title: "Conversations", Component: ThreadsTool };
