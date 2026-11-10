/** React state for the message views: view mode, cross-thread selection, Escape, colours. */
import { useCallback, useEffect, useRef, useState } from "react";
import { selectRange, toggleRow } from "./selection.js";
import { ThreadColors } from "./threadColors.js";

const VIEW_KEY = "message_view_mode";
export const VIEWS = { thread: "thread", chrono: "chrono" };

function readView() {
  try {
    return localStorage.getItem(VIEW_KEY) === VIEWS.chrono ? VIEWS.chrono : VIEWS.thread;
  } catch {
    return VIEWS.thread;
  }
}

/** "thread" or "chrono", remembered in localStorage across reloads. */
export function useViewMode() {
  const [view, setViewState] = useState(readView);
  const setView = useCallback((next) => {
    setViewState(next);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {
      // storage unavailable: the choice lasts until reload
    }
  }, []);
  return [view, setView];
}

/** A set of message keys with click / shift-click handling over a row list. */
export function useKeySelection() {
  const [selected, setSelected] = useState(() => new Set());
  const anchor = useRef(null);

  const click = useCallback((rows, rowIndex, closureOf, shiftKey) => {
    setSelected((prev) => {
      const anchorIndex = rows.findIndex((row) => row.key === anchor.current);
      anchor.current = rows[rowIndex].key;
      if (shiftKey && anchorIndex >= 0 && prev.size > 0) {
        return selectRange(prev, rows, anchorIndex, rowIndex);
      }
      return toggleRow(prev, rows, rowIndex, closureOf);
    });
  }, []);

  const replace = useCallback((keys) => setSelected(new Set(keys)), []);
  const clear = useCallback(() => {
    anchor.current = null;
    setSelected((prev) => (prev.size ? new Set() : prev));
  }, []);

  return { selected, click, replace, clear };
}

/** Call `handler` on Escape while `enabled` (e.g. no dialog open). */
export function useEscape(handler, enabled) {
  useEffect(() => {
    if (!enabled) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") handler();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handler, enabled]);
}

/** One `ThreadColors` for the lifetime of the page. */
export function useThreadColors() {
  const ref = useRef(null);
  if (!ref.current) ref.current = new ThreadColors();
  return ref.current;
}
