import { useCallback, useEffect, useState } from "react";
import { fetchGoldWindows, setGoldWindowDone } from "./api";

/** Gold-window mode: the configured windows, the selected one and its "done" flag.
 *
 * The selection is the window's start time (not its index). It is mirrored into
 * `?window=<index>` so a link or a reload opens the same window, and into localStorage
 * so the tagger reopens the last window when the URL names none. */

const STORAGE_KEY = "thread_tagger_gold_window";

export function formatMinute(iso) {
  if (!iso) return "";
  return iso.replace("T", " ").slice(0, 16);
}

export function windowLabel(span) {
  const start = formatMinute(span.start);
  const end = formatMinute(span.end);
  const sameDay = start.slice(0, 10) === end.slice(0, 10);
  return `${start}–${sameDay ? end.slice(11) : end}`;
}

function urlIndex() {
  const raw = new URLSearchParams(window.location.search).get("window");
  const index = raw === null || raw === "" ? NaN : Number(raw);
  return Number.isInteger(index) ? index : null;
}

function readStored() {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function persist(index, start) {
  const url = new URL(window.location.href);
  if (index >= 0) url.searchParams.set("window", String(index));
  else url.searchParams.delete("window");
  window.history.replaceState(null, "", url);
  try {
    if (start) localStorage.setItem(STORAGE_KEY, start);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // storage unavailable: the URL still carries the selection
  }
}

/** First load: `?window=<index>` wins, then the last window this browser had open. */
function initialStart(windows) {
  const fromUrl = urlIndex();
  if (fromUrl !== null) return windows[fromUrl]?.start ?? null;
  const stored = readStored();
  return windows.some((w) => w.start === stored) ? stored : null;
}

export function useGoldWindows(onError) {
  const [windows, setWindows] = useState([]);
  const [selectedStart, setSelectedStart] = useState(null);
  // False until the windows and the initial selection are known (also after an error).
  const [ready, setReady] = useState(false);

  useEffect(() => {
    fetchGoldWindows()
      .then((list) => {
        setWindows(list);
        setSelectedStart(initialStart(list));
      })
      .catch((e) => onError(e.message))
      .finally(() => setReady(true));
  }, [onError]);

  const refresh = useCallback(
    () => fetchGoldWindows().then(setWindows).catch((e) => onError(e.message)),
    [onError]
  );

  const index = windows.findIndex((w) => w.start === selectedStart);
  const current = index >= 0 ? windows[index] : null;
  const enabled = windows.length > 0;

  useEffect(() => {
    if (ready && enabled) persist(index, current?.start);
  }, [ready, enabled, index, current?.start]);

  const select = useCallback(
    (nextIndex) => setSelectedStart(windows[nextIndex]?.start ?? null),
    [windows]
  );

  const toggleDone = useCallback(async () => {
    if (!current) return;
    try {
      await setGoldWindowDone(index, !current.done);
      await refresh();
    } catch (e) {
      onError(e.message);
    }
  }, [current, index, refresh, onError]);

  return {
    ready,
    enabled,
    windows,
    index: index >= 0 ? index : null,
    current,
    select,
    refresh,
    toggleDone,
  };
}
