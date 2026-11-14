import { useCallback, useEffect, useRef, useState } from "react";
import { useArtifactChange } from "./events.js";
import { useLatest } from "./latest.js";

const NOT_LOADED = { data: undefined, error: null, loading: true };
const NO_KEYS = [];

/**
 * Fetch server data for a panel, without the stale-response race.
 *
 *   const detail = useResource(
 *     () => (selectedId ? api.get(`/claims/${encodeURIComponent(selectedId)}`) : null),
 *     [selectedId],
 *     { watch: tool.reads }
 *   );
 *   // detail = { data, error, loading, reload, setData }
 *
 * - Runs `fetcher` when `deps` change. Only the newest call may set the result, so a slow
 *   response for a previous selection never overwrites the current one.
 * - `data` is `undefined` until the first result. When `deps` change it is cleared (pass
 *   `keepPrevious: true` to keep showing the old value while loading, e.g. a filtered list).
 * - `watch`: artifact keys; when one changes on disk (or events may have been missed) the
 *   data is refetched quietly: `loading` stays false and the old value stays visible.
 * - `reload()` refetches quietly; `setData(value)` stores a value the tool already has
 *   (for example the response of a save).
 */
export function useResource(fetcher, deps, { watch = NO_KEYS, keepPrevious = false } = {}) {
  const [state, setState] = useState(NOT_LOADED);
  const begin = useLatest();
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(
    async (quiet) => {
      const isCurrent = begin();
      if (!quiet) {
        setState((previous) => ({
          data: keepPrevious ? previous.data : undefined,
          error: null,
          loading: true,
        }));
      }
      try {
        const data = await fetcherRef.current();
        if (isCurrent()) setState({ data, error: null, loading: false });
      } catch (error) {
        if (isCurrent()) {
          setState((previous) => ({
            data: quiet ? previous.data : undefined,
            error,
            loading: false,
          }));
        }
      }
    },
    [begin, keepPrevious]
  );

  useEffect(() => {
    load(false);
  }, [load, ...deps]);

  const reload = useCallback(() => load(true), [load]);
  useArtifactChange(watch, reload);

  const setData = useCallback(
    (data) => {
      begin(); // a response still in flight is older than this value
      setState({ data, error: null, loading: false });
    },
    [begin]
  );

  return { ...state, reload, setData };
}
