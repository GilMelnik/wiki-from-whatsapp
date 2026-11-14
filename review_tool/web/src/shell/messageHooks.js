import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { selectRange, toggleRow } from "./messageStream.js";
import { ThreadColors } from "./threadColors.js";

// React state of the shared message views.

/**
 * Selected messages, by key, across conversations. `click(rows, index, closureOf, shift)`:
 * a click toggles a message with its reply chain, Shift-click adds the range from the last
 * click. A new `resetKey` (another conversation or window) starts with nothing selected.
 */
export function useMessageSelection(resetKey) {
  const [selected, setSelected] = useState(() => new Set());
  const anchor = useRef(null);

  const clear = useCallback(() => {
    anchor.current = null;
    setSelected((previous) => (previous.size ? new Set() : previous));
  }, []);
  useEffect(clear, [resetKey]);

  const click = useCallback((rows, rowIndex, closureOf, shiftKey) => {
    setSelected((previous) => {
      const anchorIndex = rows.findIndex((row) => row.key === anchor.current);
      anchor.current = rows[rowIndex].key;
      if (shiftKey && anchorIndex >= 0 && previous.size > 0) return selectRange(previous, rows, anchorIndex, rowIndex);
      return toggleRow(previous, rows, rowIndex, closureOf);
    });
  }, []);
  const replace = useCallback((keys) => setSelected(new Set(keys)), []);

  return useMemo(() => ({ selected, click, replace, clear }), [selected, click, replace, clear]);
}

/** Colours and letters for conversations; a new `resetKey` starts again at "A". */
export function useThreadColors(resetKey) {
  return useMemo(() => new ThreadColors(), [resetKey]);
}
