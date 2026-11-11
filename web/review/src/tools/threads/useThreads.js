import { useEffect, useRef, useState } from "react";
import { createApi, MESSAGE_VIEWS, useResource, useStoredState } from "@shell";
import { addRange, DEFAULT_SEARCH, extraParams, MODES, TOOL_ID } from "./threads.js";

// The conversations tool's server data and what the page remembers: the mode (all
// conversations / gold windows), the window, the view, the search and the opened
// conversation; and per subject (a conversation, or a window) the extras loaded on request:
// whole conversations to keep in view and stretches of the chat shown as context.

export const api = createApi(TOOL_ID);
const NO_EXTRA = { include: [], context: [] };

export function useThreads(tool) {
  const watch = tool.reads;
  const [mode, setMode] = useStoredState(TOOL_ID, "mode", MODES.all);
  const [windowStart, setWindowStart] = useStoredState(TOOL_ID, "window", null);
  const [view, setView] = useStoredState(TOOL_ID, "view", MESSAGE_VIEWS.conversation);
  const [search, setSearch] = useStoredState(TOOL_ID, "search", DEFAULT_SEARCH);
  const [selectedId, setSelectedId] = useStoredState(TOOL_ID, "selected", null);

  const summary = useResource(() => api.get("/summary"), [], { watch });
  const ready = summary.data?.state === "ready";
  const windows = summary.data?.windows ?? [];
  const gold = mode === MODES.gold && windows.length > 0;
  const found = windows.findIndex((window) => window.start === windowStart);
  const windowIndex = gold ? Math.max(found, 0) : null;
  const subject = gold ? `window:${windows[windowIndex].start}` : `thread:${selectedId}`;

  const [extras, setExtras] = useState({ subject, ...NO_EXTRA });
  const extra = extras.subject === subject ? extras : { subject, ...NO_EXTRA };
  const changeExtra = (patch) => setExtras((previous) => {
    const base = previous.subject === subject ? previous : { subject, ...NO_EXTRA };
    return { ...base, ...patch(base) };
  });
  const params = extraParams(extra);
  const paramsKey = JSON.stringify(params);

  const list = useResource(() => (ready && !gold ? api.get("/threads", search) : null), [ready, gold, search.q, search.sort], {
    watch,
    keepPrevious: true,
  });
  const windowView = useResource(() => (ready && gold ? api.get("/view", { window: windowIndex, ...params }) : null), [
    ready, gold, windowIndex, paramsKey,
  ], { watch, keepPrevious: true });
  const wantThread = ready && Boolean(selectedId) && (!gold || view === MESSAGE_VIEWS.conversation);
  const threadParams = gold ? { include: params.include, window: windowIndex } : params;
  // While an edit is being saved the thread view is not fetched again: the file's change
  // event can come before the answer, and the open conversation may just have been merged away.
  const editing = useRef(false);
  const held = useRef(undefined);
  const threadView = useResource(
    () => (!wantThread ? null : editing.current ? held.current : api.get("/view", { thread: selectedId, ...threadParams })),
    [wantThread, selectedId, JSON.stringify(threadParams)],
    { watch, keepPrevious: true }
  );
  held.current = threadView.data;

  // A view answer is used only for what is asked now (a previous one stays while loading).
  const windowData = windowView.data?.subject.index === windowIndex ? windowView.data : undefined;
  const threadData = threadView.data?.subject.thread_id === selectedId ? threadView.data : undefined;

  // Something is always open: the first conversation of the list, or of a window just
  // chosen (unless the open one is in it). Opening another conversation later is free.
  const firstId = gold
    ? windowData?.blocks.find((block) => block.role === "window")?.thread_id
    : list.data?.items[0]?.thread_id;
  const placedFor = useRef(null);
  useEffect(() => {
    const place = gold ? subject : MODES.all;
    if (!firstId || (placedFor.current === place && selectedId)) return;
    placedFor.current = place;
    const inWindow = windowData?.blocks.some((block) => block.thread_id === selectedId);
    if (gold ? !inWindow : !selectedId) setSelectedId(firstId);
  }, [firstId, subject]);

  // The open conversation is gone (merged away, or step 1 ran): open the first one instead.
  useEffect(() => {
    if (threadView.error?.status === 404 && firstId && firstId !== selectedId) setSelectedId(firstId);
  }, [threadView.error]);

  const timeline = view === MESSAGE_VIEWS.timeline;
  const shown = timeline && gold ? windowView : threadView;
  const shownData = timeline && gold ? windowData : threadData;

  return {
    summary, list, windowView, threadView, shown, windowData, shownData,
    mode, setMode, view, setView, search, setSearch, selectedId, setSelectedId,
    windows, gold, windowIndex, subject,
    selectWindow: (index) => windows[index] && setWindowStart(windows[index].start),
    loadContext: (range) => range && changeExtra((base) => ({ context: addRange(base.context, range) })),
    keepInView: (threadId) => changeExtra((base) => ({ include: base.include.includes(threadId) ? base.include : [...base.include, threadId] })),
    saving: async (send) => {
      editing.current = true;
      try {
        return await send();
      } finally {
        editing.current = false;
      }
    },
    // After an edit; the thread view only when the same conversation stays open (a new one loads by itself).
    reloadAll: (sameThread) => Promise.all([summary.reload(), list.reload(), windowView.reload(), sameThread && threadView.reload()]),
  };
}
