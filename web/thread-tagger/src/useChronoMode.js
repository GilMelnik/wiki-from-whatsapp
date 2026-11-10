import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchGoldWindowThreads } from "./api";
import { chronoStream, displayedThreads } from "./messages/chronoStream.js";
import { useEscape, useKeySelection, useThreadColors, useViewMode, VIEWS } from "./messages/hooks.js";
import {
  groupByThread,
  indicesFromKeys,
  keysFromIndices,
  selectionRules,
} from "./messages/selection.js";

const NO_THREADS = [];

/**
 * Thread-tagger glue for the two message views: which threads the chronological view shows
 * (the gold window's threads, else what the thread view shows), its selection, colours, and
 * what the split / move dialogs receive in the chronological view (`dialog`; null in the
 * thread view, where they keep their own props).
 */
export function useChronoMode({
  threadData,
  gold,
  listItems,
  selectedIndices,
  setSelectedIndices,
  dialogsOpen,
  onError,
}) {
  const [view, setViewMode] = useViewMode();
  const chrono = view === VIEWS.chrono;
  const colors = useThreadColors();
  const selection = useKeySelection();
  const [windowData, setWindowData] = useState(null);
  // Threads a move went into stay in the window's stream even without in-window messages.
  const [pinned, setPinned] = useState(NO_THREADS);
  const thread = threadData?.thread;

  useEffect(() => {
    setPinned(NO_THREADS);
  }, [gold.index]);
  const pin = useCallback((id) => setPinned((prev) => (prev.includes(id) ? prev : [...prev, id])), []);

  // The window's threads with their messages, fetched again after every edit.
  useEffect(() => {
    if (!chrono || gold.index === null) return undefined;
    let live = true;
    fetchGoldWindowThreads(gold.index, pinned)
      .then((data) => live && setWindowData(data))
      .catch((e) => onError(e.message));
    return () => {
      live = false;
    };
  }, [chrono, gold.index, pinned, listItems, onError]);

  const shown = useMemo(
    () => displayedThreads(thread, threadData?.context),
    [thread, threadData?.context]
  );
  const windowReady = gold.index !== null && windowData?.index === gold.index;
  const threads = gold.index !== null ? (windowReady ? windowData.threads : NO_THREADS) : shown;

  // Window threads first, so their letters follow the window order (A = first to speak).
  colors.assignAll(gold.current?.thread_ids || []);
  colors.assignAll(shown.map((t) => t.thread_id));

  // An edit or another window starts a fresh selection, as the thread view does.
  useEffect(() => {
    selection.clear();
  }, [listItems, gold.index, selection.clear]);

  const rows = useMemo(() => chronoStream(threads), [threads]);
  const groups = useMemo(() => groupByThread(rows, selection.selected), [rows, selection.selected]);
  const rules = selectionRules(groups, new Map(threads.map((t) => [t.thread_id, t.messages.length])));
  const single = rules.threads.length === 1 ? rules.threads[0] : null;

  const clearAll = useCallback(() => {
    selection.clear();
    setSelectedIndices((prev) => (prev.size ? new Set() : prev));
  }, [selection.clear, setSelectedIndices]);
  useEscape(clearAll, !dialogsOpen);

  /** Switch views, carrying over the current thread's part of the selection. */
  const setView = useCallback(
    (next) => {
      if (next === view) return;
      const own = thread ? chronoStream([thread]) : [];
      if (next === VIEWS.chrono) {
        selection.replace(keysFromIndices(own, thread?.thread_id, selectedIndices));
        setSelectedIndices(new Set());
      } else {
        setSelectedIndices(indicesFromKeys(own, thread?.thread_id, selection.selected));
        selection.clear();
      }
      setViewMode(next);
    },
    [view, thread, selectedIndices, selection, setSelectedIndices, setViewMode]
  );

  const dialog = chrono
    ? {
        thread: single ? threads.find((t) => t.thread_id === single) : thread,
        sourceId: single || thread?.thread_id,
        indices: single ? new Set(groups.get(single).positions) : new Set(),
        splitReason: rules.split,
        moveReason: rules.moveDialog,
      }
    : null;

  return {
    view,
    setView,
    chrono,
    colors,
    selection,
    threads,
    inWindowIds: windowReady ? windowData.in_window_ids : null,
    goldSpan: windowReady ? { start: windowData.start, end: windowData.end } : null,
    count: chrono ? rules.count : selectedIndices.size,
    clearAll,
    dialog,
    pin,
  };
}
